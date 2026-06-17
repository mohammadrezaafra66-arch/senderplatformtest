"""پاسخ JSON با UTF-8 صریح برای endpointهای debug."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi.responses import Response

UTF8_JSON_MEDIA_TYPE = "application/json; charset=utf-8"
MOJIBAKE_MARKERS = ("Ø", "Ù", "Ã", "Â", "â", "€")
ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06FF]")


def utf8_json_response(content: Any, *, status_code: int = 200) -> Response:
    """Serialize JSON با ensure_ascii=False و برگرداندن bytes خام UTF-8."""
    body = json.dumps(content, ensure_ascii=False, default=str).encode("utf-8")
    return Response(
        content=body,
        status_code=status_code,
        media_type=UTF8_JSON_MEDIA_TYPE,
    )


def contains_replacement_char(text: str | None) -> bool:
    if not text:
        return False
    return "\ufffd" in text or "???" in text


def contains_mojibake_pattern(text: str | None) -> bool:
    if not text:
        return False
    has_arabic = bool(ARABIC_SCRIPT_RE.search(text))
    has_markers = any(marker in text for marker in MOJIBAKE_MARKERS)
    return has_markers and not has_arabic


def encoding_debug_fields(text: str | None) -> dict[str, Any]:
    value = text or ""
    return {
        "repr": repr(value),
        "contains_replacement_char": contains_replacement_char(value),
        "contains_mojibake_pattern": contains_mojibake_pattern(value),
        "has_arabic_script": bool(ARABIC_SCRIPT_RE.search(value)),
        "utf8_byte_length": len(value.encode("utf-8")),
    }


def encoding_ping_payload() -> dict[str, Any]:
    return {
        "persian_text": "سلام محمد رضایی",
        "product_text": "تلویزیون 65 اینچ ال جی مدل 65UA85006",
        "emoji_text": "✅ تست فارسی",
        "encoding": "utf-8",
    }
