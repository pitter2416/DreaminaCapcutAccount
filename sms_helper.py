import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

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
            "PageCount": "10",
            "traceId": trace_id,
        }

        try:
            print(
                f"[SMS] request start: url={self._endpoint_url}, mailbox={mailbox}, trace_id={trace_id}, params={params}"
            )
            resp = self._session.get(self._endpoint_url, params=params, timeout=10)
            print(f"[SMS] response status: {resp.status_code}")
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
            print(f"[SMS] code matched from subject for mailbox={mailbox}")
            return m.group(1).strip().upper()

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
        # 默认基线：当前时刻向前放宽 15 分钟；若上层传入，则以其为准。
        effective_baseline_ms = int(start * 1000) - 900_000 if baseline_ms is None else int(baseline_ms)
        # 接口 createTime 与本地提交时刻可能存在几秒级偏差，允许小窗口容差。
        baseline_tolerance_ms = 15_000
        accepted_baseline_ms = effective_baseline_ms - baseline_tolerance_ms
        print(f"[SMS] polling start: timeout={timeout}s interval={interval}s mailbox={mailbox}")
        print(
            f"[SMS] baseline createTime(ms)>={effective_baseline_ms}, accepted with tolerance(ms)>={accepted_baseline_ms}"
        )
        while time.time() - start < timeout:
            params = {
                "Folder": "Inbox",
                "MailBox": mailbox,
                "FilterType": "0",
                "PageIndex": "1",
                "PageCount": "10",
                "traceId": trace_id,
            }
            try:
                print(
                    f"[SMS] request start: url={self._endpoint_url}, mailbox={mailbox}, trace_id={trace_id}, params={params}"
                )
                resp = self._session.get(self._endpoint_url, params=params, timeout=10)
                print(f"[SMS] response status: {resp.status_code}")
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
            latest_fallback_code: Optional[str] = None
            latest_fallback_ctime = 0
            for mail in mail_list_sorted:
                ctime_ms = _to_ms(mail.get("createTime", 0))
                mtime_ms = _to_ms(mail.get("modifyDate", 0))
                event_ms = max(ctime_ms, mtime_ms)
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

                # 优先：本轮时间窗口内的最新验证码
                if event_ms >= accepted_baseline_ms:
                    print(
                        f"[SMS] polling success: code={code}, eventTime={event_ms}, createTime={ctime_ms}, modifyDate={mtime_ms}, messageId={msg_id}"
                    )
                    return code

                # 兜底：保留列表里最新的可解析验证码，避免接口时间戳漂移造成死循环
                if event_ms > latest_fallback_ctime:
                    latest_fallback_ctime = event_ms
                    latest_fallback_code = code

                if unmatched_debug < 3:
                    if unmatched_debug < 3:
                        print(
                            f"[SMS] skip old mail: code={code} eventTime={event_ms} createTime={ctime_ms} modifyDate={mtime_ms} baseline={effective_baseline_ms} accepted_baseline={accepted_baseline_ms} messageId={msg_id} subject={str(mail.get('subject') or '')[:80]}"
                        )
                        unmatched_debug += 1

            # 仅在未显式传入 baseline_ms 时，才允许旧码兜底。
            # 传入 baseline_ms 代表上层要求“只要本次提交后的新验证码”。
            if baseline_ms is None and latest_fallback_code:
                print(
                    f"[SMS] polling fallback success: code={latest_fallback_code}, createTime={latest_fallback_ctime} (older than baseline)"
                )
                return latest_fallback_code

            print("[SMS] no fresh code matched in this polling round")
            time.sleep(interval)
        print("[SMS] polling timeout without code")
        return None
