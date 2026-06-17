"""خواندن و آماده‌سازی پایگاه دانش برای تولید پیام."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "storage" / "knowledge_base"
DEFAULT_KB_FILENAME = "default_sales_kb.md"


def get_default_kb_path() -> str:
    return str(KNOWLEDGE_BASE_DIR / DEFAULT_KB_FILENAME)


def _relative_source(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def read_knowledge_base(file_path: str | None = None) -> dict:
    path = Path(file_path) if file_path else Path(get_default_kb_path())

    if not path.exists():
        return {
            "success": False,
            "error": f"Knowledge base file not found: {_relative_source(path)}",
        }

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "success": False,
            "error": f"Failed to read knowledge base file: {exc}",
        }

    if not content.strip():
        return {
            "success": False,
            "error": "Knowledge base file is empty.",
        }

    return {
        "success": True,
        "source": _relative_source(path),
        "content": content,
        "character_count": len(content),
    }


def build_kb_context(max_chars: int = 4000) -> dict:
    if not isinstance(max_chars, int) or max_chars < 1:
        return {
            "success": False,
            "error": "max_chars must be an integer greater than or equal to 1.",
        }

    kb_result = read_knowledge_base()
    if not kb_result.get("success"):
        return {
            "success": False,
            "error": kb_result.get("error", "Failed to read knowledge base."),
        }

    content = kb_result.get("content", "")
    if not content:
        return {
            "success": False,
            "error": "Knowledge base content is empty.",
        }

    truncated = len(content) > max_chars
    context = content[:max_chars]

    return {
        "success": True,
        "context": context,
        "truncated": truncated,
        "character_count": len(context),
        "max_chars": max_chars,
    }
