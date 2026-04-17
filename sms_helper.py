import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SMSFetcherConfig:
    endpoint_url: str
    token: str
    timeout_seconds: int = 60
    tls_verify: bool = True


class SMSCodeFetcher:
    """
    验证码自动获取器（短信/邮件均可）

    适配常见响应形态（示例）：
      {
        "code": 200,
        "message": "ok",
        "result": {
          "list": [
            { "mailBox": "a@b.com", "subject": "... verification code is R9WB6X", "createTime": 1710000000 }
          ]
        }
      }
    """

    _CODE_RE = re.compile(r"(?:verification\s+code\s+is|验证码[：:]?)\s*([A-Za-z0-9]{6})\b", re.I)

    def __init__(self, cfg: SMSFetcherConfig):
        self.cfg = cfg
        self._endpoint_url = (cfg.endpoint_url or "").strip()
        self._session = requests.Session()
        self._session.headers.update(
            {
                # 按要求使用小写 key：authorization
                "authorization": f"Bearer {cfg.token}",
                "Content-Type": "application/json",
            }
        )
        self._session.verify = bool(cfg.tls_verify)
        self._last_successful_code: Optional[str] = None
        self._last_code_timestamp: float = 0.0  # 记录上次成功获取验证码的时间戳
        
        # 2925邮箱登录信息
        self._2925_login_url = "https://2925.com/login/"
        self._2925_username = "chentuanhui1"
        self._2925_password = "2955230303@cth"
        
        # 标记是否已尝试过登录，避免重复打开浏览器
        self._login_attempted = False
        
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    def fetch_latest_code(self, mailbox: str, trace_id: str) -> Optional[str]:
        """
        调用接口获取最新邮件/短信中的 6 位混合验证码（字母+数字）
        """
        params = {
            "Folder": "Inbox",
            "MailBox": mailbox,
            "FilterType": "0",
            "PageIndex": "1",
            "PageCount": "1",
            "traceId": trace_id,
        }

        try:
            print(
                f"[SMS] request start: url={self._endpoint_url}, mailbox={mailbox}, trace_id={trace_id}, params={params}"
            )
            resp = self._session.get(self._endpoint_url, params=params, timeout=10)
            print(f"[SMS] response status: {resp.status_code}", flush=True)
            
            # 关键：在 raise_for_status() 之前检查401
            print(f"[DEBUG] 检查状态码是否为401: {resp.status_code == 401}", flush=True)
            if resp.status_code == 401:
                # 检查是否已经尝试过登录，避免无限循环
                if not self._login_attempted:
                    self._login_attempted = True
                    print("[SMS] ====== 检测到401，开始自动登录 ======")
                    if self._auto_login_2925():
                        # 登录成功后重新请求
                        print("[SMS] 登录成功，重置login_attempted标记")
                        self._login_attempted = False  # 重置标记
                        print("[SMS] 重试请求...")
                        resp = self._session.get(self._endpoint_url, params=params, timeout=10)
                        print(f"[SMS] 重试响应状态: {resp.status_code}")
                        
                        # 如果重试还是401，说明登录失败
                        if resp.status_code == 401:
                            print("[SMS] 重试仍然是401，登录可能失败")
                            return None
                    else:
                        print("[SMS] 自动登录失败，无法继续")
                        return None
                else:
                    print("[SMS] 已经尝试过登录，仍然401，放弃")
                    return None
            
            # 现在才检查其他HTTP错误
            resp.raise_for_status()
            data: Any = resp.json()
        except Exception as e:
            print(f"[SMS] request failed: {type(e).__name__}: {e}")
            logger.warning("验证码接口请求失败：%s: %s", type(e).__name__, e)
            return None

        if not isinstance(data, dict):
            print(f"[SMS] invalid json object: {type(data).__name__}")
            logger.warning("验证码接口返回非 JSON 对象：%r", data)
            return None

        print(f"[SMS] response code={data.get('code')} message={data.get('message')}")
        if data.get("code") != 200:
            logger.warning("验证码接口返回异常：code=%r message=%r", data.get("code"), data.get("message"))
            return None

        result = data.get("result") or {}
        mail_list = result.get("list") or []
        print(f"[SMS] message list count={len(mail_list) if isinstance(mail_list, list) else 0}")
        if not isinstance(mail_list, list) or not mail_list:
            return None

        def _ctime(x: Any) -> float:
            if not isinstance(x, dict):
                return 0.0
            v = x.get("createTime", 0)
            try:
                return float(v)
            except Exception:
                return 0.0

        def _to_ms(v: Any) -> int:
            try:
                n = int(float(v))
            except Exception:
                return 0
            # 兼容秒级/毫秒级时间戳
            return n if n > 10_000_000_000 else n * 1000

        mail_list_sorted = sorted(mail_list, key=_ctime, reverse=True)

        for mail in mail_list_sorted:
            if not isinstance(mail, dict):
                continue
            if mailbox and mail.get("mailBox") and str(mail.get("mailBox")) != mailbox:
                continue
            subject = str(mail.get("subject") or "")
            if not subject:
                continue
            m = self._CODE_RE.search(subject)
            if not m:
                continue
            
            # 检查时间戳以确保获取最新的验证码
            create_time = _to_ms(mail.get("createTime", 0))
            modify_time = _to_ms(mail.get("modifyDate", 0))
            latest_time = max(create_time, modify_time)
            
            code = m.group(1).strip().upper()
            # 如果是更新的验证码或不同的验证码，则返回
            if latest_time > self._last_code_timestamp or code != self._last_successful_code:
                print(f"[SMS] code matched from subject for mailbox={mailbox}, timestamp={latest_time}")
                self._last_successful_code = code
                self._last_code_timestamp = latest_time
                return code

        print("[SMS] no code matched in latest messages")
        return None

    def wait_for_code(
        self,
        mailbox: str,
        trace_id: str,
        *,
        interval_seconds: int = 3,
        baseline_ms: Optional[int] = None,
    ) -> Optional[str]:
        """
        轮询等待验证码到达。超时由 cfg.timeout_seconds 控制。
        
        Args:
            mailbox: 邮箱地址
            trace_id: 追踪ID
            interval_seconds: 轮询间隔（秒）
            baseline_ms: 时间基线（毫秒），用于过滤过旧的验证码
                        如果不提供，会自动计算（当前时间 - 60秒）
                        这样可以避免使用太旧的验证码，同时不会错过刚收到的验证码
        """
        def _to_ms(v: Any) -> int:
            try:
                n = int(float(v))
            except Exception:
                return 0
            # 兼容秒级/毫秒级时间戳
            return n if n > 10_000_000_000 else n * 1000

        timeout = max(1, int(self.cfg.timeout_seconds))
        interval = max(1, int(interval_seconds))
        start = time.time()
        
        # 如果没有提供baseline_ms，使用当前时间减去60秒作为基线
        # 这样可以接受最近1分钟内的验证码，避免使用太旧的
        if baseline_ms is None:
            baseline_ms = int(time.time() * 1000) - 60000  # 60秒前
            print(f"[SMS] polling start: timeout={timeout}s interval={interval}s mailbox={mailbox}")
            print(f"[SMS] using auto baseline (current - 60s): {baseline_ms}")
        else:
            # 如果提供了baseline，再往前推60秒，确保不会错过验证码
            adjusted_baseline = baseline_ms - 60000
            print(f"[SMS] polling start: timeout={timeout}s interval={interval}s mailbox={mailbox}")
            print(f"[SMS] using provided baseline: {baseline_ms}, adjusted to: {adjusted_baseline} (baseline - 60s)")
            baseline_ms = adjusted_baseline

        while time.time() - start < timeout:
            params = {
                "Folder": "Inbox",
                "MailBox": mailbox,
                "FilterType": "0",
                "PageIndex": "1",
                "PageCount": "1",
                "traceId": trace_id,
            }
            try:
                print(
                    f"[SMS] request start: url={self._endpoint_url}, mailbox={mailbox}, trace_id={trace_id}, params={params}"
                )
                resp = self._session.get(self._endpoint_url, params=params, timeout=10)
                print(f"[SMS] response status: {resp.status_code}", flush=True)
                
                # 关键：在 raise_for_status() 之前检查401
                print(f"[DEBUG] 检查状态码是否为401: {resp.status_code == 401}", flush=True)
                if resp.status_code == 401:
                    # 检查是否已经尝试过登录，避免无限循环
                    if not self._login_attempted:
                        self._login_attempted = True
                        print("[SMS] ====== 检测到401，开始自动登录 ======")
                        if self._auto_login_2925():
                            # 登录成功后重新请求
                            print("[SMS] 登录成功，重置login_attempted标记")
                            self._login_attempted = False  # 重置标记
                            print("[SMS] 重试请求...")
                            resp = self._session.get(self._endpoint_url, params=params, timeout=10)
                            print(f"[SMS] 重试响应状态: {resp.status_code}", flush=True)
                            
                            # 如果重试还是401，说明登录失败
                            if resp.status_code == 401:
                                print("[SMS] 重试仍然是401，登录可能失败")
                                self._login_attempted = False  # 重置标记，允许下次重试
                                time.sleep(interval)
                                continue
                        else:
                            print("[SMS] 自动登录失败，无法继续")
                            self._login_attempted = False  # 重置标记，允许下次重试
                            time.sleep(interval)
                            continue
                    else:
                        print("[SMS] 已经尝试过登录，仍然401，放弃")
                        self._login_attempted = False  # 重置标记，允许下次重试
                        time.sleep(interval)
                        continue
                
                # 现在才检查其他HTTP错误
                resp.raise_for_status()
                data: Any = resp.json()
            except Exception as e:
                print(f"[SMS] request failed: {type(e).__name__}: {e}")
                logger.warning("验证码接口请求失败：%s: %s", type(e).__name__, e)
                time.sleep(interval)
                continue

            if not isinstance(data, dict):
                print(f"[SMS] invalid json object: {type(data).__name__}")
                time.sleep(interval)
                continue

            print(f"[SMS] response code={data.get('code')} message={data.get('message')}")
            if data.get("code") != 200:
                logger.warning("验证码接口返回异常：code=%r message=%r", data.get("code"), data.get("message"))
                time.sleep(interval)
                continue

            result = data.get("result") or {}
            mail_list = result.get("list") or []
            print(f"[SMS] message list count={len(mail_list) if isinstance(mail_list, list) else 0}")
            if not isinstance(mail_list, list) or not mail_list:
                time.sleep(interval)
                continue

            mail_list_sorted = sorted(
                [m for m in mail_list if isinstance(m, dict)],
                key=lambda x: max(_to_ms(x.get("createTime", 0)), _to_ms(x.get("modifyDate", 0))),
                reverse=True,
            )
            unmatched_debug = 0
            for mail in mail_list_sorted:
                msg_id = str(mail.get("messageId") or "")
                subject = str(mail.get("subject") or "")
                if not subject:
                    if unmatched_debug < 3:
                        print("[SMS] skip empty subject")
                        unmatched_debug += 1
                    continue

                m = self._CODE_RE.search(subject)
                if not m:
                    if unmatched_debug < 3:
                        print(f"[SMS] skip subject no code pattern: subject={subject[:120]}")
                        unmatched_debug += 1
                    continue

                code = m.group(1).strip().upper()
                
                # 检查验证码时间戳
                create_time = _to_ms(mail.get("createTime", 0))
                modify_time = _to_ms(mail.get("modifyDate", 0))
                latest_time = max(create_time, modify_time)
                
                # 关键：只接受比 baseline_ms 更新的验证码
                if latest_time <= baseline_ms:
                    if unmatched_debug < 3:
                        print(f"[SMS] skip old code (before baseline): code={code}, timestamp={latest_time}, baseline={baseline_ms}")
                        unmatched_debug += 1
                    continue
                
                # 检查是否与上次成功的验证码重复
                if code == self._last_successful_code and latest_time <= self._last_code_timestamp:
                    if unmatched_debug < 3:
                        print(f"[SMS] skip repeated code with old timestamp: {code} messageId={msg_id}, timestamp={latest_time}")
                        unmatched_debug += 1
                    continue
                
                # 通过所有检查，接受这个验证码
                print(f"[SMS] polling success: code={code}, messageId={msg_id}, timestamp={latest_time}, baseline={baseline_ms}")
                self._last_successful_code = code
                self._last_code_timestamp = latest_time
                return code

            print("[SMS] no new code matched in this polling round")
            time.sleep(interval)

        print("[SMS] polling timeout without code")
        return None
    
    def _auto_login_2925(self) -> bool:
        """
        通过 API 直接登录 2925 邮箱获取新的 token
        比浏览器登录更可靠，可以直接获取 API token
        """
        print(f"[SMS] ====== 开始自动登录流程 ======")
        print(f"[SMS] 步骤1: 尝试 API 登录...")
        
        try:
            # 2925邮箱的登录 API 端点
            login_url = "https://maillogin.2980.com/oauth/login"
            
            login_data = {
                "grant_type": "Password",
                "client_id": "B9257F7F9B1EF15CE",
                "username": self._2925_username,
                "password": self._2925_password,
            }
            
            print(f"[SMS] 发送登录请求到: {login_url}")
            print(f"[SMS] 用户名: {self._2925_username}")
            login_resp = requests.post(
                login_url,
                json=login_data,
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            
            print(f"[SMS] 登录响应状态: {login_resp.status_code}")
            
            if login_resp.status_code == 200:
                login_result = login_resp.json()
                print(f"[SMS] 登录响应: code={login_result.get('code')}")
                
                # 提取 token
                if login_result.get("code") == 200 and "result" in login_result:
                    result = login_result["result"]
                    # token 可能在 result.accessToken 或 result.token 中
                    new_token = result.get("accessToken") or result.get("token") or result.get("access_token")
                    
                    if new_token:
                        print(f"[SMS] ✅ 成功获取新 token (长度: {len(new_token)})")
                        # 更新 session 中的 token
                        self._session.headers.update({"authorization": f"Bearer {new_token}"})
                        print(f"[SMS] ✅ Token 已更新到 session")
                        print(f"[SMS] ====== 自动登录成功 ======")
                        return True
                    else:
                        print(f"[SMS] ❌ 响应中没有找到 token: {result}")
                else:
                    print(f"[SMS] ❌ 登录失败: {login_result}")
            else:
                print(f"[SMS] ❌ 登录请求失败，状态码: {login_resp.status_code}")
                print(f"[SMS] 响应内容: {login_resp.text[:500]}")
            
            # API 登录失败，回退到浏览器登录
            print(f"[SMS] API 登录失败，切换到浏览器登录...")
            print(f"[SMS] 步骤2: 启动浏览器登录...")
            return self._auto_login_2925_browser()
            
        except requests.exceptions.RequestException as e:
            print(f"[SMS] ❌ API 登录网络异常: {type(e).__name__}: {e}")
            print(f"[SMS] 切换到浏览器登录...")
            print(f"[SMS] 步骤2: 启动浏览器登录...")
            return self._auto_login_2925_browser()
        except Exception as e:
            print(f"[SMS] ❌ API 登录异常: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            print(f"[SMS] 切换到浏览器登录...")
            print(f"[SMS] 步骤2: 启动浏览器登录...")
            return self._auto_login_2925_browser()
    
    def _auto_login_2925_browser(self) -> bool:
        """
        通过浏览器登录2925邮箱（备用方案）
        使用子进程执行，避免asyncio冲突
        """
        print(f"[SMS] ====== 开始浏览器登录流程 ======")
        print(f"[SMS] 打开登录页面: {self._2925_login_url}")
        
        try:
            import subprocess
            import os
            
            # 获取当前脚本所在目录
            script_dir = os.path.dirname(os.path.abspath(__file__))
            helper_script = os.path.join(script_dir, "browser_login_helper.py")
            
            if not os.path.exists(helper_script):
                print(f"[SMS] ❌ 找不到浏览器登录脚本: {helper_script}")
                return False
            
            # 使用subprocess调用独立的浏览器登录脚本
            print(f"[SMS] 启动子进程执行浏览器登录...")
            result = subprocess.run(
                [
                    sys.executable,  # 使用当前Python解释器
                    helper_script,
                    self._2925_username,
                    self._2925_password,
                    self._2925_login_url
                ],
                capture_output=True,
                text=True,
                timeout=120  # 2分钟超时
            )
            
            # 打印子进程的输出
            if result.stdout:
                for line in result.stdout.split('\n'):
                    if line.strip():
                        print(f"[Browser] {line}")
            
            if result.stderr:
                print(f"[SMS] ⚠️  子进程错误输出:")
                for line in result.stderr.split('\n'):
                    if line.strip():
                        print(f"  {line}")
            
            if result.returncode != 0:
                print(f"[SMS] ❌ 浏览器登录脚本执行失败，返回码: {result.returncode}")
                return False
            
            # 解析JSON结果（最后一行）
            try:
                lines = result.stdout.strip().split('\n')
                json_line = lines[-1]  # 最后一行是JSON
                login_result = json.loads(json_line)
                
                if login_result.get("success"):
                    print("[SMS] ✅ 浏览器登录成功")
                    
                    # 更新token
                    token = login_result.get("token")
                    if token:
                        print(f"[SMS] ✅ 提取到 token (长度: {len(token)})")
                        self._session.headers.update({"authorization": f"Bearer {token}"})
                        print("[SMS] ✅ Token 已更新到 session")
                    else:
                        print("[SMS] ⚠️  未提取到 token，但登录可能已成功")
                    
                    # 更新cookies
                    cookies = login_result.get("cookies", [])
                    if cookies:
                        print(f"[SMS] ✅ 获取到 {len(cookies)} 个 cookies")
                        for cookie in cookies:
                            self._session.cookies.set(
                                cookie['name'],
                                cookie['value'],
                                domain=cookie.get('domain'),
                                path=cookie.get('path', '/')
                            )
                        print("[SMS] ✅ Cookies 已更新到 session")
                    
                    print("[SMS] ====== 浏览器登录成功 ======")
                    return True
                else:
                    print("[SMS] ❌ 浏览器登录失败")
                    return False
                    
            except json.JSONDecodeError as e:
                print(f"[SMS] ❌ 无法解析登录结果: {e}")
                return False
            
        except subprocess.TimeoutExpired:
            print("[SMS] ❌ 浏览器登录超时（120秒）")
            return False
        except Exception as e:
            print(f"[SMS] ❌ 浏览器登录异常: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False
