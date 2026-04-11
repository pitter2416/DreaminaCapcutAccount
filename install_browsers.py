"""
把浏览器安装到项目目录内（.playwright-browsers），避免依赖全局缓存路径。

用法（在已激活 .venv 中）:
  python install_browsers.py
  python install_browsers.py --force
"""

import os
import subprocess
import sys


def main() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    browser_root = os.path.join(root, ".playwright-browsers")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_root
    print(f"PLAYWRIGHT_BROWSERS_PATH={browser_root}", flush=True)

    # patchright 的浏览器修订号可能与 playwright 不同，这里优先用 patchright 安装 chromium。
    cmd = [sys.executable, "-m", "patchright", "install", "chromium"] + sys.argv[1:]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()

