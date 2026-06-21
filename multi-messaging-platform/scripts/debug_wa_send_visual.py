"""Debug WA send — saves screenshot on failure."""
from __future__ import annotations

import asyncio
import os
import sys

from playwright.async_api import async_playwright

from workers.whatsapp_web.playwright_sender import (
    WHATSAPP_WEB_URL,
    _MESSAGE_INPUT_SELECTORS,
    _SEND_BUTTON_SELECTORS,
    _compose_box_text,
    _detect_invalid_recipient,
    _fill_compose_box,
    _first_visible_locator,
    is_whatsapp_web_logged_in,
    open_whatsapp_persistent_context,
)


async def main() -> int:
    recipient = sys.argv[1] if len(sys.argv) > 1 else "989122270261"
    text = sys.argv[2] if len(sys.argv) > 2 else "TEST123 simple ascii"
    profile = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "SenderPlatform",
        "mmp-whatsapp",
        "account-248",
    )
    headless = os.environ.get("WHATSAPP_WEB_HEADLESS", "false").lower() == "true"
    out = os.path.join(os.path.dirname(__file__), "..", "storage", "wa_send_debug.png")

    async with async_playwright() as p:
        ctx = await open_whatsapp_persistent_context(p, profile, headless=headless)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(WHATSAPP_WEB_URL, wait_until="domcontentloaded")
        print("logged_in", await is_whatsapp_web_logged_in(page, timeout_ms=45000))
        send_url = f"{WHATSAPP_WEB_URL}/send?phone={recipient}"
        await page.goto(send_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        print("url", page.url)
        print("invalid", await _detect_invalid_recipient(page))

        msg_input = None
        for sel in _MESSAGE_INPUT_SELECTORS:
            try:
                await page.wait_for_selector(sel, timeout=8000)
                msg_input = page.locator(sel).first
                print("compose_sel", sel)
                break
            except Exception:
                pass

        if msg_input:
            await _fill_compose_box(page, msg_input, text)
            print("after_fill", repr(await _compose_box_text(msg_input)))

        btn = await _first_visible_locator(page, _SEND_BUTTON_SELECTORS)
        print("send_btn", btn is not None)
        if btn:
            await btn.click()
        else:
            await page.keyboard.press("Enter")

        await page.wait_for_timeout(5000)
        count = await page.locator("div.message-out").count()
        print("message_out", count)
        if count:
            try:
                print("last_out", repr(await page.locator("div.message-out span.selectable-text").last.inner_text()))
            except Exception as e:
                print("last_out_err", e)

        await page.screenshot(path=out, full_page=True)
        print("screenshot", os.path.abspath(out))
        await ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
