import random
import time
from dataclasses import dataclass


@dataclass
class FailureThrottleConfig:
    enabled: bool = True
    consecutive_failures: int = 6
    action: str = "both"  # pause | reduce | both
    pause_seconds: int = 120
    reduce_by: int = 1
    min_concurrent: int = 1
    recover_after_successes: int = 3
    recover_step: int = 1


class ThrottleState:
    def __init__(self, initial_max: int):
        self.initial_max = initial_max
        self.dynamic_limit = initial_max
        self.consecutive_failures = 0
        self.success_streak = 0


def _as_int(x, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return default


def on_success(state: ThrottleState, cfg: FailureThrottleConfig) -> None:
    if not cfg.enabled:
        return
    state.consecutive_failures = 0
    if cfg.recover_after_successes <= 0:
        state.success_streak = 0
        return
    state.success_streak += 1
    if state.success_streak >= cfg.recover_after_successes:
        state.success_streak = 0
        if state.dynamic_limit < state.initial_max:
            old = state.dynamic_limit
            state.dynamic_limit = min(state.initial_max, state.dynamic_limit + max(1, cfg.recover_step))
            print(f"[Throttle] 连续成功 {cfg.recover_after_successes} 次，并发 {old} -> {state.dynamic_limit}")


def on_failure(state: ThrottleState, cfg: FailureThrottleConfig) -> None:
    if not cfg.enabled:
        return
    state.success_streak = 0
    state.consecutive_failures += 1
    if state.consecutive_failures < max(1, cfg.consecutive_failures):
        return

    action = (cfg.action or "reduce").lower()
    if action in ("pause", "both"):
        ps = max(0, cfg.pause_seconds)
        print(f"[Throttle] 连续失败 {cfg.consecutive_failures} 次，暂停 {ps} 秒...")
        time.sleep(ps)
    if action in ("reduce", "both"):
        old = state.dynamic_limit
        state.dynamic_limit = max(max(1, cfg.min_concurrent), state.dynamic_limit - max(1, cfg.reduce_by))
        print(f"[Throttle] 连续失败 {cfg.consecutive_failures} 次，并发 {old} -> {state.dynamic_limit}")
    state.consecutive_failures = 0

