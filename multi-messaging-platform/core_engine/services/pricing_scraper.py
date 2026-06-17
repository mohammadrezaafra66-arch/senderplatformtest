"""اسکرپر موقت HTML برای قیمت‌های افراکالا — fallback تا آماده شدن JSON API."""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from core_engine.config import get_settings

PRICING_FETCH_TIMEOUT_SECONDS = 15
RAW_TEXT_MAX_LEN = 500
BLOCK_TAGS = ("div", "article", "section", "li")
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ENGLISH_DIGITS = "0123456789"
DIGIT_TRANSLATION = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS,
    ENGLISH_DIGITS + ENGLISH_DIGITS,
)
PRICE_KEYWORDS = ("تومان", "ریال", "قیمت", "price", "irr", "تومن")

MIN_VALID_PRICE = 1_000_000
MAX_VALID_PRICE = 2_000_000_000
MIN_TABLE_COLUMNS = 8
SKU_PREFIX = "AFK-"

HEADER_FIELD_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("row_number", ("ردیف",)),
    ("title", ("نام محصول", "نام کالا")),
    ("brand", ("برند",)),
    ("category", ("دسته",)),
    ("sku", ("sku",)),
    ("availability", ("موجودی",)),
    ("previous_price", ("قیمت قبلی",)),
    ("cash_price", ("قیمت فعلی",)),
    ("change_percent", ("تغییر",)),
    ("updated_at_text", ("آخرین بروزرسانی", "بروزرسانی")),
]

DEFAULT_COLUMN_MAP: dict[str, int] = {
    "row_number": 0,
    "title": 1,
    "brand": 2,
    "category": 3,
    "sku": 4,
    "availability": 5,
    "previous_price": 6,
    "cash_price": 7,
    "change_percent": 8,
    "updated_at_text": 9,
}

GARBAGE_TITLE_VALUES = frozenset(
    {
        "",
        "-",
        "—",
        "موجود",
        "ناموجود",
        "بدون تغییر",
        "جزئیات",
        "ردیف",
        "sku",
    }
)


def normalize_persian_digits(value: str) -> str:
    if not value:
        return ""
    return value.translate(DIGIT_TRANSLATION)


def clean_text(value: str | None) -> str:
    if value is None:
        return ""
    normalized = normalize_persian_digits(value)
    normalized = normalized.replace("\xa0", " ")
    normalized = re.sub(r"[\t\r\n]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def parse_price_from_text(text: str) -> int | None:
    """یک عدد قیمت معتبر از متن سلول — نه از کل ردیف."""
    cleaned = clean_text(text)
    if not cleaned:
        return None

    cleaned = re.sub(
        r"(?:تومان|تومن|ریال|irr)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"[تT]\s*$", "", cleaned).strip()

    digits_only = re.sub(r"[^\d]", "", normalize_persian_digits(cleaned))
    if not digits_only:
        return None

    try:
        value = int(digits_only)
    except ValueError:
        return None

    if value < MIN_VALID_PRICE or value > MAX_VALID_PRICE:
        return None

    return value


def _parse_row_number(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", normalize_persian_digits(text))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _truncate_raw_text(value: str) -> str:
    cleaned = clean_text(value)
    if len(cleaned) <= RAW_TEXT_MAX_LEN:
        return cleaned
    return cleaned[:RAW_TEXT_MAX_LEN]


def _looks_like_price_fragment(text: str) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return True
    if parse_price_from_text(cleaned) is not None:
        return True
    lowered = cleaned.lower()
    return any(keyword in lowered for keyword in PRICE_KEYWORDS)


def _match_header_field(header_text: str) -> str | None:
    normalized = clean_text(header_text).lower()
    if not normalized:
        return None
    for field, aliases in HEADER_FIELD_PATTERNS:
        for alias in aliases:
            alias_lower = alias.lower()
            if alias_lower in normalized or normalized in alias_lower:
                return field
    return None


def _build_column_map_from_headers(header_texts: list[str]) -> dict[str, int]:
    column_map: dict[str, int] = {}
    for index, header in enumerate(header_texts):
        field = _match_header_field(header)
        if field and field not in column_map:
            column_map[field] = index
    required = ("title", "sku", "cash_price")
    if all(field in column_map for field in required):
        return column_map
    return {}


def _resolve_column_map(table) -> tuple[dict[str, int], bool]:
    thead = table.find("thead")
    if thead:
        header_cells = thead.find_all(["th", "td"])
        if header_cells:
            header_texts = [clean_text(cell.get_text(" ", strip=True)) for cell in header_cells]
            mapped = _build_column_map_from_headers(header_texts)
            if mapped:
                return mapped, True

    for row in table.find_all("tr", limit=3):
        header_cells = row.find_all("th")
        if not header_cells:
            continue
        header_texts = [clean_text(cell.get_text(" ", strip=True)) for cell in header_cells]
        mapped = _build_column_map_from_headers(header_texts)
        if mapped:
            return mapped, True

    first_row = table.find("tr")
    if first_row:
        cells = first_row.find_all(["th", "td"])
        texts = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        if any(_match_header_field(text) for text in texts):
            mapped = _build_column_map_from_headers(texts)
            if mapped:
                return mapped, True

    return dict(DEFAULT_COLUMN_MAP), False


def _cell_text(cells: list, index: int) -> str:
    if index < 0 or index >= len(cells):
        return ""
    return clean_text(cells[index].get_text(" ", strip=True))


def _is_valid_sku(sku: str) -> bool:
    cleaned = clean_text(sku).upper()
    return bool(cleaned) and cleaned.startswith(SKU_PREFIX)


def _is_valid_title(title: str) -> bool:
    cleaned = clean_text(title)
    if len(cleaned) < 3:
        return False
    if cleaned.lower() in GARBAGE_TITLE_VALUES:
        return False
    if _looks_like_price_fragment(cleaned) and len(cleaned) < 20:
        return False
    return True


def _parse_table_row(
    cells: list,
    column_map: dict[str, int],
    *,
    source: str,
    page_number: int,
) -> dict[str, Any] | None:
    if len(cells) < MIN_TABLE_COLUMNS:
        return None

    if cells and cells[0].name == "th":
        return None

    row_number_text = _cell_text(cells, column_map.get("row_number", 0))
    title = _cell_text(cells, column_map.get("title", 1))
    brand = _cell_text(cells, column_map.get("brand", 2))
    category = _cell_text(cells, column_map.get("category", 3))
    sku = _cell_text(cells, column_map.get("sku", 4)).upper()
    availability = _cell_text(cells, column_map.get("availability", 5))
    previous_price_text = _cell_text(cells, column_map.get("previous_price", 6))
    cash_price_text = _cell_text(cells, column_map.get("cash_price", 7))
    change_percent = _cell_text(cells, column_map.get("change_percent", 8))
    updated_at_text = _cell_text(cells, column_map.get("updated_at_text", 9))

    if not _is_valid_sku(sku):
        return None
    if not _is_valid_title(title):
        return None

    cash_price = parse_price_from_text(cash_price_text)
    if cash_price is None:
        return None

    previous_price = parse_price_from_text(previous_price_text)
    row_number = _parse_row_number(row_number_text)
    cell_texts = [_cell_text(cells, idx) for idx in range(len(cells))]
    raw_text = " | ".join(text for text in cell_texts if text)

    return {
        "row_number": row_number,
        "title": title,
        "brand": brand or None,
        "category": category or None,
        "sku": sku,
        "availability": availability or None,
        "previous_price": previous_price,
        "cash_price": cash_price,
        "change_percent": change_percent or None,
        "updated_at_text": updated_at_text or None,
        "source": source,
        "page_number": page_number,
        "raw_text": _truncate_raw_text(raw_text),
    }


def _extract_from_pricing_table(
    soup: BeautifulSoup,
    *,
    source: str = "html_scraper",
    page_number: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    tables = soup.find_all("table")
    best_items: list[dict[str, Any]] = []
    best_stats = {
        "tables_found": len(tables),
        "rows_checked": 0,
        "valid_table_rows": 0,
        "invalid_rows": 0,
    }

    for table in tables:
        column_map, has_header = _resolve_column_map(table)
        items: list[dict[str, Any]] = []
        rows_checked = 0
        valid_table_rows = 0
        invalid_rows = 0

        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            rows_checked += 1
            if has_header and all(cell.name == "th" for cell in cells):
                continue
            if has_header:
                first_field = _match_header_field(_cell_text(cells, 0))
                if first_field == "row_number" and _cell_text(cells, 0) == "ردیف":
                    continue
            parsed = _parse_table_row(
                cells,
                column_map,
                source=source,
                page_number=page_number,
            )
            if parsed is None:
                if len([cell for cell in cells if cell.name == "td"]) >= MIN_TABLE_COLUMNS:
                    invalid_rows += 1
                continue
            valid_table_rows += 1
            items.append(parsed)

        if valid_table_rows > best_stats["valid_table_rows"]:
            best_items = items
            best_stats = {
                "tables_found": len(tables),
                "rows_checked": rows_checked,
                "valid_table_rows": valid_table_rows,
                "invalid_rows": invalid_rows,
            }

    return best_items, best_stats


def _extract_from_blocks(
    soup: BeautifulSoup,
    *,
    source: str = "html_scraper",
) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    blocks_checked = 0

    for tag_name in BLOCK_TAGS:
        for block in soup.find_all(tag_name):
            raw_text = clean_text(block.get_text(" ", strip=True))
            if len(raw_text) < 20:
                continue
            blocks_checked += 1
            cash_price = parse_price_from_text(raw_text)
            if cash_price is None:
                continue

            child_texts = [
                clean_text(child.get_text(" ", strip=True))
                for child in block.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "span"])
            ]
            child_texts = [text for text in child_texts if text]
            title = None
            for part in sorted(child_texts or [raw_text], key=len, reverse=True):
                if _is_valid_title(part) and not _looks_like_price_fragment(part):
                    title = part
                    break
            if not title:
                continue

            items.append(
                {
                    "row_number": None,
                    "title": title,
                    "brand": None,
                    "category": None,
                    "sku": None,
                    "availability": None,
                    "previous_price": None,
                    "cash_price": cash_price,
                    "change_percent": None,
                    "updated_at_text": None,
                    "source": source,
                    "page_number": 1,
                    "raw_text": _truncate_raw_text(raw_text),
                }
            )

    return items, blocks_checked


def dedupe_pricing_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen_sku: set[str] = set()
    seen_fallback: set[tuple[str, int]] = set()
    unique: list[dict[str, Any]] = []
    duplicates_removed = 0

    for item in items:
        sku = clean_text(str(item.get("sku") or "")).upper()
        if sku:
            if sku in seen_sku:
                duplicates_removed += 1
                continue
            seen_sku.add(sku)
            unique.append(item)
            continue

        title = clean_text(str(item.get("title") or "")).lower()
        cash_price = item.get("cash_price")
        if cash_price is None:
            duplicates_removed += 1
            continue
        fallback_key = (title, int(cash_price))
        if fallback_key in seen_fallback:
            duplicates_removed += 1
            continue
        seen_fallback.add(fallback_key)
        unique.append(item)

    return unique, duplicates_removed


def _filter_invalid_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    valid: list[dict[str, Any]] = []
    removed = 0
    for item in items:
        sku = clean_text(str(item.get("sku") or "")).upper()
        title = clean_text(str(item.get("title") or ""))
        cash_price = item.get("cash_price")

        if cash_price is None or not isinstance(cash_price, int):
            removed += 1
            continue
        if cash_price < MIN_VALID_PRICE or cash_price > MAX_VALID_PRICE:
            removed += 1
            continue
        if sku and not _is_valid_sku(sku):
            removed += 1
            continue
        if not _is_valid_title(title):
            removed += 1
            continue
        valid.append(item)
    return valid, removed


def scrape_pricing_from_html(
    html: str,
    source_url: str,
    *,
    source: str = "html_scraper",
    page_number: int = 1,
    allow_block_scraping: bool = True,
    dedupe: bool = True,
) -> dict[str, Any]:
    warnings: list[str] = []
    soup = BeautifulSoup(html, "lxml")

    table_items, table_stats = _extract_from_pricing_table(
        soup,
        source=source,
        page_number=page_number,
    )
    block_scraping_used = False
    blocks_checked = 0

    if table_stats["valid_table_rows"] > 0:
        raw_items = table_items
        block_scraping_used = False
    elif allow_block_scraping:
        block_items, blocks_checked = _extract_from_blocks(soup, source=source)
        raw_items = block_items
        block_scraping_used = True
        if block_items:
            warnings.append(
                "No valid pricing table rows found; items were extracted from generic blocks"
            )
    else:
        raw_items = []

    items_before_dedupe = list(raw_items)
    filtered_items, invalid_items_removed = _filter_invalid_items(items_before_dedupe)

    if dedupe:
        items, duplicates_removed = dedupe_pricing_items(filtered_items)
    else:
        items = filtered_items
        duplicates_removed = 0

    debug_summary = {
        "tables_found": table_stats["tables_found"],
        "pagination_detected": False,
        "pages_scraped": 1,
        "rows_checked": table_stats["rows_checked"],
        "valid_table_rows": table_stats["valid_table_rows"],
        "invalid_rows": table_stats["invalid_rows"],
        "block_scraping_used": block_scraping_used,
        "blocks_checked": blocks_checked,
        "items_extracted_before_dedupe": len(items_before_dedupe),
        "duplicates_removed": duplicates_removed,
        "invalid_items_removed": invalid_items_removed,
        "items_extracted": len(items),
    }

    if not items:
        warnings.append("No products could be extracted from HTML")
        return {
            "success": False,
            "source": source,
            "source_url": source_url,
            "items": [],
            "item_count": 0,
            "warnings": warnings,
            "debug_summary": debug_summary,
        }

    if table_stats["tables_found"] == 0 and block_scraping_used:
        warnings.append("No HTML tables found; items were extracted from generic blocks")

    return {
        "success": True,
        "source": source,
        "source_url": source_url,
        "items": items,
        "item_count": len(items),
        "warnings": warnings,
        "debug_summary": debug_summary,
    }


def merge_scraped_pages(
    page_results: list[dict[str, Any]],
    *,
    pagination_detected: bool,
    pages_scraped: int,
) -> dict[str, Any]:
    if not page_results:
        return {
            "success": False,
            "source": "playwright_html_scraper",
            "items": [],
            "item_count": 0,
            "warnings": ["No pages were scraped"],
            "debug_summary": {
                "tables_found": 0,
                "pagination_detected": pagination_detected,
                "pages_scraped": pages_scraped,
                "rows_checked": 0,
                "valid_table_rows": 0,
                "invalid_rows": 0,
                "block_scraping_used": False,
                "items_extracted_before_dedupe": 0,
                "duplicates_removed": 0,
                "invalid_items_removed": 0,
                "items_extracted": 0,
            },
        }

    source = str(page_results[0].get("source") or "playwright_html_scraper")
    source_url = str(page_results[0].get("source_url") or "")
    warnings: list[str] = []
    for page in page_results:
        warnings.extend(page.get("warnings") or [])

    all_items: list[dict[str, Any]] = []
    totals = {
        "tables_found": 0,
        "rows_checked": 0,
        "valid_table_rows": 0,
        "invalid_rows": 0,
        "blocks_checked": 0,
    }
    block_scraping_used = False

    for page in page_results:
        all_items.extend(page.get("items") or [])
        summary = page.get("debug_summary") or {}
        totals["tables_found"] = max(totals["tables_found"], int(summary.get("tables_found") or 0))
        totals["rows_checked"] += int(summary.get("rows_checked") or 0)
        totals["valid_table_rows"] += int(summary.get("valid_table_rows") or 0)
        totals["invalid_rows"] += int(summary.get("invalid_rows") or 0)
        totals["blocks_checked"] += int(summary.get("blocks_checked") or 0)
        block_scraping_used = block_scraping_used or bool(summary.get("block_scraping_used"))

    items_before_dedupe = len(all_items)
    filtered_items, invalid_items_removed = _filter_invalid_items(all_items)
    items, duplicates_removed = dedupe_pricing_items(filtered_items)

    debug_summary = {
        "tables_found": totals["tables_found"],
        "pagination_detected": pagination_detected,
        "pages_scraped": pages_scraped,
        "rows_checked": totals["rows_checked"],
        "valid_table_rows": totals["valid_table_rows"],
        "invalid_rows": totals["invalid_rows"],
        "block_scraping_used": block_scraping_used,
        "blocks_checked": totals["blocks_checked"],
        "items_extracted_before_dedupe": items_before_dedupe,
        "duplicates_removed": duplicates_removed,
        "invalid_items_removed": invalid_items_removed,
        "items_extracted": len(items),
    }

    if not items:
        warnings.append("No products could be extracted from HTML")
        return {
            "success": False,
            "source": source,
            "source_url": source_url,
            "items": [],
            "item_count": 0,
            "warnings": warnings,
            "debug_summary": debug_summary,
        }

    return {
        "success": True,
        "source": source,
        "source_url": source_url,
        "items": items,
        "item_count": len(items),
        "warnings": warnings,
        "debug_summary": debug_summary,
    }


async def fetch_pricing_html() -> dict[str, Any]:
    settings = get_settings()
    url = settings.PRICING_API_URL

    try:
        async with httpx.AsyncClient(timeout=PRICING_FETCH_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
    except httpx.RequestError as exc:
        return {
            "success": False,
            "error": f"Pricing HTML fetch failed: {exc}",
            "url": url,
        }

    content_type = response.headers.get("content-type", "")
    result: dict[str, Any] = {
        "success": response.status_code == 200,
        "url": url,
        "status_code": response.status_code,
        "content_type": content_type,
        "html": response.text,
    }

    if response.status_code != 200:
        result["error"] = f"Pricing HTML fetch failed: HTTP {response.status_code}"
        result["success"] = False
        return result

    if "text/html" not in content_type.lower():
        result["warning"] = (
            f"Expected HTML but received content-type: {content_type or 'unknown'}"
        )

    return result


async def scrape_pricing_page() -> dict[str, Any]:
    fetch_result = await fetch_pricing_html()
    if not fetch_result.get("success"):
        return {
            "success": False,
            "source": "html_scraper",
            "source_url": fetch_result.get("url"),
            "items": [],
            "item_count": 0,
            "warnings": [],
            "error": fetch_result.get("error", "Failed to fetch pricing HTML"),
            "fetch": {
                "success": False,
                "status_code": fetch_result.get("status_code"),
                "content_type": fetch_result.get("content_type"),
            },
        }

    scraped = scrape_pricing_from_html(
        fetch_result["html"],
        fetch_result.get("url", ""),
        source="html_scraper",
    )
    scraped["fetch"] = {
        "success": True,
        "status_code": fetch_result.get("status_code"),
        "content_type": fetch_result.get("content_type"),
        "html_received": bool(fetch_result.get("html")),
    }
    return scraped


def preview_scraper_result(result: dict[str, Any], *, preview_count: int = 5) -> dict[str, Any]:
    preview = dict(result)
    items = []
    for item in (result.get("items") or [])[:preview_count]:
        copied = dict(item)
        copied["raw_text"] = _truncate_raw_text(str(copied.get("raw_text") or ""))
        items.append(copied)
    preview["items"] = items
    preview.pop("html", None)
    return preview
