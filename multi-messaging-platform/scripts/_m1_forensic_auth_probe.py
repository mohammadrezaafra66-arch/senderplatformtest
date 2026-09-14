#!/usr/bin/env python3
"""M1 dedicated Rubika auth/identity probe — memory-only, non-persisting.

Allowed network:
- client.connect (load in-memory StringSession credentials)
- get_me / get_user_info / get_abs_objects (identity read)

Forbidden:
- session.save / SQLiteSession file paths
- send_message / send_code / sign_in / logout
- DB writes / Redis writes / OTP / LoginChallenge
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

PROOF_PASS = "PROOF_PASS"
AUTH_RECONNECT_FAILED = "AUTH_RECONNECT_FAILED"
AUTH_RECONNECT_TIMEOUT = "AUTH_RECONNECT_TIMEOUT"
SESSION_STRUCTURALLY_INVALID = "SESSION_STRUCTURALLY_INVALID"
IDENTITY_MISSING = "IDENTITY_MISSING"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"

_FORBIDDEN_CLIENT_METHODS = frozenset(
    {
        "send_message",
        "send_code",
        "sign_in",
        "logout",
        "logout_all",
        "add_address_book",
        "join_group",
        "leave_group",
    }
)

_M1_NETWORK_CALLS = (
    "rubpy.Client.connect",
    "rubpy.Client.get_me",
    "rubpy.Client.get_user_info",
    "rubpy.Client.get_abs_objects",
    "rubpy.Client.disconnect",
)


def m1_network_calls() -> tuple[str, ...]:
    return _M1_NETWORK_CALLS


def _sanitize(text: str | None, *, max_len: int = 120) -> str:
    if not text:
        return ""
    msg = str(text)
    msg = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+", "<redacted_url>", msg)
    msg = re.sub(r"(?i)\b[a-f0-9]{24,}\b", "<redacted_hex>", msg)
    msg = re.sub(r"(?i)\b[A-Za-z0-9_-]{40,}\b", "<redacted_token>", msg)
    msg = re.sub(r"\+?\d[\d\s\-()]{7,}\d", "<redacted_phone>", msg)
    return re.sub(r"\s+", " ", msg).strip()[:max_len]


def _extract_guid(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        for key in ("user_guid", "guid"):
            val = str(obj.get(key) or "").strip()
            if val:
                return val
        user = obj.get("user")
        if isinstance(user, dict):
            return _extract_guid(user)
        return None
    for attr in ("user_guid", "guid"):
        val = str(getattr(obj, attr, None) or "").strip()
        if val:
            return val
    to_dict = getattr(obj, "to_dict", None)
    data = to_dict if isinstance(to_dict, dict) else (to_dict() if callable(to_dict) else None)
    if isinstance(data, dict):
        return _extract_guid(data)
    user = getattr(obj, "user", None)
    if user is not None:
        return _extract_guid(user)
    return None


async def _identity_probe(client: Any) -> tuple[str, str | None]:
    stored = str(getattr(client, "guid", "") or "").strip() or None
    probes: list[tuple[str, Any]] = []
    if callable(getattr(client, "get_me", None)):
        probes.append(("get_me", client.get_me))
    if stored and callable(getattr(client, "get_user_info", None)):
        probes.append(("get_user_info", lambda: client.get_user_info(stored)))
    if stored and callable(getattr(client, "get_abs_objects", None)):
        probes.append(("get_abs_objects", lambda: client.get_abs_objects([stored])))

    last_err: Exception | None = None
    for name, call in probes:
        try:
            result = await call()
            guid = _extract_guid(result)
            if guid:
                return name, guid
            if stored:
                return name, stored
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            msg = str(exc).lower()
            if any(
                tok in msg
                for tok in (
                    "otp",
                    "login",
                    "sign in",
                    "unauthorized",
                    "invalid auth",
                    "session expired",
                    "requires login",
                    "pass_key",
                )
            ):
                raise
            continue
    if last_err is not None and stored is None:
        raise last_err
    return "connect_only", stored


async def _build_memory_client(envelope: dict[str, str]) -> Any:
    from rubpy import Client
    from rubpy.sessions import StringSession

    # StringSession keeps credentials in-process only (no SQLiteSession file).
    string_session = StringSession()
    string_session.session = [
        envelope["phone_number"],
        envelope["auth"],
        envelope["guid"],
        envelope["user_agent"],
        envelope["private_key"],
    ]
    client = Client(name=string_session, display_welcome=False)
    # Hard refuse accidental mutation methods if ever invoked.
    for method in _FORBIDDEN_CLIENT_METHODS:
        if hasattr(client, method):
            setattr(
                client,
                method,
                lambda *a, _m=method, **k: (_ for _ in ()).throw(
                    RuntimeError(f"M1_FORBIDDEN_CLIENT_METHOD:{_m}")
                ),
            )
    # Refuse session file persistence.
    sess = getattr(client, "session", None)
    if sess is not None and callable(getattr(sess, "save", None)):
        sess.save = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("M1_FORBIDDEN_SESSION_SAVE")
        )
    return client


async def _connect_authenticated(client: Any) -> None:
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    from rubpy.crypto import Crypto as RubikaCrypto

    await client.connect()
    client.decode_auth = RubikaCrypto.decode_auth(client.auth) if client.auth else None
    client.import_key = (
        pkcs1_15.new(RSA.import_key(client.private_key.encode()))
        if client.private_key
        else None
    )


def _compare_identity(
    proven_guid: str | None,
    *,
    expected_guid: str | None,
    account_bound_guid: str | None,
    account_identity_status: str | None,
) -> tuple[bool | None, str | None, str | None]:
    cleaned = str(proven_guid or "").strip()
    if not cleaned:
        return False, None, IDENTITY_MISSING

    expected = str(expected_guid or "").strip() or None
    if expected and cleaned != expected:
        return False, cleaned, IDENTITY_MISMATCH

    bound = str(account_bound_guid or "").strip() or None
    status = str(account_identity_status or "").strip().lower() or None
    if bound is None or status in {None, "unbound", ""}:
        return True, cleaned, None
    if bound != cleaned:
        return False, cleaned, IDENTITY_MISMATCH
    return True, cleaned, None


async def forensic_auth_and_identity(
    envelope: dict[str, str],
    *,
    expected_guid: str | None,
    account_bound_guid: str | None,
    account_identity_status: str | None,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Authenticate using an in-memory envelope copy. Never persists anything."""
    started = time.perf_counter()
    client: Any | None = None
    out: dict[str, Any] = {
        "auth_status": "AUTH_INDETERMINATE",
        "reason_code": None,
        "identity_guid": None,
        "identity_match": None,
        "probe_method": None,
        "duration_ms": 0,
        "sanitized_message": "",
        "persists_session": False,
        "requests_otp": False,
        "writes_redis": False,
        "writes_db": False,
        "network_calls": list(_M1_NETWORK_CALLS),
    }
    try:

        async def _run() -> None:
            nonlocal client
            client = await _build_memory_client(envelope)
            await _connect_authenticated(client)
            method, proven = await _identity_probe(client)
            out["probe_method"] = method
            matched, identity_guid, fail_code = _compare_identity(
                proven,
                expected_guid=expected_guid,
                account_bound_guid=account_bound_guid,
                account_identity_status=account_identity_status,
            )
            out["identity_guid"] = identity_guid
            out["identity_match"] = matched
            if not matched:
                out["auth_status"] = "AUTH_FAIL"
                out["reason_code"] = fail_code or IDENTITY_MISMATCH
                out["sanitized_message"] = _sanitize(fail_code or IDENTITY_MISMATCH)
                return
            out["auth_status"] = "AUTH_PASS"
            out["reason_code"] = PROOF_PASS
            out["sanitized_message"] = "authenticated reconnect and identity verified"

        await asyncio.wait_for(_run(), timeout=float(timeout_seconds))
    except asyncio.TimeoutError:
        out["auth_status"] = "AUTH_FAIL"
        out["reason_code"] = AUTH_RECONNECT_TIMEOUT
        out["sanitized_message"] = "reconnect proof timed out"
    except Exception as exc:  # noqa: BLE001
        out["auth_status"] = "AUTH_FAIL"
        out["reason_code"] = AUTH_RECONNECT_FAILED
        out["sanitized_message"] = _sanitize(type(exc).__name__)
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        out["duration_ms"] = int((time.perf_counter() - started) * 1000)
        # Discard any mutated in-memory client state by dropping references.
        client = None
    return out
