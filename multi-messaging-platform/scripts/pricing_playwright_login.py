#!/usr/bin/env python3
"""Login دستی Playwright برای scraper قیمت — session در persistent profile ذخیره می‌شود."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core_engine.config import get_settings


async def run_manual_login() -> int:
    settings = get_settings()
    profile_dir = Path(settings.PRICING_PLAYWRIGHT_PROFILE_DIR).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    target_url = settings.PRICING_API_URL

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright is not installed. Install requirements and try again.", file=sys.stderr)
        return 1

    print("Opening browser for manual login (headed mode).")
    print(f"Target URL: {target_url}")
    print(f"Profile directory: {profile_dir}")
    print("Complete login in the browser window, then return here and press Enter.")
    print("No credentials or cookies will be printed.")

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            locale="fa-IR",
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(target_url, wait_until="domcontentloaded")
            await asyncio.to_thread(input, "Press Enter after login is complete... ")
        finally:
            await context.close()

    print("Session saved to persistent profile.")
    return 0


def main() -> int:
    return asyncio.run(run_manual_login())


if __name__ == "__main__":
    raise SystemExit(main())
