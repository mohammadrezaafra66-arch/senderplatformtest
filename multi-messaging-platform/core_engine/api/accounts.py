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
    AccountsListResponse,
    AccountSessionRegisterRequest,
    AccountSessionRegisterResponse,
    AccountSessionStatusResponse,
    AccountSendTestRequest,
    AccountSendTestResponse,
    LiveSendPreflightCheckItem,
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


def _account_to_response(account: Account) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        platform=account.platform,
        account_identifier=account.phone_number,
        label=account.label,
        status=account.status,
        proxy_url=account.proxy_url,
        policy_id=account.policy_id,
        created_at=account.created_at,
        updated_at=account.updated_at,
        last_used_at=account.last_used_at,
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
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """لیست اکانت‌ها با فیلتر اختیاری platform."""
    query = db.query(Account)
    if platform:
        query = query.filter(Account.platform == platform)

    accounts = query.order_by(Account.created_at.desc()).all()
    return AccountsListResponse(
        items=[_account_to_response(account) for account in accounts],
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
        account = Account(
            platform=payload.platform,
            phone_number=account_identifier,
            label=payload.label,
            proxy_url=payload.proxy_url,
            status=payload.status,
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
                "status": payload.status.value,
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
        raise HTTPException(status_code=500, detail="Failed to create account.") from exc


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
        account.status = updates["status"]

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
        return _account_to_response(account)
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
    """تست اتصال اکانت — فعلاً بدون اتصال واقعی به کانال."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    body = payload or AccountTestConnectionRequest()
    readiness = evaluate_account_session_readiness(db, account)

    success = readiness.ready
    message = readiness.message
    error: str | None = readiness.error

    if body.force_fail:
        success = False
        error = "forced_failure"
        message = "Connection test failed (forced)."
    elif account.status == AccountStatus.BANNED:
        success = False
        error = "account_banned"
        message = "Connection test failed: account is banned."
    elif not success and error is None:
        error = "session_not_ready"

    record_audit(
        db,
        current_user["username"],
        "test_account_connection",
        "account",
        str(account.id),
        {"success": success, "error": error},
    )
    db.commit()

    return AccountTestConnectionResponse(
        success=success,
        account_id=account.id,
        platform=account.platform,
        message=message,
        error=error,
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
    """وضعیت یکپارچه سشن اکانت (همه کانال‌ها)."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    return AccountSessionStatusResponse(**build_account_session_status(db, account))


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
