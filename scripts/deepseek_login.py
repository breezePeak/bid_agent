from __future__ import annotations

"""Open the persistent DeepSeek profile once so a user can complete login."""

import os
from pathlib import Path
from time import monotonic

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = Path(
    os.environ.get(
        "BID_AGENT_DEEPSEEK_PROFILE_DIR",
        str(ROOT / ".runtime" / "deepseek-playwright"),
    )
).resolve()
WAIT_MS = max(60_000, min(int(os.environ.get("BID_AGENT_DEEPSEEK_LOGIN_WAIT_MS", "300000")), 900_000))


def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://chat.deepseek.com/", wait_until="domcontentloaded", timeout=30_000)
            page.bring_to_front()
            print("DeepSeek 登录窗口已打开，请在窗口中完成登录。", flush=True)
            deadline = monotonic() + WAIT_MS / 1000
            while monotonic() < deadline:
                if page.locator("textarea").count() > 0:
                    print("DeepSeek 登录状态已保存。", flush=True)
                    return 0
                page.wait_for_timeout(1_000)
            print("等待登录超时；未修改已有会话。", flush=True)
            return 2
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
