"""Protected template placeholder extraction and comparison."""

from __future__ import annotations

import hashlib
import re

# Same syntax as campaign template rendering: {{identifier}}
TEMPLATE_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
SINGLE_BRACE_NAME = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def extract_placeholder_names(text: str) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for name in TEMPLATE_PLACEHOLDER.findall(text or ""):
        if name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def canonical_placeholder_set(text: str) -> frozenset[str]:
    return frozenset(extract_placeholder_names(text))


def has_malformed_placeholders(text: str, *, expected_names: frozenset[str]) -> bool:
    """True when a known placeholder appears as {name} instead of {{name}}."""
    if not expected_names:
        return False
    found = {name for name in SINGLE_BRACE_NAME.findall(text or "")}
    return bool(found & expected_names)


def template_fingerprint(template_text: str) -> str:
    normalized = (template_text or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()
