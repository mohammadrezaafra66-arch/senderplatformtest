"""WhatsApp Web session metadata and browser profile paths (Playwright persistent context)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import ChannelSession, SessionType
from core_engine.services.session_storage import (
    load_channel_session_plaintext,
    store_channel_session,
)

WHATSAPP_WEB_METADATA_VERSION = 1


@dataclass(frozen=True, slots=True)
class WhatsAppWebSessionMetadata:
    account_id: int
    profile_dir: str
    linked: bool
    phone: str | None = None
    linked_at: str | None = None
    version: int = WHATSAPP_WEB_METADATA_VERSION


def resolve_whatsapp_profile_dir(
    account_id: int,
    *,
    profile_root: str | None = None,
) -> Path:
    """Return the on-disk Playwright userDataDir for a WhatsApp account."""
    root = Path(profile_root or get_settings().WHATSAPP_WEB_PROFILE_ROOT)
    return root / f"account-{account_id}"


def profile_dir_has_browser_data(profile_dir: str | Path) -> bool:
    """True when a persistent Chromium profile directory looks initialized."""
    path = Path(profile_dir)
    if not path.is_dir():
        return False
    markers = (
        path / "Default",
        path / "Local State",
        path / "First Run",
    )
    return any(marker.exists() for marker in markers)


def default_windows_host_profile_dir(account_id: int) -> Path:
    """Local Chrome profile used by whatsapp_web_link_local.ps1 on Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return Path()
    return Path(local_app_data) / "SenderPlatform" / "mmp-whatsapp" / f"account-{account_id}"


def resolve_whatsapp_runtime_profile_dir(
    account_id: int,
    stored_profile_dir: str | Path | None = None,
    *,
    profile_root: str | None = None,
) -> str:
    """Return the first usable browser profile directory for the current runtime."""
    host_profile = default_windows_host_profile_dir(account_id)
    candidates: list[Path] = []
    # On Windows the live Chrome profile lives under LOCALAPPDATA; the Docker
    # copy under storage/ is often stale after whatsapp_web_link_local.ps1.
    if os.name == "nt" and host_profile.parts:
        candidates.append(host_profile)
    if stored_profile_dir:
        candidates.append(Path(stored_profile_dir))
    candidates.append(resolve_whatsapp_profile_dir(account_id, profile_root=profile_root))
    if os.name != "nt" and host_profile.parts:
        candidates.append(host_profile)

    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if profile_dir_has_browser_data(candidate):
            resolved = candidate.resolve()
            return str(resolved) if os.name == "nt" else resolved.as_posix()

    fallback = candidates[0] if candidates else resolve_whatsapp_profile_dir(account_id)
    resolved = fallback.resolve()
    return str(resolved) if os.name == "nt" else resolved.as_posix()


def _metadata_to_json(metadata: WhatsAppWebSessionMetadata) -> str:
    payload = {
        "version": metadata.version,
        "profile_dir": metadata.profile_dir,
        "linked": metadata.linked,
        "phone": metadata.phone,
        "linked_at": metadata.linked_at,
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_whatsapp_web_metadata(plaintext: bytes) -> WhatsAppWebSessionMetadata:
    """Parse encrypted session JSON stored for BROWSER_PROFILE sessions."""
    text = plaintext.decode("utf-8").strip()
    if not text:
        raise ValueError("WhatsApp Web session metadata is empty.")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("WhatsApp Web session metadata is not valid JSON.") from exc

    if not isinstance(data, dict):
        raise ValueError("WhatsApp Web session metadata must be a JSON object.")

    profile_dir = str(data.get("profile_dir") or "").strip()
    if not profile_dir:
        raise ValueError("WhatsApp Web session metadata is missing profile_dir.")

    linked = bool(data.get("linked"))
    phone = data.get("phone")
    phone_str = str(phone).strip() if phone is not None and str(phone).strip() else None
    linked_at = data.get("linked_at")
    linked_at_str = (
        str(linked_at).strip() if linked_at is not None and str(linked_at).strip() else None
    )
    version = int(data.get("version") or WHATSAPP_WEB_METADATA_VERSION)

    return WhatsAppWebSessionMetadata(
        account_id=0,
        profile_dir=profile_dir,
        linked=linked,
        phone=phone_str,
        linked_at=linked_at_str,
        version=version,
    )


def load_whatsapp_web_session(
    db: Session,
    account_id: int,
) -> WhatsAppWebSessionMetadata | None:
    """Load the latest WhatsApp Web browser-profile session for an account."""
    row = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == SessionType.BROWSER_PROFILE,
        )
        .order_by(ChannelSession.id.desc())
        .first()
    )
    if row is None or not row.ciphertext:
        return None

    plaintext = load_channel_session_plaintext(row)
    metadata = parse_whatsapp_web_metadata(plaintext)
    profile_dir = row.file_path or metadata.profile_dir
    return WhatsAppWebSessionMetadata(
        account_id=account_id,
        profile_dir=profile_dir,
        linked=metadata.linked,
        phone=metadata.phone,
        linked_at=metadata.linked_at,
        version=metadata.version,
    )


def store_whatsapp_web_session(
    db: Session,
    *,
    account_id: int,
    linked: bool,
    phone: str | None = None,
    profile_dir: str | Path | None = None,
) -> ChannelSession:
    """Persist WhatsApp Web session metadata (browser files live on disk separately)."""
    resolved_profile = Path(
        profile_dir
        if profile_dir is not None
        else resolve_whatsapp_profile_dir(account_id)
    )
    # Always store POSIX-style paths so Linux containers resolve the mounted volume.
    profile_path = resolved_profile.as_posix()
    linked_at = datetime.now(timezone.utc).isoformat() if linked else None
    metadata = WhatsAppWebSessionMetadata(
        account_id=account_id,
        profile_dir=profile_path,
        linked=linked,
        phone=phone.strip() if phone and phone.strip() else None,
        linked_at=linked_at,
    )
    row = store_channel_session(
        db,
        account_id=account_id,
        session_type=SessionType.BROWSER_PROFILE,
        plaintext=_metadata_to_json(metadata),
    )
    row.file_path = profile_path
    row.last_refresh_at = datetime.utcnow()
    db.flush()
    return row


def build_whatsapp_web_status(
    db: Session,
    account_id: int,
    *,
    profile_root: str | None = None,
) -> dict[str, object]:
    """Summarize WhatsApp Web linkage for API responses (no live browser probe)."""
    profile_dir = resolve_whatsapp_profile_dir(account_id, profile_root=profile_root)
    session = load_whatsapp_web_session(db, account_id)
    profile_exists = profile_dir_has_browser_data(profile_dir)
    session_registered = session is not None
    linked = bool(session and session.linked)
    needs_qr = not (profile_exists and linked)

    message = "WhatsApp Web session is linked and ready."
    if not profile_exists and not session_registered:
        message = "No browser profile yet. Run whatsapp_web_link to scan QR."
    elif profile_exists and not linked:
        message = "Browser profile exists but session is not marked linked."
    elif not profile_exists and session_registered:
        message = "Session metadata exists but browser profile directory is missing."

    return {
        "account_id": account_id,
        "delivery_mode": "web",
        "profile_dir": str(profile_dir),
        "profile_exists": profile_exists,
        "session_registered": session_registered,
        "linked": linked,
        "needs_qr": needs_qr,
        "phone": session.phone if session else None,
        "linked_at": session.linked_at if session else None,
        "message": message,
    }
