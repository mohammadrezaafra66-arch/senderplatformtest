"""API مدیریت اکانت‌های پیام‌رسان."""

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core_engine.config import get_settings

from core_engine.api.schemas import (
    AccountCreateRequest,
    AccountCreateResponse,
    AccountResponse,
    AccountRuntimeAuthBlock,
    AccountRuntimeBlock,
    AccountRuntimeCredentialBlock,
    AccountRuntimeDispatchBlock,
    AccountRuntimeIdentityBlock,
    AccountRuntimeOperatorActionBlock,
    AccountRuntimeWorkerBlock,
    AccountsListResponse,
    AccountSessionRegisterRequest,
    AccountSessionRegisterResponse,
    AccountSessionStatusResponse,
    AccountSendTestRequest,
    AccountSendTestResponse,
    ArchiveActionRequest,
    ArchiveActionResponse,
    LiveSendPreflightCheckItem,
    RubikaUserLoginStartRequest,
    RubikaUserLoginStartResponse,
    RubikaUserLoginVerifyRequest,
    RubikaUserLoginVerifyResponse,
    LiveSendPreflightResponse,
    AccountTestConnectionRequest,
    AccountTestConnectionResponse,
    AccountUpdateRequest,
    DeployReadinessResponse,
    WhatsAppWebRegisterRequest,
    WhatsAppWebRegisterResponse,
    WhatsAppWebPoolStatusResponse,
    WhatsAppWebStatusResponse,
)
from core_engine.services.redis_client import get_redis_client
from core_engine.database import get_db
from core_engine.models import Account, AccountStatus, PlatformType, RoleType, SessionType
from core_engine.services.audit_service import record_audit
from core_engine.services.account_session_wiring import (
    build_account_session_status,
    build_deploy_readiness,
    evaluate_account_session_readiness,
    register_api_token_session,
    required_session_type,
    resolve_whatsapp_delivery_mode,
)
from core_engine.services.account_runtime_status import (
    compute_all_account_runtime_statuses,
    run_connection_test,
)
from core_engine.services.campaign_sender_eligibility import (
    evaluate_campaign_sender_eligibility,
    resolve_display_identity,
)
from core_engine.services.operational_send import (
    OperationalSendError,
    build_live_send_preflight,
    operational_send_capabilities,
    send_account_test_message,
)
from core_engine.services.rbac import requires_role
from core_engine.services.whatsapp_web_session import (
    build_whatsapp_web_status,
    resolve_whatsapp_profile_dir,
    store_whatsapp_web_session,
)
from core_engine.services.evolution_service import _instance_name
from core_engine.services.worker_pool_status import list_whatsapp_pool_workers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _rubika_active_allowed(db: Session, account: Account) -> bool:
    from core_engine.services.rubika_account_lifecycle import rubika_active_transition_allowed

    return rubika_active_transition_allowed(db, account)


def _login_http_error(code: str, *, retry_after_seconds: int | None = None, state: str | None = None) -> HTTPException:
    from core_engine.services.rubika_account_lifecycle import operator_message

    detail: dict[str, object] = {"code": code, "message": operator_message(code)}
    if retry_after_seconds is not None:
        detail["retry_after_seconds"] = int(retry_after_seconds)
    if state:
        detail["state"] = state
    return HTTPException(status_code=400, detail=detail)


def _create_evolution_instance(instance_name: str) -> None:
    settings = get_settings()
    evolution_url = settings.EVOLUTION_API_URL.rstrip("/")
    evolution_key = settings.EVOLUTION_API_KEY
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{evolution_url}/instance/create",
                headers={"apikey": evolution_key, "Content-Type": "application/json"},
                json={"instanceName": instance_name, "integration": "WHATSAPP-BAILEYS"},
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "Evolution instance create failed: %s %s",
                    resp.status_code,
                    resp.text,
                )
    except Exception as exc:
        logger.warning("Evolution instance create error: %s", exc)


def _runtime_to_block(runtime) -> AccountRuntimeBlock:
    return AccountRuntimeBlock(
        runtime_status=runtime.runtime_status,
        runtime_status_label=runtime.runtime_status_label,
        enabled=runtime.enabled,
        auth=AccountRuntimeAuthBlock(state=runtime.auth_state, reason=runtime.auth_reason),
        credential=AccountRuntimeCredentialBlock(
            type=runtime.credential_type, state=runtime.credential_state
        ),
        identity=AccountRuntimeIdentityBlock(state=runtime.identity_state),
        worker=AccountRuntimeWorkerBlock(
            state=runtime.worker_state,
            covered=runtime.worker_covered,
            heartbeat_fresh=runtime.worker_heartbeat_fresh,
        ),
        dispatch=AccountRuntimeDispatchBlock(
            ready=runtime.dispatch_ready, blocker=runtime.dispatch_blocker
        ),
        operator_action=AccountRuntimeOperatorActionBlock(
            code=runtime.operator_action_code, label=runtime.operator_action_label
        ),
        reason_code=runtime.reason_code,
        last_verified_at=runtime.last_verified_at,
    )


def _account_to_response(account: Account, runtime=None, db: Session | None = None) -> AccountResponse:
    block = _runtime_to_block(runtime) if runtime is not None else None
    # Base campaign eligibility without campaign/capacity context (picker parity).
    elig = None
    if runtime is not None:
        elig = evaluate_campaign_sender_eligibility(
            db,
            account,
            runtime=runtime,
        )
    display = resolve_display_identity(
        account_id=int(account.id),
        phone_number=account.phone_number,
        label=account.label,
    )
    return AccountResponse(
        id=account.id,
        platform=account.platform,
        account_identifier=account.phone_number,
        label=account.label,
        display_identity=display,
        status=account.status,
        proxy_url=account.proxy_url,
        policy_id=account.policy_id,
        created_at=account.created_at,
        updated_at=account.updated_at,
        last_used_at=account.last_used_at,
        archived_at=account.archived_at,
        archived_by=account.archived_by,
        archive_reason=account.archive_reason,
        runtime=block,
        runtime_status=runtime.runtime_status if runtime is not None else None,
        runtime_status_label=runtime.runtime_status_label if runtime is not None else None,
        account_enabled=runtime.enabled if runtime is not None else (account.status == AccountStatus.ACTIVE),
        campaign_eligible=elig.campaign_eligible if elig is not None else None,
        campaign_blocker_code=elig.blocker_code if elig is not None else None,
        campaign_blocker_label=elig.blocker_label if elig is not None else None,
        campaign_status_label=elig.runtime_status_label if elig is not None else None,
    )


def _find_duplicate_account(
    db: Session,
    platform: PlatformType,
    account_identifier: str,
    *,
    exclude_id: int | None = None,
) -> Account | None:
    query = db.query(Account).filter(
        Account.platform == platform,
        Account.phone_number == account_identifier,
    )
    if exclude_id is not None:
        query = query.filter(Account.id != exclude_id)
    return query.first()


@router.get("", response_model=AccountsListResponse)
def list_accounts(
    platform: PlatformType | None = None,
    archived: bool = False,
    q: str | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """لیست اکانت‌ها با فیلتر اختیاری platform + وضعیت runtime (L18).

    By default excludes archived accounts. Pass archived=true for the Archive list.
    """
    query = db.query(Account)
    if platform:
        query = query.filter(Account.platform == platform)
    if archived:
        query = query.filter(Account.archived_at.isnot(None))
    else:
        query = query.filter(Account.archived_at.is_(None))
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            (Account.phone_number.ilike(needle)) | (Account.label.ilike(needle))
        )

    accounts = query.order_by(Account.created_at.desc()).all()
    runtimes = compute_all_account_runtime_statuses(db, accounts)
    by_id = {r.account_id: r for r in runtimes}
    return AccountsListResponse(
        items=[_account_to_response(account, by_id.get(int(account.id)), db) for account in accounts],
        total_count=len(accounts),
    )


@router.post("", response_model=AccountCreateResponse, status_code=201)
def create_account(
    payload: AccountCreateRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """ایجاد اکانت جدید."""
    account_identifier = payload.account_identifier.strip()
    if not account_identifier:
        raise HTTPException(status_code=400, detail="account_identifier cannot be empty.")

    if _find_duplicate_account(db, payload.platform, account_identifier):
        raise HTTPException(
            status_code=409,
            detail="An account with this platform and account_identifier already exists.",
        )

    try:
        from core_engine.services.rubika_account_lifecycle import resolve_create_status

        create_status = resolve_create_status(payload.platform, payload.status)
        account = Account(
            platform=payload.platform,
            phone_number=account_identifier,
            label=payload.label,
            proxy_url=payload.proxy_url,
            status=create_status,
        )
        db.add(account)
        db.flush()

        record_audit(
            db,
            current_user["username"],
            "create_account",
            "account",
            str(account.id),
            {
                "platform": payload.platform.value,
                "account_identifier": account_identifier,
                "status": create_status.value,
            },
        )
        db.commit()
        db.refresh(account)

        if (
            account.platform == PlatformType.WHATSAPP
            and resolve_whatsapp_delivery_mode() == "evolution"
        ):
            _create_evolution_instance(_instance_name(account.id))

        return AccountCreateResponse(
            status="created",
            account_id=account.id,
            message="Account created successfully.",
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create account.: {str(exc)}"
        ) from exc


@router.post("/{account_id}/archive", response_model=ArchiveActionResponse)
def archive_account_endpoint(
    account_id: int,
    payload: ArchiveActionRequest | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Soft-archive an account. Idempotent. Does not delete session or history."""
    from core_engine.services.archive import archive_account

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    reason = payload.reason if payload else None
    result = archive_account(
        db,
        account,
        actor=current_user["username"],
        reason=reason,
    )
    db.commit()
    return ArchiveActionResponse(
        status="archived",
        entity_type=result.entity_type,
        entity_id=result.entity_id,
        already_archived=result.already_archived,
        archived_at=result.archived_at,
        previous_status=result.previous_status,
        queued_items_cancelled=result.queued_items_cancelled,
        message=(
            "Account already archived."
            if result.already_archived
            else "Account archived successfully."
        ),
        details=result.details,
    )


@router.post("/{account_id}/restore", response_model=ArchiveActionResponse)
def restore_account_endpoint(
    account_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Restore an archived account. Does not claim READY; runtime is recomputed."""
    from core_engine.services.archive import restore_account

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    result = restore_account(db, account, actor=current_user["username"])
    db.commit()
    db.refresh(account)
    runtime_status = None
    runtime_status_label = None
    try:
        runtime = compute_all_account_runtime_statuses(db, [account])
        rt = runtime[0] if runtime else None
        if rt is not None:
            runtime_status = rt.runtime_status
            runtime_status_label = rt.runtime_status_label
    except Exception:
        # Restore must succeed even if runtime evaluation is unavailable.
        pass
    return ArchiveActionResponse(
        status="restored",
        entity_type=result.entity_type,
        entity_id=result.entity_id,
        already_active=result.already_active,
        restored_at=result.restored_at,
        previous_status=result.previous_status,
        message=(
            "Account already active."
            if result.already_active
            else "Account restored from archive."
        ),
        details={
            **result.details,
            "runtime_status": runtime_status,
            "runtime_status_label": runtime_status_label,
        },
    )


@router.patch("/{account_id}", response_model=AccountResponse)
def update_account(
    account_id: int,
    payload: AccountUpdateRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """ویرایش اکانت (فعال/غیرفعال، شناسه، برچسب، پروکسی)."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    audit_details = dict(updates)

    new_identifier = updates.pop("account_identifier", None)
    if new_identifier is not None:
        new_identifier = new_identifier.strip()
        if not new_identifier:
            raise HTTPException(status_code=400, detail="account_identifier cannot be empty.")
        duplicate = _find_duplicate_account(
            db,
            account.platform,
            new_identifier,
            exclude_id=account.id,
        )
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail="An account with this platform and account_identifier already exists.",
            )
        account.phone_number = new_identifier

    if "label" in updates:
        account.label = updates["label"]
    if "proxy_url" in updates:
        account.proxy_url = updates["proxy_url"]
    if "status" in updates:
        next_status = updates["status"]
        if (
            account.platform == PlatformType.RUBIKA
            and next_status == AccountStatus.ACTIVE
            and not _rubika_active_allowed(db, account)
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "LOGIN_REQUIRED",
                    "message": "اکانت روبیکا تا تأیید ورود موفق نمی‌تواند فعال شود.",
                },
            )
        account.status = next_status

    try:
        record_audit(
            db,
            current_user["username"],
            "update_account",
            "account",
            str(account.id),
            audit_details,
        )
        db.commit()
        db.refresh(account)
        runtimes = compute_all_account_runtime_statuses(db, [account])
        return _account_to_response(account, runtimes[0] if runtimes else None, db)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update account.") from exc


@router.post("/{account_id}/test-connection", response_model=AccountTestConnectionResponse)
def test_account_connection(
    account_id: int,
    payload: AccountTestConnectionRequest | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """تست اتصال امن — بدون ارسال پیام، بدون درخواست OTP، بدون mutation غیرضروری سشن."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    body = payload or AccountTestConnectionRequest()
    result = run_connection_test(db, account, force_fail=bool(body.force_fail))

    record_audit(
        db,
        current_user["username"],
        "test_account_connection",
        "account",
        str(account.id),
        {
            "success": result.get("success"),
            "error": result.get("error"),
            "reason_code": result.get("reason_code"),
            "runtime_status": result.get("runtime_status"),
        },
    )
    db.commit()

    return AccountTestConnectionResponse(
        success=bool(result.get("success")),
        account_id=account.id,
        platform=account.platform,
        message=str(result.get("message") or ""),
        error=result.get("error"),
        status=result.get("status"),
        reason_code=result.get("reason_code"),
        verified_at=result.get("verified_at"),
        runtime_status=result.get("runtime_status"),
        runtime_status_label=result.get("runtime_status_label"),
    )


@router.get("/whatsapp-web/pool-status", response_model=WhatsAppWebPoolStatusResponse)
async def whatsapp_web_pool_status(
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """وضعیت replicaهای زنده whatsapp_worker_pool (heartbeat Redis)."""
    redis_client = get_redis_client()
    workers = await list_whatsapp_pool_workers(redis_client)
    return WhatsAppWebPoolStatusResponse(workers=workers, total=len(workers))


@router.get("/{account_id}/whatsapp-web/status", response_model=WhatsAppWebStatusResponse)
def whatsapp_web_status(
    account_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """وضعیت سشن واتساپ وب (پروفایل مرورگر + متادیتای ذخیره‌شده)."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if account.platform != PlatformType.WHATSAPP:
        raise HTTPException(
            status_code=400,
            detail="WhatsApp Web status is only available for WhatsApp accounts.",
        )

    status = build_whatsapp_web_status(db, account_id)
    return WhatsAppWebStatusResponse(**status)


@router.post(
    "/{account_id}/whatsapp-web/register",
    response_model=WhatsAppWebRegisterResponse,
)
def register_whatsapp_web_session(
    account_id: int,
    payload: WhatsAppWebRegisterRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """ثبت یا به‌روزرسانی متادیتای سشن واتساپ وب پس از اسکن QR."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if account.platform != PlatformType.WHATSAPP:
        raise HTTPException(
            status_code=400,
            detail="WhatsApp Web registration is only available for WhatsApp accounts.",
        )

    profile_dir = resolve_whatsapp_profile_dir(account_id)
    store_whatsapp_web_session(
        db,
        account_id=account_id,
        linked=payload.linked,
        phone=payload.phone or account.phone_number,
        profile_dir=profile_dir,
    )
    if payload.linked and account.status == AccountStatus.REQUIRES_LOGIN:
        account.status = AccountStatus.ACTIVE

    record_audit(
        db,
        current_user["username"],
        "register_whatsapp_web_session",
        "account",
        str(account.id),
        {"linked": payload.linked, "profile_dir": str(profile_dir)},
    )
    db.commit()

    message = (
        "WhatsApp Web session registered and marked linked."
        if payload.linked
        else "WhatsApp Web session metadata saved (not linked)."
    )
    return WhatsAppWebRegisterResponse(
        success=True,
        account_id=account.id,
        message=message,
        profile_dir=str(profile_dir),
        linked=payload.linked,
    )


@router.get("/deploy/readiness", response_model=DeployReadinessResponse)
def deploy_readiness(
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """چک‌لیست عملیاتی فاز ۸ — وضعیت سشن اکانت‌ها و فلگ‌های امنیتی."""
    return DeployReadinessResponse(**build_deploy_readiness(db))


@router.get("/operational-send/capabilities")
def operational_send_status(
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """وضعیت فلگ‌های ارسال live از طریق API (فاز ۹.۲)."""
    return operational_send_capabilities()


@router.get("/{account_id}/session/status", response_model=AccountSessionStatusResponse)
def account_session_status(
    account_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """وضعیت یکپارچه سشن اکانت — بدون افشای secret/token."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    base = build_account_session_status(db, account)
    runtimes = compute_all_account_runtime_statuses(db, [account])
    runtime = runtimes[0] if runtimes else None
    requires_relogin = False
    if runtime is not None:
        requires_relogin = runtime.runtime_status in {
            "LOGIN_REQUIRED",
            "SESSION_ERROR",
            "OTP_WAITING",
        }
        base["credential_type"] = runtime.credential_type
        base["credential_state"] = runtime.credential_state
        base["runtime_status"] = runtime.runtime_status
        base["runtime_status_label"] = runtime.runtime_status_label
        base["requires_relogin"] = requires_relogin
        base["last_verified_at"] = runtime.last_verified_at
        sid = (runtime.details or {}).get("session_id")
        if sid is not None:
            base["session_id"] = int(sid)
    return AccountSessionStatusResponse(**base)


@router.post(
    "/{account_id}/session/register",
    response_model=AccountSessionRegisterResponse,
)
def register_account_session(
    account_id: int,
    payload: AccountSessionRegisterRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """ثبت توکن API رمزشده برای بله، تلگرام، روبیکا یا واتساپ Cloud API."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    if account.platform == PlatformType.WHATSAPP:
        mode = resolve_whatsapp_delivery_mode()
        if mode == "web":
            raise HTTPException(
                status_code=400,
                detail=(
                    "WhatsApp Web uses browser profile sessions. "
                    "Use /whatsapp-web/register after QR scan."
                ),
            )
    elif account.platform not in (
        PlatformType.BALE,
        PlatformType.TELEGRAM,
        PlatformType.RUBIKA,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Platform {account.platform.value} does not support API token registration.",
        )

    try:
        register_api_token_session(db, account=account, session_payload=payload.session_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session_type = required_session_type(account.platform)
    record_audit(
        db,
        current_user["username"],
        "register_account_session",
        "account",
        str(account.id),
        {"platform": account.platform.value, "session_type": session_type.value},
    )
    db.commit()

    return AccountSessionRegisterResponse(
        success=True,
        account_id=account.id,
        platform=account.platform,
        session_type=session_type.value,
        message="Encrypted session registered successfully.",
    )


@router.post(
    "/{account_id}/rubika/session/register",
    response_model=RubikaUserLoginStartResponse,
)
async def register_rubika_user_session(
    account_id: int,
    payload: RubikaUserLoginStartRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """مرحله ۱ ورود تعاملی روبیکا (user_account) — ارسال کد یا تکمیل pass_key.

    دو روش فراخوانی: phone_number برای شروع تازه، یا registration_token+pass_key
    برای تکمیل مرحله pass_key (وقتی پاسخ قبلی stage=pass_key_required بود).
    """
    from core_engine.services.rubika_user_session import (
        RubikaLoginError,
        start_rubika_user_login,
    )

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if account.platform != PlatformType.RUBIKA:
        raise HTTPException(
            status_code=400, detail="این endpoint فقط برای اکانت‌های روبیکا است."
        )

    from core_engine.services.rubika_mode import assert_rubika_user_login_allowed

    try:
        assert_rubika_user_login_allowed()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from core_engine.config import get_settings

    from core_engine.services.rubika_login_state_machine import (
        account_uses_l3_login,
        request_rubika_login,
    )

    if account_uses_l3_login(account_id, db):
        from core_engine.services.rubika_login_live_provider import LiveRubikaLoginProvider

        result = await request_rubika_login(
            db,
            account_id,
            phone_number=payload.phone_number,
            provider=LiveRubikaLoginProvider(),
        )
        if not result.ok:
            raise _login_http_error(
                result.code,
                retry_after_seconds=result.retry_after_seconds,
                state=result.state,
            )
        record_audit(
            db,
            current_user["username"],
            "request_rubika_login",
            "account",
            str(account.id),
            {"challenge_id": result.challenge_id, "state": result.state},
        )
        db.commit()
        return RubikaUserLoginStartResponse(
            registration_token=result.challenge_id or "",
            stage="code_required",
            message=result.message or result.code,
            state=result.state,
            code=result.code,
            retry_after_seconds=result.retry_after_seconds,
        )

    try:
        result = await start_rubika_user_login(
            account_id=account_id,
            phone_number=payload.phone_number,
            pass_key=payload.pass_key,
            registration_token=payload.registration_token,
        )
    except RubikaLoginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record_audit(
        db, current_user["username"], "register_rubika_user_session", "account",
        str(account.id), {"stage": result["stage"]},
    )
    db.commit()

    return RubikaUserLoginStartResponse(**result)


@router.post(
    "/{account_id}/rubika/session/verify",
    response_model=RubikaUserLoginVerifyResponse,
)
async def verify_rubika_user_session(
    account_id: int,
    payload: RubikaUserLoginVerifyRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """مرحله ۲ — تأیید کد پیامکی و ذخیره session رمزنگاری‌شده اکانت شخصی روبیکا."""
    from core_engine.services.rubika_user_session import (
        RubikaLoginError,
        verify_rubika_user_login,
    )

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if account.platform != PlatformType.RUBIKA:
        raise HTTPException(
            status_code=400, detail="این endpoint فقط برای اکانت‌های روبیکا است."
        )

    from core_engine.services.rubika_mode import assert_rubika_user_login_allowed

    try:
        assert_rubika_user_login_allowed()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from core_engine.config import get_settings

    from core_engine.services.rubika_login_state_machine import (
        account_uses_l3_login,
        submit_rubika_login_code,
    )

    if account_uses_l3_login(account_id, db):
        from core_engine.services.rubika_candidate_prover import resolve_canonical_candidate_prover
        from core_engine.services.rubika_login_live_provider import LiveRubikaLoginProvider

        result = await submit_rubika_login_code(
            db,
            account_id,
            challenge_id=payload.registration_token,
            code=payload.phone_code,
            provider=LiveRubikaLoginProvider(),
            prover=resolve_canonical_candidate_prover(),
        )
        if not result.ok:
            db.rollback()
            raise _login_http_error(result.code, state=result.state)
        record_audit(
            db,
            current_user["username"],
            "submit_rubika_login_code",
            "account",
            str(account.id),
            {
                "challenge_id": result.challenge_id,
                "state": result.state,
                "auth_ready": result.auth_ready,
                "dispatch_ready": result.dispatch_ready,
                "lifecycle_status": result.lifecycle_status,
            },
        )
        db.commit()
        db.refresh(account)
        from core_engine.services.account_runtime_status import compute_account_runtime_status

        runtime = compute_account_runtime_status(db, account)
        return RubikaUserLoginVerifyResponse(
            success=True,
            account_id=account_id,
            guid="",  # never echo secrets; GUID is bound on Account
            phone_number="",
            message=result.message or result.code,
            lifecycle_status=result.lifecycle_status,
            runtime_status=runtime.runtime_status,
            runtime_status_label=runtime.runtime_status_label,
        )

    try:
        result = await verify_rubika_user_login(
            db,
            registration_token=payload.registration_token,
            phone_code=payload.phone_code,
        )
    except RubikaLoginError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result["account_id"] != account_id:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="این registration_token برای اکانت دیگری ساخته شده است.",
        )

    record_audit(
        db, current_user["username"], "verify_rubika_user_session", "account",
        str(account.id), {"guid": result["guid"]},
    )
    db.commit()

    return RubikaUserLoginVerifyResponse(**result)

@router.get(
    "/{account_id}/operational-send/preflight",
    response_model=LiveSendPreflightResponse,
)
def live_send_preflight(
    account_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """چک‌لیست پیش از ارسال live برای یک اکانت."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    return LiveSendPreflightResponse(**build_live_send_preflight(db, account))


@router.post("/{account_id}/send-test", response_model=AccountSendTestResponse)
async def send_test_message(
    account_id: int,
    payload: AccountSendTestRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """ارسال یک پیام تست عملیاتی (پیش‌فرض: dry-run — بدون ارسال واقعی)."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    from core_engine.services.archive import require_account_not_archived

    require_account_not_archived(account)

    try:
        result = await send_account_test_message(
            db,
            account,
            message_text=payload.message_text,
            recipient=payload.recipient,
            dry_run=payload.dry_run,
            confirm_live_send=payload.confirm_live_send,
        )
    except OperationalSendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record_audit(
        db,
        current_user["username"],
        "send_test_message",
        "account",
        str(account.id),
        {
            "dry_run": payload.dry_run,
            "live_send": not payload.dry_run,
            "success": result["success"],
            "status": result["status"],
            "recipient": result["recipient"],
            "message_text": payload.message_text,
            "platform_message_id": result.get("platform_message_id"),
        },
    )
    db.commit()

    return AccountSendTestResponse(**result)
