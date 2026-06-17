"""API مدیریت اکانت‌های پیام‌رسان."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core_engine.api.schemas import (
    AccountCreateRequest,
    AccountCreateResponse,
    AccountResponse,
    AccountsListResponse,
    AccountTestConnectionRequest,
    AccountTestConnectionResponse,
    AccountUpdateRequest,
)
from core_engine.database import get_db
from core_engine.models import Account, AccountStatus, PlatformType, RoleType
from core_engine.services.audit_service import record_audit
from core_engine.services.rbac import requires_role

router = APIRouter(prefix="/accounts", tags=["accounts"])


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
    success = True
    message = "Account configuration looks valid (live channel test not enabled)."
    error: str | None = None

    if body.force_fail:
        success = False
        error = "Forced failure for testing."
        message = "Connection test failed."
    elif account.status == AccountStatus.BANNED:
        success = False
        error = "Account is banned."
        message = "Connection test failed."
    elif account.status == AccountStatus.REQUIRES_LOGIN:
        success = False
        error = "Account requires login."
        message = "Connection test failed."
    elif not account.phone_number:
        success = False
        error = "Missing account_identifier."
        message = "Connection test failed."

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
