import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, Optional

from utils import ensure_dir


@dataclass
class AccountResult:
    email: str
    status: str  # success | fail | otp_timeout
    reason: str
    started_at: float
    ended_at: float
    run_id: str


class ResultsStore:
    def __init__(self, results_dir: str, run_id: str):
        self.results_dir = results_dir
        self.run_id = run_id
        ensure_dir(results_dir)
        self._status_path = os.path.join(results_dir, "status.jsonl")
        self._success_path = os.path.join(results_dir, "success.txt")
        self._fail_path = os.path.join(results_dir, "fail.txt")

    def append(self, res: AccountResult) -> None:
        with open(self._status_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
        if res.status == "success":
            with open(self._success_path, "a", encoding="utf-8") as f:
                f.write(f"{res.email}\n")
        else:
            with open(self._fail_path, "a", encoding="utf-8") as f:
                f.write(f"{res.email}\t{res.status}\t{res.reason}\n")

    def load_success_set(self) -> set[str]:
        if not os.path.exists(self._success_path):
            return set()
        with open(self._success_path, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}

