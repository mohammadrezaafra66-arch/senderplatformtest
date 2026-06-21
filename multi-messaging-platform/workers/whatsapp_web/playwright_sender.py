"""Playwright helpers for WhatsApp Web send and session probing."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from workers.errors import PermanentWorkerError, RetryableWorkerError

logger = logging.getLogger("workers.whatsapp_web.playwright_sender")

WHATSAPP_WEB_URL = "https://web.whatsapp.com"

# Reduce automation fingerprint; prefer installed Google Chrome over bundled Chromium.
_WHATSAPP_BROWSER_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
)


def whatsapp_persistent_context_options(
    profile_dir: str,
    *,
    headless: bool,
) -> dict:
    """Kwargs for Playwright persistent context used by WA Web link/send."""
    return {
        "user_data_dir": profile_dir,
        "headless": headless,
        "locale": "en-US",
        "channel": "chrome",
        "ignore_default_args": ["--enable-automation"],
        "args": list(_WHATSAPP_BROWSER_ARGS),
    }


async def open_whatsapp_persistent_context(playwright, profile_dir: str, *, headless: bool):
    """Launch WA Web browser context; fall back to Chromium if Chrome is missing."""
    options = whatsapp_persistent_context_options(profile_dir, headless=headless)
    try:
        return await playwright.chromium.launch_persistent_context(**options)
    except Exception as exc:
        logger.warning("chrome channel unavailable (%s); falling back to chromium", exc)
        fallback = dict(options)
        fallback.pop("channel", None)
        return await playwright.chromium.launch_persistent_context(**fallback)

_LOGGED_IN_SELECTORS = (
    "#pane-side",
    "#side",
    '[data-testid="chat-list"]',
)

_QR_SELECTORS = (
    'canvas[aria-label*="QR"]',
    '[data-testid="qrcode"]',
    'div[data-ref] canvas',
)

# After QR scan, WA Web often stalls here (common on slow/filtered networks).
_LOADING_OR_SYNC_SELECTORS = (
    'text=Loading your chats',
    'text=Syncing',
    '[data-testid="startup"]',
    '[data-icon="progress"]',
)

_MESSAGE_INPUT_SELECTORS = (
    'div[contenteditable="true"][data-tab="10"]',
    'footer div[contenteditable="true"]',
    '[data-testid="conversation-compose-box-input"]',
)

_SEND_BUTTON_SELECTORS = (
    'button[data-tab="11"]',
    'span[data-icon="send"]',
    '[data-testid="send"]',
)


@dataclass(frozen=True, slots=True)
class WhatsAppWebSendResult:
    message_id: str
    recipient_digits: str


async def _first_visible_locator(page, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() > 0 and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def is_whatsapp_web_logged_in(page, *, timeout_ms: int) -> bool:
    """Return True when the main chat list is visible (session active)."""
    for selector in _LOGGED_IN_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            continue

    for selector in _QR_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=min(timeout_ms, 3000))
            return False
        except Exception:
            continue

    return False


async def is_whatsapp_web_session_linked(page, *, timeout_ms: int) -> bool:
    """True when QR is gone and WA Web is syncing/loading or fully logged in."""
    if await is_whatsapp_web_logged_in(page, timeout_ms=timeout_ms):
        return True

    for selector in _LOADING_OR_SYNC_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=min(timeout_ms, 5000))
            return True
        except Exception:
            continue

    for selector in _QR_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=min(timeout_ms, 2000))
            return False
        except Exception:
            continue

    return False


async def probe_whatsapp_web_session(
    profile_dir: str,
    *,
    headless: bool = True,
    timeout_ms: int = 30000,
) -> bool:
    """Open WA Web with a persistent profile and check whether the user is logged in."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        context = await open_whatsapp_persistent_context(
            playwright, profile_dir, headless=headless
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(WHATSAPP_WEB_URL, wait_until="domcontentloaded")
            return await is_whatsapp_web_session_linked(page, timeout_ms=timeout_ms)
        finally:
            await context.close()


async def _compose_box_text(message_input) -> str:
    try:
        text = await message_input.evaluate(
            """el => {
                const direct = (el.innerText || el.textContent || '').trim();
                if (direct) return direct;
                const leaf = el.querySelector('[data-lexical-text="true"]');
                return leaf ? (leaf.textContent || '').trim() : '';
            }"""
        )
        return str(text).strip()
    except Exception:
        return ""


async def _fill_compose_box(page, message_input, text: str) -> None:
    """Type message text into WhatsApp Web's contenteditable compose box."""
    await message_input.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    try:
        await page.keyboard.insert_text(text)
    except Exception:
        await message_input.press_sequentially(text, delay=10)


async def _wait_for_outgoing_message(page, text: str, *, timeout_ms: int) -> None:
    """Require the open chat to show our text in an outgoing bubble or main panel."""
    import asyncio
    import time

    snippet = str(text).strip()
    if not snippet:
        raise RetryableWorkerError("WhatsApp Web message text is empty.")

    match_key = snippet[:40] if len(snippet) > 40 else snippet

    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        selectors = (
            "div.message-out span.selectable-text",
            "div.message-out",
            '[data-testid="conversation-panel-body"] div.message-out',
        )
        for selector in selectors:
            bubbles = page.locator(selector)
            count = await bubbles.count()
            for index in range(count - 1, max(count - 8, -1), -1):
                try:
                    body = (await bubbles.nth(index).inner_text()).strip()
                except Exception:
                    continue
                if match_key in body or body in snippet:
                    return

        try:
            main_text = await page.locator("#main").inner_text()
            if match_key in main_text:
                return
        except Exception:
            pass

        await asyncio.sleep(0.5)

    raise RetryableWorkerError(
        "WhatsApp Web did not show a sent message bubble; delivery is unconfirmed."
    )


async def _detect_invalid_recipient(page) -> str | None:
    invalid_markers = (
        "Phone number shared via url is invalid",
        "phone number shared via url is invalid",
        "Invalid phone number",
    )
    for marker in invalid_markers:
        try:
            locator = page.get_by_text(marker, exact=False)
            if await locator.count() > 0 and await locator.first.is_visible():
                return marker
        except Exception:
            continue
    return None


async def send_whatsapp_web_message(
    profile_dir: str,
    recipient_digits: str,
    text: str,
    *,
    headless: bool = True,
    timeout_ms: int = 90000,
    account_id: int | None = None,
    source: str = "script",
    message_id: str | None = None,
    record_delivery_audit: bool = True,
) -> WhatsAppWebSendResult:
    """Send a text message through WhatsApp Web using a saved browser profile."""
    from playwright.async_api import async_playwright

    from core_engine.services.delivery_audit import record_whatsapp_delivery_audit
    from core_engine.services.whatsapp_send_guard import (
        WhatsAppSendBlockedError,
        assert_whatsapp_send_allowed,
    )

    message_text = str(text).strip()

    def _audit(
        *,
        success: bool,
        status: str,
        platform_message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if account_id is None or not record_delivery_audit:
            return
        record_whatsapp_delivery_audit(
            source=source,
            account_id=account_id,
            recipient=recipient_digits,
            message_id=message_id,
            message_text=message_text,
            success=success,
            status=status,
            platform_message_id=platform_message_id,
            error_code=error_code,
            error_message=error_message,
        )

    try:
        await assert_whatsapp_send_allowed()
    except WhatsAppSendBlockedError as exc:
        _audit(
            success=False,
            status="blocked",
            error_code="whatsapp_send_disabled",
            error_message=str(exc),
        )
        raise PermanentWorkerError(str(exc)) from exc

    if not recipient_digits or not recipient_digits.isdigit():
        raise PermanentWorkerError("WhatsApp Web recipient must be E.164 digits without '+'.")
    if not message_text:
        raise PermanentWorkerError("WhatsApp Web message text is empty.")

    send_url = f"{WHATSAPP_WEB_URL}/send?phone={recipient_digits}"

    try:
        async with async_playwright() as playwright:
            context = await open_whatsapp_persistent_context(
                playwright, profile_dir, headless=headless
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(WHATSAPP_WEB_URL, wait_until="domcontentloaded")

                if not await is_whatsapp_web_logged_in(page, timeout_ms=min(timeout_ms, 45000)):
                    raise PermanentWorkerError(
                        "WhatsApp Web session is not logged in. Scan QR with whatsapp_web_link."
                    )

                await page.goto(send_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                invalid_reason = await _detect_invalid_recipient(page)
                if invalid_reason:
                    raise PermanentWorkerError(
                        f"WhatsApp Web rejected recipient {recipient_digits}: {invalid_reason}"
                    )

                message_input = None
                per_selector_timeout = max(timeout_ms // len(_MESSAGE_INPUT_SELECTORS), 5000)
                for selector in _MESSAGE_INPUT_SELECTORS:
                    try:
                        await page.wait_for_selector(selector, timeout=per_selector_timeout)
                        message_input = page.locator(selector).first
                        break
                    except Exception:
                        continue

                if message_input is None:
                    raise RetryableWorkerError(
                        "WhatsApp Web compose box did not appear. Recipient may be invalid."
                    )

                await _fill_compose_box(page, message_input, message_text)

                if not await _compose_box_text(message_input):
                    raise RetryableWorkerError(
                        "WhatsApp Web compose box is empty after typing; send aborted."
                    )

                send_button = await _first_visible_locator(page, _SEND_BUTTON_SELECTORS)
                if send_button is not None:
                    await send_button.click()
                else:
                    await page.keyboard.press("Enter")

                await page.wait_for_timeout(500)
                await page.keyboard.press("Enter")

                await _wait_for_outgoing_message(
                    page,
                    message_text,
                    timeout_ms=min(timeout_ms, 45000),
                )

                wa_message_id = f"wa-web-{recipient_digits}-{uuid.uuid4().hex[:12]}"
                logger.info(
                    "whatsapp_web_send_ok recipient_suffix=%s message_id=%s",
                    recipient_digits[-4:] if len(recipient_digits) >= 4 else "****",
                    wa_message_id,
                )
                result = WhatsAppWebSendResult(
                    message_id=wa_message_id,
                    recipient_digits=recipient_digits,
                )
                _audit(success=True, status="delivered", platform_message_id=wa_message_id)
                return result
            finally:
                await context.close()
    except PermanentWorkerError as exc:
        _audit(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_web_session_expired",
            error_message=str(exc),
        )
        raise
    except RetryableWorkerError as exc:
        _audit(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_web_send_failed",
            error_message=str(exc),
        )
        raise
    except Exception as exc:
        wrapped = RetryableWorkerError(f"WhatsApp Web send failed: {exc}")
        _audit(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_web_send_failed",
            error_message=str(wrapped),
        )
        raise wrapped from exc
