#!/usr/bin/env python3
"""Phase 9.1 — E2E WhatsApp send via DB staging + queue_bridge + worker pool.

Stages a test message in Postgres, pushes to ``queue:whatsapp:{account_id}`` through
``queue_bridge``, then polls ``message_attempts`` until success or failure.

Usage (from repo root, with Docker postgres/redis/workers running):

    # On Windows host — point DB/Redis at localhost:
    set DATABASE_URL=postgresql://mmp_user:mmp_pass@localhost:5432/mmp_db
    set REDIS_URL=redis://localhost:6379/0
    python scripts/e2e_whatsapp_real_send.py --account-id 248 --recipient 0912xxxxxxx

Or inside core_api container:

    docker compose run --rm core_api python scripts/e2e_whatsapp_real_send.py --account-id 248
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow ``python scripts/e2e_whatsapp_real_send.py`` without installing the package.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.database import SessionLocal
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    ConsentStatus,
    Contact,
    Message,
    MessageAttempt,
    MessageAttemptStatus,
    PlatformType,
    RenderStatus,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.queue_bridge import push_staged_items_to_worker_queue
from core_engine.services.redis_client import get_redis_client, ping_redis
from core_engine.services.whatsapp_web_session import (
    profile_dir_has_browser_data,
    resolve_whatsapp_profile_dir,
)
from core_engine.services.worker_pool_status import (
    account_covered_by_pool,
    list_whatsapp_pool_workers,
)
from workers.redis_keys import queue_key

DEFAULT_MESSAGE = "تست ارسالِ واقعی از داکر"
DEFAULT_ACCOUNT_ID = 248
POLL_INTERVAL_SECONDS = 2.0

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[36m"
RESET = "\033[0m"


@dataclass(frozen=True, slots=True)
class ConnectivityResult:
    ok: bool
    checks: list[tuple[str, bool, str]]


@dataclass(frozen=True, slots=True)
class SeededE2E:
    campaign_id: int
    contact_id: int
    message_id: int
    staged_item_id: int
    dedupe_key: str


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{code}{text}{RESET}"


def _host_local_urls() -> None:
    """When running on the host, rewrite Docker service hostnames to localhost."""
    if Path("/.dockerenv").exists():
        return
    db = os.environ.get("DATABASE_URL", "")
    if "@postgres:" in db:
        os.environ["DATABASE_URL"] = db.replace(
            "@postgres:5432", "@127.0.0.1:5433"
        ).replace("@postgres:", "@127.0.0.1:5433/")
    redis = os.environ.get("REDIS_URL", "")
    if "redis://redis:" in redis:
        os.environ["REDIS_URL"] = redis.replace("redis://redis:", "redis://localhost:")
    get_settings.cache_clear()


def _require_live_env() -> list[str]:
    settings = get_settings()
    errors: list[str] = []
    if not settings.REAL_QUEUE_PUSH_ENABLED:
        errors.append("REAL_QUEUE_PUSH_ENABLED must be true for queue_bridge push.")
    if not settings.REAL_MESSAGE_SENDING_ENABLED:
        errors.append("REAL_MESSAGE_SENDING_ENABLED must be true for live worker delivery.")
    if not settings.CHANNEL_CONNECTORS_ENABLED:
        errors.append("CHANNEL_CONNECTORS_ENABLED must be true.")
    if settings.DRY_RUN:
        errors.append("DRY_RUN must be false.")
    return errors


async def check_whatsapp_worker_pool_connectivity(
    account_id: int,
) -> ConnectivityResult:
    """Verify Redis is reachable and a live pool worker covers ``account_id``."""
    checks: list[tuple[str, bool, str]] = []

    redis_ok = await ping_redis()
    checks.append(
        (
            "redis_ping",
            redis_ok,
            "Redis PING ok." if redis_ok else "Cannot reach Redis (check REDIS_URL / mmp_redis).",
        )
    )

    workers: list[dict[str, Any]] = []
    if redis_ok:
        redis = get_redis_client()
        workers = await list_whatsapp_pool_workers(redis)
        pool_ok = len(workers) > 0
        checks.append(
            (
                "whatsapp_worker_pool_heartbeat",
                pool_ok,
                (
                    f"{len(workers)} pool replica(s) reporting heartbeat."
                    if pool_ok
                    else "No whatsapp_worker_pool heartbeat — run: docker compose up -d whatsapp_worker_pool"
                ),
            )
        )
        covered = account_covered_by_pool(account_id, workers)
        checks.append(
            (
                "account_assigned_to_pool",
                covered,
                (
                    f"Account {account_id} is assigned to a live pool worker."
                    if covered
                    else (
                        f"Account {account_id} not in WHATSAPP_ACCOUNT_IDS — "
                        f"set WHATSAPP_ACCOUNT_IDS={account_id} and restart pool."
                    )
                ),
            )
        )
    else:
        checks.append(("whatsapp_worker_pool_heartbeat", False, "Skipped (Redis down)."))
        checks.append(("account_assigned_to_pool", False, "Skipped (Redis down)."))

    ok = all(item[1] for item in checks)
    return ConnectivityResult(ok=ok, checks=checks)


def check_browser_profile_volume(account_id: int) -> ConnectivityResult:
    """Ensure the linked Windows profile is visible at the mounted storage path."""
    settings = get_settings()
    profile_dir = resolve_whatsapp_profile_dir(account_id, profile_root=settings.WHATSAPP_WEB_PROFILE_ROOT)
    abs_path = profile_dir.resolve()
    exists = profile_dir.is_dir()
    has_data = profile_dir_has_browser_data(profile_dir) if exists else False

    checks = [
        (
            "profile_directory_exists",
            exists,
            f"Profile dir exists: {abs_path}" if exists else f"Missing profile dir: {abs_path}",
        ),
        (
            "profile_browser_data",
            has_data,
            (
                "Chromium profile data present (QR link succeeded)."
                if has_data
                else "Profile folder empty — re-run whatsapp_web_link_local.ps1 on Windows."
            ),
        ),
        (
            "docker_volume_path",
            True,
            (
                "Docker mount: ./storage/browser_profiles -> /app/storage/browser_profiles "
                f"(account-{account_id} must exist under storage/browser_profiles/whatsapp/)."
            ),
        ),
    ]
    ok = exists and has_data
    return ConnectivityResult(ok=ok, checks=checks)


def _sole_active_whatsapp_account(db: Session, account_id: int) -> tuple[bool, str]:
    active = (
        db.query(Account)
        .filter(
            Account.platform == PlatformType.WHATSAPP,
            Account.status == AccountStatus.ACTIVE,
        )
        .order_by(Account.id.asc())
        .all()
    )
    ids = [row.id for row in active]
    if account_id not in ids:
        return False, f"Account {account_id} is not an ACTIVE WhatsApp account."
    if len(ids) > 1:
        return (
            False,
            f"Multiple active WhatsApp accounts {ids} — queue_bridge round-robin may not pick "
            f"{account_id}. Deactivate others or keep only {account_id} ACTIVE.",
        )
    return True, f"Account {account_id} is the sole active WhatsApp sender."


def seed_e2e_bundle(
    db: Session,
    *,
    account_id: int,
    recipient_phone: str,
    message_text: str,
) -> SeededE2E:
    account = db.get(Account, account_id)
    if account is None:
        raise SystemExit(f"Account {account_id} not found.")
    if account.platform != PlatformType.WHATSAPP:
        raise SystemExit(f"Account {account_id} is not WhatsApp (platform={account.platform}).")

    sole_ok, sole_msg = _sole_active_whatsapp_account(db, account_id)
    if not sole_ok:
        raise SystemExit(sole_msg)
    print(_color(f"OK  {sole_msg}", GREEN))

    test_tag = uuid.uuid4().hex[:10]
    dedupe_key = f"e2e-wa-{account_id}-{test_tag}"

    campaign = Campaign(
        name=f"e2e-whatsapp-{test_tag}",
        channel="whatsapp",
        title=f"E2E WhatsApp {test_tag}",
        platform=PlatformType.WHATSAPP,
        status=CampaignStatus.RUNNING.value,
        intent="e2e_operational_test",
    )
    db.add(campaign)
    db.flush()

    phone_suffix = test_tag[-8:]
    contact = Contact(
        campaign_id=campaign.id,
        phone=recipient_phone,
        phone_e164=None,
        consent_status=ConsentStatus.ALLOWED.value,
        blacklisted=False,
    )
    db.add(contact)
    db.flush()

    message = Message(
        campaign_id=campaign.id,
        account_id=account_id,
        contact_id=contact.id,
        rendered_text=message_text,
        dedupe_key=dedupe_key,
    )
    db.add(message)
    db.flush()

    recipient = CampaignRecipient(
        campaign_id=campaign.id,
        contact_id=contact.id,
        render_status=RenderStatus.RENDERED,
        send_status=SendStatus.QUEUED,
        final_message_id=message.id,
    )
    db.add(recipient)

    queue_payload: dict[str, Any] = {
        "message_id": message.id,
        "campaign_id": campaign.id,
        "contact_id": contact.id,
        "channel": "whatsapp",
        "platform": "whatsapp",
        "final_text": message_text,
        "message_text": message_text,
        "phone": recipient_phone,
        "account_id": account_id,
        "attempt": 1,
        "dedupe_key": dedupe_key,
        "metadata": {
            "source": "e2e_whatsapp_real_send",
            "customer_phone": recipient_phone,
        },
    }

    staged = StagedQueueItem(
        campaign_id=campaign.id,
        contact_id=contact.id,
        channel="whatsapp",
        status=StagedQueueItemStatus.READY.value,
        final_text=message_text,
        queue_payload=queue_payload,
    )
    db.add(staged)
    db.commit()

    return SeededE2E(
        campaign_id=campaign.id,
        contact_id=contact.id,
        message_id=message.id,
        staged_item_id=staged.id,
        dedupe_key=dedupe_key,
    )


async def push_via_queue_bridge(db: Session, account_id: int) -> dict[str, int]:
    stats = await push_staged_items_to_worker_queue(db, batch_size=10)
    if stats.get("pushed", 0) < 1:
        raise SystemExit(
            f"queue_bridge pushed 0 items (stats={stats}). "
            "Is REAL_QUEUE_PUSH_ENABLED=true?"
        )

    redis = get_redis_client()
    key = queue_key("whatsapp", account_id)
    depth = await redis.llen(key)
    if not depth:
        raise SystemExit(
            f"Redis queue {key} is empty after bridge push — "
            "account assignment may have targeted a different WhatsApp account."
        )

    raw = await redis.lindex(key, -1)
    if raw:
        payload = json.loads(raw)
        pushed_account = int(payload.get("account_id") or 0)
        if pushed_account != account_id:
            raise SystemExit(
                f"Queue payload account_id={pushed_account}, expected {account_id}."
            )

    print(_color(f"OK  Pushed to {key} (depth={depth})", GREEN))
    return stats


def _attempt_is_success(status: MessageAttemptStatus) -> bool:
    return status in (MessageAttemptStatus.SUCCESS, MessageAttemptStatus.SHADOW_SENT)


def _attempt_is_failure(status: MessageAttemptStatus) -> bool:
    return status in (
        MessageAttemptStatus.FAILED_RETRYABLE,
        MessageAttemptStatus.FAILED_PERMANENT,
    )


def monitor_message_attempts(
    db_factory,
    message_id: int,
    *,
    timeout_seconds: float,
) -> int:
    """Poll message_attempts until terminal state. Returns 0 on success, 1 on failure."""
    deadline = time.monotonic() + timeout_seconds
    last_status: str | None = None

    while time.monotonic() < deadline:
        db = db_factory()
        try:
            attempts = (
                db.query(MessageAttempt)
                .filter(MessageAttempt.message_id == message_id)
                .order_by(MessageAttempt.id.desc())
                .all()
            )
            if attempts:
                latest = attempts[0]
                status = latest.status
                if status != last_status:
                    print(
                        f"{CYAN}[monitor]{RESET} message_id={message_id} "
                        f"attempt #{latest.attempt_no} status={status.value}"
                    )
                    last_status = status.value

                if _attempt_is_success(status):
                    print(_color("ارسال با موفقیت انجام شد", GREEN))
                    if latest.platform_message_id:
                        print(f"  platform_message_id: {latest.platform_message_id}")
                    if latest.accepted_at:
                        print(f"  accepted_at: {latest.accepted_at.isoformat()}")
                    return 0

                if _attempt_is_failure(status):
                    print(_color("ارسال ناموفق (FAILED)", RED))
                    print(f"  error_code: {latest.error_code or '—'}")
                    print(f"  error_message: {latest.error_message or '—'}")
                    return 1
        finally:
            db.close()

        time.sleep(POLL_INTERVAL_SECONDS)

    print(_color(f"Timeout after {timeout_seconds}s — no terminal MessageAttempt.", RED))
    return 1


def _print_connectivity(title: str, result: ConnectivityResult) -> bool:
    print(f"\n=== {title} ===")
    for key, passed, message in result.checks:
        label = "OK  " if passed else "FAIL"
        color = GREEN if passed else RED
        print(_color(f"{label} {key}: {message}", color))
    return result.ok


async def async_main(args: argparse.Namespace) -> int:
    _host_local_urls()
    get_settings.cache_clear()
    settings = get_settings()

    print(f"E2E WhatsApp real send — account_id={args.account_id}")
    print(f"  DATABASE_URL: {settings.DATABASE_URL}")
    print(f"  REDIS_URL: {settings.REDIS_URL}")
    print(f"  WHATSAPP_DELIVERY_MODE: {settings.WHATSAPP_DELIVERY_MODE}")

    env_errors = _require_live_env()
    if env_errors:
        for err in env_errors:
            print(_color(f"FAIL env: {err}", RED))
        return 1

    redis_result = await check_whatsapp_worker_pool_connectivity(args.account_id)
    if not _print_connectivity("Connectivity — whatsapp_worker_pool / Redis", redis_result):
        return 1

    profile_result = check_browser_profile_volume(args.account_id)
    if not _print_connectivity("Connectivity — browser profile volume", profile_result):
        return 1

    db = SessionLocal()
    try:
        account = db.get(Account, args.account_id)
        if account is None:
            print(_color(f"Account {args.account_id} not found.", RED))
            return 1

        recipient = (args.recipient or account.phone_number or "").strip()
        if not recipient:
            print(
                _color(
                    "Missing --recipient and account has no phone_number.",
                    RED,
                )
            )
            return 1

        message_text = (args.message or DEFAULT_MESSAGE).strip()
        print(f"\n=== Seed DB + stage queue item ===")
        print(f"  recipient: {recipient}")
        print(f"  message: {message_text}")

        bundle = seed_e2e_bundle(
            db,
            account_id=args.account_id,
            recipient_phone=recipient,
            message_text=message_text,
        )
        print(
            _color(
                f"OK  campaign={bundle.campaign_id} message={bundle.message_id} "
                f"staged_item={bundle.staged_item_id}",
                GREEN,
            )
        )
    finally:
        db.close()

    print(f"\n=== queue_bridge push ===")
    db = SessionLocal()
    try:
        await push_via_queue_bridge(db, args.account_id)
    finally:
        db.close()

    print(f"\n=== Monitor MessageAttempts (timeout={args.timeout}s) ===")
    exit_code = monitor_message_attempts(
        SessionLocal,
        bundle.message_id,
        timeout_seconds=float(args.timeout),
    )
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="E2E WhatsApp send via queue_bridge (Phase 9.1).",
    )
    parser.add_argument(
        "--account-id",
        type=int,
        default=DEFAULT_ACCOUNT_ID,
        help=f"WhatsApp account id (default: {DEFAULT_ACCOUNT_ID}).",
    )
    parser.add_argument(
        "--recipient",
        type=str,
        default=None,
        help="Recipient phone (defaults to account.phone_number).",
    )
    parser.add_argument(
        "--message",
        type=str,
        default=DEFAULT_MESSAGE,
        help="Test message body.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Seconds to wait for MessageAttempt terminal status.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
