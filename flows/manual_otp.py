import time


def wait_for_otp_completion(page, *, timeout_seconds: int, poll_interval_ms: int, success_predicate) -> None:
    """
    人工 OTP 门控：
    - 脚本检测到 OTP 页面后调用此函数
    - 由用户在浏览器里手动输入验证码并提交
    - 本函数轮询 success_predicate(page) 直到通过或超时
    """
    deadline = time.time() + max(1, int(timeout_seconds))
    print(
        f"[OTP] 检测到验证码步骤：请在浏览器里手动输入验证码并提交。"
        f"（超时 {timeout_seconds}s）",
        flush=True,
    )
    while time.time() < deadline:
        try:
            if success_predicate(page):
                print("[OTP] 已检测到进入下一步，继续流程。", flush=True)
                return
        except Exception:
            # 页面可能在跳转中，忽略本次轮询
            pass
        page.wait_for_timeout(max(200, int(poll_interval_ms)))
    raise TimeoutError("OTP 等待超时：未检测到进入下一步（请检查是否已提交验证码）")

