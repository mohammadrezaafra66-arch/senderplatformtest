"""CLI: link a WhatsApp account via QR scan (Playwright persistent profile).

Usage:
    python -m workers.whatsapp_web_link --account-id 1

Opens a visible browser window. After scanning QR on your phone, session metadata
is stored in channel_sessions (BROWSER_PROFILE) for the worker to reuse.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from core_engine.config import get_settings
from core_engine.database import SessionLocal
from core_engine.models import Account, PlatformType
from core_engine.services.whatsapp_web_session import (
    resolve_whatsapp_profile_dir,
    store_whatsapp_web_session,
)
from workers.whatsapp_web.playwright_sender import (
    WHATSAPP_WEB_URL,
    is_whatsapp_web_logged_in,
    open_whatsapp_persistent_context,
    probe_whatsapp_web_session,
)

logger = logging.getLogger("workers.whatsapp_web_link")


async def _wait_for_qr_login(
    profile_dir: str,
    *,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> bool:
    from playwright.async_api import async_playwright

    deadline = time.monotonic() + timeout_seconds
    async with async_playwright() as playwright:
        context = await open_whatsapp_persistent_context(
            playwright, profile_dir, headless=False
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(WHATSAPP_WEB_URL, wait_until="domcontentloaded")
            logger.info("Scan the QR code in the browser window with WhatsApp on your phone.")

            while time.monotonic() < deadline:
                if await is_whatsapp_web_logged_in(page, timeout_ms=5000):
                    # Let Chromium flush the persistent profile before closing context.
                    await asyncio.sleep(5)
                    return True
                await asyncio.sleep(poll_interval_seconds)
            return False
        finally:
            await context.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Link WhatsApp Web via QR for an account.")
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument(
        "--profile-dir",
        type=str,
        default=None,
        help="Override browser profile directory (absolute path).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="How long to wait for QR scan (default: 300).",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Only check whether an existing profile is logged in (no QR window).",
    )
    parser.add_argument(
        "--register-from-profile",
        action="store_true",
        help=(
            "After linking in real Chrome (manual QR): verify profile on disk "
            "and save session metadata to the database."
        ),
    )
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="With --register-from-profile: save session when profile files exist (skip Playwright probe).",
    )
    return parser.parse_args()


def _store_linked_session(db, account, profile_dir) -> int:
    from core_engine.services.whatsapp_web_session import profile_dir_has_browser_data

    source = Path(profile_dir)
    canonical = resolve_whatsapp_profile_dir(account.id)

    if not profile_dir_has_browser_data(source) and not profile_dir_has_browser_data(canonical):
        logger.error(
            "No browser profile data under %s or %s. Scan QR in real Chrome first.",
            source,
            canonical,
        )
        return 3

    # Persist container-relative path so Docker workers/API resolve the mounted volume.
    store_whatsapp_web_session(
        db,
        account_id=account.id,
        linked=True,
        phone=account.phone_number,
        profile_dir=str(canonical.as_posix()),
    )
    db.commit()
    logger.info(
        "WhatsApp Web linked for account_id=%s profile_dir=%s (source=%s)",
        account.id,
        canonical,
        source,
    )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args()
    get_settings()

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == args.account_id).first()
        if account is None:
            logger.error("Account %s not found.", args.account_id)
            return 1
        if account.platform != PlatformType.WHATSAPP:
            logger.error("Account %s is not a WhatsApp account.", args.account_id)
            return 1

        profile_dir = (
            Path(args.profile_dir).resolve()
            if args.profile_dir
            else resolve_whatsapp_profile_dir(args.account_id).resolve()
        )
        profile_dir.mkdir(parents=True, exist_ok=True)

        if args.probe_only:
            logged_in = asyncio.run(
                probe_whatsapp_web_session(str(profile_dir), headless=True)
            )
            logger.info("Probe result: logged_in=%s profile_dir=%s", logged_in, profile_dir)
            return 0 if logged_in else 2

        if args.register_from_profile:
            if not args.skip_probe:
                logged_in = asyncio.run(
                    probe_whatsapp_web_session(str(profile_dir), headless=True)
                )
                if not logged_in:
                    logger.error(
                        "Profile folder exists but WhatsApp Web is not logged in. "
                        "Close Chrome and re-scan QR, then run register again "
                        "(or use --skip-probe if QR was scanned and chats are loading)."
                    )
                    return 2
            return _store_linked_session(db, account, profile_dir)

        logged_in = asyncio.run(
            _wait_for_qr_login(
                str(profile_dir),
                timeout_seconds=args.timeout_seconds,
                poll_interval_seconds=3.0,
            )
        )
        if not logged_in:
            logger.error("QR login timed out after %s seconds.", args.timeout_seconds)
            return 2

        from core_engine.services.whatsapp_web_session import profile_dir_has_browser_data

        if not profile_dir_has_browser_data(profile_dir):
            logger.error(
                "Login detected but browser profile was not saved under %s. "
                "Re-run the link command and keep the browser window open a few seconds.",
                profile_dir,
            )
            return 3

        return _store_linked_session(db, account, profile_dir)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
