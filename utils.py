import os
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


EMAIL_PASS_RE = re.compile(r"^\s*(?P<email>[^:\s]+)\s*:\s*(?P<password>.+?)\s*$")


@dataclass(frozen=True)
class Account:
    email: str
    password: str


def sleep_ms(ms: int) -> None:
    time.sleep(max(0, ms) / 1000)


def human_delay(step_delay_ms: int, jitter_ms: int) -> None:
    base = max(0, int(step_delay_ms))
    jitter = max(0, int(jitter_ms))
    sleep_ms(base + (random.randint(0, jitter) if jitter else 0))


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


_REMOVE_ACCOUNTS_LOCK = threading.Lock()


def load_accounts(path: str) -> List[Account]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"账号文件不存在: {path}\n"
            f"请创建 accounts.txt（可参考 accounts.example.txt），每行格式: email: password"
        )
    accounts: List[Account] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = EMAIL_PASS_RE.match(line)
            if not m:
                raise ValueError(f"账号文件格式错误（第 {i} 行）: {line!r}，期望: email: password")
            accounts.append(Account(email=m.group("email"), password=m.group("password")))
    if not accounts:
        raise ValueError("账号文件为空：请至少提供一行 email: password")
    return accounts


def remove_accounts(path: str, emails_to_remove: Iterable[str]) -> None:
    emails_to_remove_set = {email.strip() for email in emails_to_remove if email and email.strip()}
    if not emails_to_remove_set:
        return

    with _REMOVE_ACCOUNTS_LOCK:
        if not os.path.exists(path):
            return

        temp_path = f"{path}.tmp"
        with open(path, "r", encoding="utf-8") as src, open(temp_path, "w", encoding="utf-8") as dst:
            for line in src:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    dst.write(line)
                    continue
                m = EMAIL_PASS_RE.match(stripped)
                if not m:
                    dst.write(line)
                    continue
                if m.group("email") in emails_to_remove_set:
                    continue
                dst.write(line)

        os.replace(temp_path, path)

