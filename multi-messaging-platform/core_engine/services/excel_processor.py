"""پردازش فایل‌های اکسل — خواندن، mapping ستون‌ها، نرمال‌سازی و preview."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

STANDARD_COLUMN_KEYS = (
    "first_name",
    "last_name",
    "full_name",
    "phone",
    "phone_whatsapp",
    "phone_telegram",
    "phone_bale",
    "phone_rubika",
    "chat_id",
)

COLUMN_ALIASES: dict[str, str] = {
    # فارسی
    "نام": "first_name",
    "نام کوچک": "first_name",
    "نام خانوادگی": "last_name",
    "نام و نام خانوادگی": "full_name",
    "شماره": "phone",
    "شماره تماس": "phone",
    "موبایل": "phone",
    "تلفن": "phone",
    "واتساپ": "phone_whatsapp",
    "شماره واتساپ": "phone_whatsapp",
    "تلگرام": "phone_telegram",
    "شماره تلگرام": "phone_telegram",
    "بله": "phone_bale",
    "شماره بله": "phone_bale",
    "روبیکا": "phone_rubika",
    "شماره روبیکا": "phone_rubika",
    # انگلیسی
    "first_name": "first_name",
    "firstname": "first_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "full_name": "full_name",
    "fullname": "full_name",
    "name": "full_name",
    "phone": "phone",
    "mobile": "phone",
    "phone_number": "phone",
    "phone_e164": "phone",
    "whatsapp": "phone_whatsapp",
    "phone_whatsapp": "phone_whatsapp",
    "telegram": "phone_telegram",
    "phone_telegram": "phone_telegram",
    "bale": "phone_bale",
    "phone_bale": "phone_bale",
    "rubika": "phone_rubika",
    "phone_rubika": "phone_rubika",
    "chat_id": "chat_id",
    "bale_chat_id": "chat_id",
    "شناسه بله": "chat_id",
}

NORMALIZED_COLUMN_ALIASES = {
    alias.casefold(): standard_key for alias, standard_key in COLUMN_ALIASES.items()
}

PHONE_SOURCE_KEYS = (
    "phone",
    "phone_whatsapp",
    "phone_telegram",
    "phone_bale",
    "phone_rubika",
)

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ENGLISH_DIGITS = "0123456789"

IRAN_MOBILE_PATTERN = re.compile(r"^\+989\d{9}$")


class ExcelProcessor:
    """خواندن، اعتبارسنجی و پاکسازی داده‌های اکسل."""

    def build_preview(self, file_path: str, sheet_name: str | None = None) -> dict[str, Any]:
        preview: dict[str, Any] = {
            "file_path": file_path,
            "sheet_name": sheet_name,
            "total_rows": 0,
            "valid_rows_count": 0,
            "invalid_rows_count": 0,
            "duplicate_rows_count": 0,
            "detected_columns": [],
            "column_mapping": {},
            "rows": [],
            "errors": [],
        }

        try:
            df, resolved_sheet = self.read_excel(file_path, sheet_name)
        except Exception as exc:
            preview["errors"].append(
                {
                    "code": "file_read_error",
                    "message": str(exc),
                }
            )
            return preview

        preview["sheet_name"] = resolved_sheet
        preview["detected_columns"] = list(df.columns)
        column_mapping = self.detect_column_mapping(preview["detected_columns"])
        preview["column_mapping"] = column_mapping

        if not self._has_phone_column(column_mapping):
            preview["errors"].append(
                {
                    "code": "missing_required_phone_column",
                    "message": "Required phone column was not found in the Excel file.",
                }
            )
            return preview

        seen_phones: dict[str, int] = {}
        rows: list[dict[str, Any]] = []

        for row_offset, (_, series) in enumerate(df.iterrows()):
            row_index = row_offset + 2
            row_result = self.process_row(
                row_index=row_index,
                series=series,
                column_mapping=column_mapping,
                seen_phones=seen_phones,
            )
            rows.append(row_result)

        preview["rows"] = rows
        preview["total_rows"] = len(rows)
        preview["valid_rows_count"] = sum(1 for row in rows if row["status"] == "valid")
        preview["invalid_rows_count"] = sum(1 for row in rows if row["status"] == "invalid")
        preview["duplicate_rows_count"] = sum(1 for row in rows if row["status"] == "duplicate")

        return preview

    def read_excel(self, file_path: str, sheet_name: str | None = None) -> tuple[pd.DataFrame, str]:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Excel file not found: {file_path}")

        excel_file = pd.ExcelFile(path)
        resolved_sheet = sheet_name or excel_file.sheet_names[0]

        df = pd.read_excel(path, sheet_name=resolved_sheet, dtype=str)
        df = df.dropna(how="all")
        df.columns = [self.normalize_column_name(str(column)) for column in df.columns]
        df = df.apply(lambda column: column.map(self._clean_cell_value))

        return df, resolved_sheet

    def normalize_column_name(self, column_name: str) -> str:
        normalized = column_name.strip()
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    def detect_column_mapping(self, columns: list[str]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for column in columns:
            lookup_key = self._column_lookup_key(column)
            standard_key = NORMALIZED_COLUMN_ALIASES.get(lookup_key)
            if standard_key and standard_key not in mapping:
                mapping[standard_key] = column
        return mapping

    def _has_phone_column(self, column_mapping: dict[str, str]) -> bool:
        return any(key in column_mapping for key in PHONE_SOURCE_KEYS)

    def normalize_phone(self, value: str | int | float | None) -> str | None:
        if value is None:
            return None

        text = self._clean_cell_value(value)
        if not text:
            return None

        text = self._to_english_digits(text)
        text = re.sub(r"[^\d+]", "", text)

        if not text:
            return None

        if text.startswith("00"):
            text = "+" + text[2:]
        elif text.startswith("+"):
            text = "+" + text[1:].replace("+", "")
        else:
            text = text.lstrip("+")

        if text.startswith("0") and len(text) == 11 and text[1] == "9":
            text = "+98" + text[1:]
        elif text.startswith("98"):
            text = "+" + text
        elif text.startswith("9") and len(text) == 10:
            text = "+98" + text
        elif text.startswith("9") and not text.startswith("+98"):
            text = "+98" + text
        elif not text.startswith("+"):
            text = "+98" + text

        if not text.startswith("+"):
            text = "+" + text

        if IRAN_MOBILE_PATTERN.match(text):
            return text
        return None

    def split_full_name(self, full_name: str | None) -> tuple[str | None, str | None]:
        if not full_name:
            return None, None

        parts = full_name.strip().split()
        if not parts:
            return None, None
        if len(parts) == 1:
            return parts[0], None
        return parts[0], " ".join(parts[1:])

    def process_row(
        self,
        row_index: int,
        series: pd.Series,
        column_mapping: dict[str, str],
        seen_phones: dict[str, int],
    ) -> dict[str, Any]:
        raw_data = {
            column: self._serialize_value(series.get(column))
            for column in series.index
        }

        mapped_values = {
            standard_key: raw_data.get(source_column)
            for standard_key, source_column in column_mapping.items()
        }

        first_name = self._empty_to_none(mapped_values.get("first_name"))
        last_name = self._empty_to_none(mapped_values.get("last_name"))
        full_name = self._empty_to_none(mapped_values.get("full_name"))

        if full_name and not first_name and not last_name:
            first_name, last_name = self.split_full_name(full_name)

        phone_candidates = [
            mapped_values.get(key)
            for key in PHONE_SOURCE_KEYS
            if mapped_values.get(key) not in (None, "")
        ]
        primary_phone_raw = phone_candidates[0] if phone_candidates else None
        normalized_phone = self.normalize_phone(primary_phone_raw)

        telegram_hint = self._empty_to_none(
            self.normalize_phone(mapped_values.get("phone_telegram"))
            or mapped_values.get("phone_telegram")
        )

        extra_variables = {
            column: value
            for column, value in raw_data.items()
            if column not in column_mapping.values() and value not in (None, "")
        }

        normalized_data: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "phone_e164": normalized_phone,
            "telegram_hint": telegram_hint,
            "locale": "fa-IR",
            "extra_variables": extra_variables,
        }

        row_result: dict[str, Any] = {
            "row_index": row_index,
            "raw_data": raw_data,
            "normalized_data": normalized_data,
            "status": "valid",
            "is_valid": True,
            "error_code": None,
            "error_message": None,
        }

        if not primary_phone_raw:
            row_result.update(
                {
                    "status": "invalid",
                    "is_valid": False,
                    "error_code": "missing_phone",
                    "error_message": "شماره تلفن در این ردیف وجود ندارد.",
                }
            )
            return row_result

        if not normalized_phone:
            row_result.update(
                {
                    "status": "invalid",
                    "is_valid": False,
                    "error_code": "invalid_phone",
                    "error_message": f"شماره تلفن معتبر نیست: {primary_phone_raw}",
                }
            )
            return row_result

        if normalized_phone in seen_phones:
            previous_row = seen_phones[normalized_phone]
            row_result.update(
                {
                    "status": "duplicate",
                    "is_valid": False,
                    "error_code": "duplicate_phone",
                    "error_message": (
                        f"این شماره قبلاً در ردیف {previous_row} ثبت شده است."
                    ),
                }
            )
            return row_result

        seen_phones[normalized_phone] = row_index
        return row_result

    def _column_lookup_key(self, column_name: str) -> str:
        normalized = self.normalize_column_name(column_name)
        return normalized.casefold()

    def _clean_cell_value(self, value: Any) -> str | None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None

        text = str(value).strip()
        if text.lower() in {"", "nan", "none", "null"}:
            return None

        if re.fullmatch(r"\d+\.0", text):
            text = text[:-2]

        return text or None

    def _serialize_value(self, value: Any) -> Any:
        cleaned = self._clean_cell_value(value)
        return cleaned

    def _empty_to_none(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value).strip() or None

    def _to_english_digits(self, text: str) -> str:
        translation_table = str.maketrans(
            PERSIAN_DIGITS + ARABIC_DIGITS,
            ENGLISH_DIGITS * 2,
        )
        return text.translate(translation_table)
