"""One-shot WhatsApp Web send test (run while worker is STOPPED)."""
from __future__ import annotations

import asyncio
import os
import sys

from workers.errors import PermanentWorkerError, RetryableWorkerError
from workers.whatsapp_web.playwright_sender import send_whatsapp_web_message


async def main() -> int:
    recipient = sys.argv[1] if len(sys.argv) > 1 else "989122270261"
    text = sys.argv[2] if len(sys.argv) > 2 else "TEST123 simple ascii"
    headless = os.environ.get("WHATSAPP_WEB_HEADLESS", "false").lower() == "true"
    profile = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "SenderPlatform",
        "mmp-whatsapp",
        "account-248",
    )
    print(f"profile={profile}")
    print(f"recipient={recipient} headless={headless}")
    try:
        result = await send_whatsapp_web_message(
            profile,
            recipient,
            text,
            headless=headless,
            timeout_ms=120_000,
            account_id=248,
            source="script",
            message_id=f"script-test-{recipient[-4:]}",
        )
        print("SUCCESS", result)
        return 0
    except (PermanentWorkerError, RetryableWorkerError) as exc:
        print("FAILED", type(exc).__name__, exc)
        return 1
    except Exception as exc:
        print("ERROR", type(exc).__name__, exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
