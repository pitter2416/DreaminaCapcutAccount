import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from controllers.browser_controller import BrowserConfig, BrowserController
from flows.registration_flow import FlowConfig, RegistrationFlow
from results_store import AccountResult, ResultsStore
from throttle import FailureThrottleConfig, ThrottleState, on_failure, on_success
from utils import Account, ensure_dir, human_delay, load_accounts, remove_accounts
from sms_helper import SMSCodeFetcher, SMSFetcherConfig


SHUTTING_DOWN = False


def auto_generate_accounts(accounts_file: str, count: int = 20) -> bool:
    """
    当账号用尽时，自动生成新账号
    
    Args:
        accounts_file: 账号文件路径
        count: 生成数量
    
    Returns:
        bool: 是否成功生成
    """
    try:
        print(f"\n[AutoGen] 检测到账号已用完，开始自动生成 {count} 个新账号...")
        
        # 获取当前脚本所在目录
        script_dir = os.path.dirname(os.path.abspath(__file__))
        generator_script = os.path.join(script_dir, "generate_accounts.py")
        
        if not os.path.exists(generator_script):
            print(f"[AutoGen] ❌ 找不到生成脚本: {generator_script}")
            return False
        
        # 调用 generate_accounts.py
        result = subprocess.run(
            [
                sys.executable,
                generator_script,
                "-n", str(count),
                "-o", accounts_file
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        # 打印输出
        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                print(f"[AutoGen] {line}")
        
        if result.stderr:
            print(f"[AutoGen] ⚠️  警告信息:")
            for line in result.stderr.strip().split('\n'):
                print(f"[AutoGen]   {line}")
        
        if result.returncode == 0:
            print(f"[AutoGen] ✅ 新账号生成成功")
            return True
        else:
            print(f"[AutoGen] ❌ 生成失败，返回码: {result.returncode}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"[AutoGen] ❌ 生成超时（30秒）")
        return False
    except Exception as e:
        print(f"[AutoGen] ❌ 异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


@dataclass
class AppConfig:
    target_url: str
    headless: bool
    concurrent_flows: int
    max_tasks: int
    step_delay_ms: int
    jitter_ms: int
    human_pause_ms: int
    otp_timeout_seconds: int
    otp_poll_interval_ms: int
    sms_enabled: bool
    sms_endpoint_url: str
    sms_token: str
    sms_timeout_seconds: int
    sms_trace_id_prefix: str
    sms_tls_verify: bool
    accounts_file: str
    results_dir: str
    run_id: str
    failure_throttle: FailureThrottleConfig


def load_config() -> AppConfig:
    with open("config.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    sms = data.get("sms") or {}
    ft = data.get("failure_throttle") or {}
    ft_cfg = FailureThrottleConfig(
        enabled=bool(ft.get("enabled", True)),
        consecutive_failures=int(ft.get("consecutive_failures", 6)),
        action=str(ft.get("action", "both")),
        pause_seconds=int(ft.get("pause_seconds", 120)),
        reduce_by=int(ft.get("reduce_by", 1)),
        min_concurrent=int(ft.get("min_concurrent", 1)),
        recover_after_successes=int(ft.get("recover_after_successes", 3)),
        recover_step=int(ft.get("recover_step", 1)),
    )

    return AppConfig(
        target_url=str(data["target_url"]),
        headless=bool(data.get("headless", False)),
        concurrent_flows=int(data.get("concurrent_flows", 2)),
        max_tasks=int(data.get("max_tasks", 0)),
        step_delay_ms=int(data.get("step_delay_ms", 900)),
        jitter_ms=int(data.get("jitter_ms", 700)),
        human_pause_ms=int(data.get("human_pause_ms", 1800)),
        otp_timeout_seconds=int(data.get("otp_timeout_seconds", 300)),
        otp_poll_interval_ms=int(data.get("otp_poll_interval_ms", 800)),
        sms_enabled=bool(sms.get("enabled", False)),
        sms_endpoint_url=str(sms.get("endpoint_url", "")),
        sms_token=str(sms.get("token", "")),
        sms_timeout_seconds=int(sms.get("timeout_seconds", 60)),
        sms_trace_id_prefix=str(sms.get("trace_id_prefix", "")),
        sms_tls_verify=bool(sms.get("tls_verify", True)),
        accounts_file=str(data.get("accounts_file", "accounts.txt")),
        results_dir=str(data.get("results_dir", "Results")),
        run_id=str(data.get("run_id") or ""),
        failure_throttle=ft_cfg,
    )


def _finish_future(future, stats, throttle_state: ThrottleState, throttle_cfg: FailureThrottleConfig, apply_throttle: bool):
    try:
        ok = future.result()
    except Exception as e:
        if not SHUTTING_DOWN:
            print(e)
        ok = False

    if ok:
        stats["succeeded"] += 1
        if apply_throttle:
            on_success(throttle_state, throttle_cfg)
        try:
            if hasattr(future, "acc"):
                future.acc._success_recorded = True
        except Exception:
            pass
    else:
        stats["failed"] += 1
        if apply_throttle:
            on_failure(throttle_state, throttle_cfg)


def run_one(flow: RegistrationFlow, store: ResultsStore, acc: Account, accounts_file: str) -> bool:
    started = time.time()
    try:
        ok, reason = flow.run(acc)
        ended = time.time()
        store.append(
            AccountResult(
                email=acc.email,
                password=acc.password,
                status="success" if ok else "fail",
                reason=reason,
                started_at=started,
                ended_at=ended,
                run_id=store.run_id,
            )
        )
        if ok:
            remove_accounts(accounts_file, [acc.email])
        return ok
    except TimeoutError as e:
        ended = time.time()
        store.append(
            AccountResult(
                email=acc.email,
                password=acc.password,
                status="otp_timeout",
                reason=str(e),
                started_at=started,
                ended_at=ended,
                run_id=store.run_id,
            )
        )
        return False
    except Exception as e:
        ended = time.time()
        store.append(
            AccountResult(
                email=acc.email,
                password=acc.password,
                status="fail",
                reason=str(e),
                started_at=started,
                ended_at=ended,
                run_id=store.run_id,
            )
        )
        return False


def run_loop(cfg: AppConfig) -> None:
    ensure_dir(cfg.results_dir)

    run_id = cfg.run_id or time.strftime("%Y%m%d-%H%M%S")
    store = ResultsStore(cfg.results_dir, run_id)

    sms_fetcher: Optional[SMSCodeFetcher] = None
    if cfg.sms_enabled:
        sms_fetcher = SMSCodeFetcher(
            SMSFetcherConfig(
                endpoint_url=cfg.sms_endpoint_url,
                token=cfg.sms_token,
                timeout_seconds=cfg.sms_timeout_seconds,
                tls_verify=cfg.sms_tls_verify,
            )
        )

    accounts = load_accounts(cfg.accounts_file)
    succeeded = store.load_success_set()
    pending_accounts = [a for a in accounts if a.email not in succeeded]
    if not pending_accounts:
        print("所有账号都已成功记录，尝试自动生成新账号...")
        # 自动生成新账号
        if auto_generate_accounts(cfg.accounts_file, count=20):
            # 重新加载账号
            accounts = load_accounts(cfg.accounts_file)
            succeeded = store.load_success_set()
            pending_accounts = [a for a in accounts if a.email not in succeeded]
            
            if not pending_accounts:
                print("生成后仍然没有可用账号，退出。")
                return
            else:
                print(f"✅ 已加载 {len(pending_accounts)} 个新账号，继续运行...")
        else:
            print("自动生成账号失败，退出。")
            return

    controller = BrowserController(
        BrowserConfig(headless=cfg.headless),
        browser_root=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".playwright-browsers"),
    )
    flow = RegistrationFlow(
        controller=controller,
        flow_cfg=FlowConfig(
            target_url=cfg.target_url,
            step_delay_ms=cfg.step_delay_ms,
            jitter_ms=cfg.jitter_ms,
            human_pause_ms=cfg.human_pause_ms,
            otp_timeout_seconds=cfg.otp_timeout_seconds,
            otp_poll_interval_ms=cfg.otp_poll_interval_ms,
            sms_enabled=cfg.sms_enabled,
            sms_fetcher=sms_fetcher,
            sms_trace_id_prefix=cfg.sms_trace_id_prefix,
            results_dir=cfg.results_dir,
            run_id=run_id,
        ),
    )

    stats = {"succeeded": 0, "failed": 0}
    task_counter = 0
    infinite_mode = cfg.max_tasks <= 0
    stop_requested = False
    throttle_state = ThrottleState(initial_max=max(1, cfg.concurrent_flows))
    succeeded_emails = set(succeeded)
    succeeded_lock = threading.Lock()
    next_index = 0

    with ThreadPoolExecutor(max_workers=max(1, cfg.concurrent_flows)) as executor:
        running = set()

        def next_account() -> Optional[Account]:
            nonlocal next_index, pending_accounts
            if not pending_accounts:
                # 尝试自动生成新账号
                print("\n[AutoGen] 运行时检测到账号已用完，尝试生成新账号...")
                if auto_generate_accounts(cfg.accounts_file, count=20):
                    # 重新加载账号
                    accounts = load_accounts(cfg.accounts_file)
                    succeeded = store.load_success_set()
                    pending_accounts = [a for a in accounts if a.email not in succeeded]
                    next_index = 0  # 重置索引
                    print(f"[AutoGen] ✅ 已加载 {len(pending_accounts)} 个新账号")
                else:
                    print("[AutoGen] ❌ 生成失败")
                    return None
            
            if not pending_accounts:
                return None
            
            for _ in range(len(pending_accounts)):
                acc = pending_accounts[next_index]
                next_index = (next_index + 1) % len(pending_accounts)
                with succeeded_lock:
                    if acc.email not in succeeded_emails:
                        return acc
            return None

        try:
            while (((infinite_mode or task_counter < cfg.max_tasks) and len(succeeded_emails) < len(pending_accounts))
                   or len(running) > 0):
                done = {f for f in running if f.done()}
                for f in done:
                    _finish_future(f, stats, throttle_state, cfg.failure_throttle, apply_throttle=True)
                    if hasattr(f, "acc") and getattr(f, "acc", None) is not None:
                        try:
                            if f.result():
                                with succeeded_lock:
                                    succeeded_emails.add(f.acc.email)
                        except Exception:
                            pass
                    running.remove(f)

                limit = throttle_state.dynamic_limit
                while (not stop_requested) and len(running) < limit and ((infinite_mode or task_counter < cfg.max_tasks)
                      and len(succeeded_emails) < len(pending_accounts)):
                    acc = next_account()
                    if acc is None:
                        break
                    fut = executor.submit(run_one, flow, store, acc, cfg.accounts_file)
                    fut.acc = acc
                    running.add(fut)
                    task_counter += 1
                    if infinite_mode and task_counter % max(1, cfg.concurrent_flows) == 0:
                        print(
                            f"已提交 {task_counter} 个任务（持续运行中，当前并发上限 {limit}，Ctrl+C 可停止）"
                        )

                time.sleep(0.3)
        except KeyboardInterrupt:
            global SHUTTING_DOWN
            SHUTTING_DOWN = True
            print("\n收到停止信号：停止提交新任务，等待当前任务完成后退出...")
            stop_requested = True
            while len(running) > 0:
                done = {f for f in running if f.done()}
                for f in done:
                    _finish_future(f, stats, throttle_state, cfg.failure_throttle, apply_throttle=False)
                    running.remove(f)
                time.sleep(0.3)
        finally:
            controller.close_all()

    total_text = "持续模式" if infinite_mode else str(cfg.max_tasks)
    print(f"\n[Result] - 共: {total_text}, 已提交 {task_counter}, 成功 {stats['succeeded']}, 失败 {stats['failed']}")


if __name__ == "__main__":
    cfg = load_config()
    run_loop(cfg)

