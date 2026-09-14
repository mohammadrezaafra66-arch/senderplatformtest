"""API واردات داده (فایل اکسل)."""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from core_engine.api.schemas import ImportCommitRequest, ImportCommitResponse
from core_engine.database import get_db
from core_engine.models import (
    ConsentStatus,
    Contact,
    ImportBatch,
    ImportRow,
    ImportRowStatus,
    ImportStatus,
)
from core_engine.services.contact_delete import (
    is_contact_deleted,
    reactivate_deleted_contact_from_import,
)
from core_engine.services.contact_import import apply_import_to_contact
from core_engine.services.excel_processor import (
    NORMALIZED_COLUMN_ALIASES,
    PHONE_SOURCE_KEYS,
    ExcelProcessor,
)

router = APIRouter(prefix="/imports", tags=["imports"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR = PROJECT_ROOT / "storage" / "excel_uploads"
ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".xlsm"}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024

ROW_STATUS_MAP = {
    "valid": ImportRowStatus.VALID,
    "invalid": ImportRowStatus.INVALID,
    "duplicate": ImportRowStatus.DUPLICATE,
    "pending": ImportRowStatus.PENDING,
    "skipped": ImportRowStatus.SKIPPED,
}


def _safe_filename(filename: str | None) -> str:
    if not filename:
        return "upload.xlsx"
    return Path(filename).name


def _validate_extension(filename: str) -> None:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid file extension '{extension}'. "
                "Allowed extensions: .xlsx, .xls, .xlsm"
            ),
        )


def _map_row_status(status_value: str | None) -> ImportRowStatus:
    if not status_value:
        return ImportRowStatus.PENDING
    return ROW_STATUS_MAP.get(status_value.lower(), ImportRowStatus.INVALID)


def _extract_import_phone_raw(raw_data: dict[str, Any]) -> str | None:
    """Extract a single phone cell from known phone columns only.

    Fail closed when zero or multiple phone cells are present. Never scans
    non-phone fields (names, notes, etc.).
    """
    phone_values: list[str] = []
    for column, value in raw_data.items():
        if value in (None, ""):
            continue
        standard = NORMALIZED_COLUMN_ALIASES.get(str(column).strip().casefold())
        if standard not in PHONE_SOURCE_KEYS:
            continue
        text = str(value).strip()
        if text:
            phone_values.append(text)
    if len(phone_values) != 1:
        return None
    return phone_values[0]


def _exact_phone_identity_candidates(raw_phone: str) -> list[str]:
    """Deterministic exact identity forms only — no truncation or fuzzy variants."""
    text = raw_phone.strip()
    if not text:
        return []
    identities = [text]
    # Allow the single deterministic + prefix when the stored canonical uses it.
    if text.startswith("98") and not text.startswith("+"):
        identities.append(f"+{text}")
    deduped: list[str] = []
    seen: set[str] = set()
    for identity in identities:
        if identity not in seen:
            seen.add(identity)
            deduped.append(identity)
    return deduped


def _lookup_deleted_contact_by_exact_phone(db: Session, raw_phone: str) -> Contact | None:
    """Match soft-deleted contacts by exact canonical phone_e164 only.

    Fail closed when:
    - no exact match
    - any active Contact matches the same identity
    - more than one Contact matches across identity candidates
    - more than one soft-deleted match
    """
    identities = _exact_phone_identity_candidates(raw_phone)
    if not identities:
        return None

    matches = (
        db.query(Contact)
        .filter(Contact.phone_e164.in_(identities))
        .all()
    )
    if not matches:
        return None
    # Ambiguity / active conflict → fail closed (no mutation).
    if len(matches) != 1:
        return None
    contact = matches[0]
    if not is_contact_deleted(contact):
        return None
    return contact


@router.post("/contacts/preview")
async def preview_contacts_import(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(None),
):
    original_file_name = _safe_filename(file.filename)
    _validate_extension(original_file_name)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="File size exceeds 20MB limit.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_file_name = f"{uuid4().hex}_{original_file_name}"
    file_path = UPLOAD_DIR / stored_file_name

    try:
        file_path.write_bytes(content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to save uploaded file.") from exc

    processor = ExcelProcessor()
    try:
        preview = processor.build_preview(str(file_path), sheet_name)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to process Excel file.",
        ) from exc

    preview_errors = preview.get("errors") or []
    response_status = "preview_ready" if not preview_errors else "preview_failed"

    return {
        "status": response_status,
        "stored_file_name": stored_file_name,
        "original_file_name": original_file_name,
        "file_path": str(file_path),
        "sheet_name": preview.get("sheet_name") or sheet_name,
        "preview": preview,
    }


@router.post("/contacts/commit", response_model=ImportCommitResponse)
def commit_contacts_import(
    payload: ImportCommitRequest,
    db: Annotated[Session, Depends(get_db)],
):
    file_path = Path(payload.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Import file not found.")

    _validate_extension(payload.stored_file_name or file_path.name)

    processor = ExcelProcessor()
    try:
        preview = processor.build_preview(str(file_path), payload.sheet_name)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Failed to process Excel file for commit.",
        ) from exc

    if preview.get("errors"):
        error_codes = [error.get("code") for error in preview["errors"] if isinstance(error, dict)]
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Excel file could not be processed for commit.",
                "errors": preview["errors"],
                "error_codes": error_codes,
            },
        )

    uploaded_by = payload.uploaded_by or "unknown"
    preview_rows: list[dict[str, Any]] = preview.get("rows") or []

    import_batch = ImportBatch(
        file_name=payload.stored_file_name,
        original_file_name=payload.original_file_name,
        file_path=str(file_path),
        sheet_name=preview.get("sheet_name") or payload.sheet_name,
        row_count=preview.get("total_rows", len(preview_rows)),
        valid_rows_count=preview.get("valid_rows_count", 0),
        invalid_rows_count=preview.get("invalid_rows_count", 0),
        duplicate_rows_count=preview.get("duplicate_rows_count", 0),
        errors_count=0,
        status=ImportStatus.PENDING,
        uploaded_by=uploaded_by,
    )

    created_contacts_count = 0
    invalid_rows_count = 0
    duplicate_rows_count = 0
    valid_rows_count = 0

    try:
        db.add(import_batch)
        db.flush()

        for row in preview_rows:
            row_status = _map_row_status(row.get("status"))
            import_row = ImportRow(
                batch_id=import_batch.id,
                row_index=row.get("row_index", 0),
                raw_data=row.get("raw_data"),
                normalized_data=row.get("normalized_data"),
                status=row_status,
                is_valid=bool(row.get("is_valid")),
                error_code=row.get("error_code"),
                error_message=row.get("error_message"),
            )
            db.add(import_row)
            db.flush()

            if row_status in {ImportRowStatus.VALID, ImportRowStatus.DUPLICATE}:
                normalized = row.get("normalized_data") or {}
                phone_e164 = normalized.get("phone_e164")
                if not phone_e164:
                    if row_status == ImportRowStatus.DUPLICATE:
                        duplicate_rows_count += 1
                    else:
                        invalid_rows_count += 1
                    continue
                contact, action, tags_added = apply_import_to_contact(
                    db,
                    phone_e164=phone_e164,
                    first_name=normalized.get("first_name"),
                    last_name=normalized.get("last_name"),
                    tags=normalized.get("tags") or [],
                    import_batch_id=import_batch.id,
                    import_row_id=import_row.id,
                    telegram_hint=normalized.get("telegram_hint"),
                    locale=normalized.get("locale"),
                    extra_variables=normalized.get("extra_variables") or {},
                    channel_handle=str(normalized.get("chat_id") or "").strip() or None,
                )
                normalized = dict(normalized)
                normalized["tags_added"] = tags_added
                normalized["merge_action"] = action
                import_row.normalized_data = normalized
                if action in {"created", "reactivated"}:
                    import_row.status = ImportRowStatus.VALID
                    import_row.is_valid = True
                    import_row.error_code = None
                    import_row.error_message = None
                    created_contacts_count += 1
                    valid_rows_count += 1
                else:
                    import_row.status = ImportRowStatus.DUPLICATE
                    import_row.is_valid = True
                    import_row.error_code = "merged_existing_contact"
                    import_row.error_message = (
                        f"شماره {phone_e164} با مخاطب موجود ادغام شد."
                    )
                    import_row.duplicate_of_contact_id = contact.id
                    duplicate_rows_count += 1
            elif row_status == ImportRowStatus.INVALID:
                reactivated = False
                if row.get("error_code") == "invalid_phone":
                    raw_data = row.get("raw_data") or {}
                    raw_phone = _extract_import_phone_raw(raw_data)
                    existing_contact = (
                        _lookup_deleted_contact_by_exact_phone(db, raw_phone)
                        if raw_phone
                        else None
                    )
                    if existing_contact:
                        normalized = dict(row.get("normalized_data") or {})
                        normalized["phone_e164"] = existing_contact.phone_e164
                        reactivate_deleted_contact_from_import(
                            existing_contact,
                            import_batch_id=import_batch.id,
                            import_row_id=import_row.id,
                            normalized=normalized,
                        )
                        import_row.status = ImportRowStatus.VALID
                        import_row.is_valid = True
                        import_row.error_code = None
                        import_row.error_message = None
                        import_row.normalized_data = normalized
                        created_contacts_count += 1
                        valid_rows_count += 1
                        reactivated = True
                if not reactivated:
                    invalid_rows_count += 1

        errors_count = invalid_rows_count + duplicate_rows_count

        import_batch.valid_rows_count = valid_rows_count
        import_batch.invalid_rows_count = invalid_rows_count
        import_batch.duplicate_rows_count = duplicate_rows_count
        import_batch.errors_count = errors_count

        if created_contacts_count > 0 or duplicate_rows_count > 0:
            import_batch.status = ImportStatus.COMMITTED
            import_batch.committed_at = datetime.utcnow()
            response_status = "committed"
            message = "Import committed successfully"
        else:
            import_batch.status = ImportStatus.FAILED
            response_status = "failed"
            message = "No valid contacts were committed"

        db.commit()
        db.refresh(import_batch)

        return ImportCommitResponse(
            status=response_status,
            import_batch_id=import_batch.id,
            total_rows=import_batch.row_count,
            created_contacts_count=created_contacts_count,
            invalid_rows_count=invalid_rows_count,
            duplicate_rows_count=duplicate_rows_count,
            errors_count=errors_count,
            message=message,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Import commit failed.",
        ) from exc
