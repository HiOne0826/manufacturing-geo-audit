from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.deepseek_web import CHAT_URL, DeepSeekWebBrowser, DeepSeekWebConfig, deepseek_web_status
from src.runtime_env import load_dotenv_file


def login(auth_state: Path) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("缺少 Playwright，请安装 requirements-web-worker.txt") from exc
    auth_state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai", viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(CHAT_URL, wait_until="domcontentloaded")
        print("请在浏览器中完成 DeepSeek 登录，并确认已进入可输入问题的对话页。")
        input("完成后按 Enter 保存登录态：")
        if page.locator("textarea, [contenteditable='true']").count() == 0:
            raise SystemExit("页面上没有检测到输入框，登录态未保存")
        context.storage_state(path=str(auth_state))
        auth_state.chmod(0o600)
        context.close()
        browser.close()
    print(f"登录态已保存：{auth_state}")
    return 0


def main() -> int:
    load_dotenv_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="DeepSeek 官网采样登录与健康检查")
    parser.add_argument("command", choices=("login", "status", "preflight"))
    parser.add_argument("--auth-state", default=os.environ.get("DEEPSEEK_WEB_AUTH_STATE", "private/deepseek-web/storage-state.json"))
    args = parser.parse_args()
    auth_state = Path(args.auth_state).expanduser()
    if not auth_state.is_absolute():
        auth_state = ROOT / auth_state
    if args.command == "login":
        return login(auth_state)
    if args.command == "status":
        print(deepseek_web_status())
        return 0
    config = DeepSeekWebConfig.from_env()
    if auth_state != config.auth_state:
        config = DeepSeekWebConfig(**{**config.__dict__, "auth_state": auth_state})
    browser = DeepSeekWebBrowser(config)
    try:
        print(browser.preflight())
    finally:
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
