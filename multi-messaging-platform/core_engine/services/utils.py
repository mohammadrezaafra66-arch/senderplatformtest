"""توابع کمکی pure — بدون وابستگی به DB، Redis یا API."""

from __future__ import annotations

import re


def normalize_phone_number(phone: str) -> str:
    """Remove spaces, dashes, parentheses, plus signs; keep digits only."""
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def is_valid_phone(phone: str) -> bool:
    """Return True if normalized phone length is between 10 and 15 inclusive."""
    digits = normalize_phone_number(phone)
    length = len(digits)
    return 10 <= length <= 15


def sum_numbers(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b
