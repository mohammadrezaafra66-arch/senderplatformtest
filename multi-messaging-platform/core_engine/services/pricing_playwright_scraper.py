"""اسکرپر موقت Playwright برای صفحات قیمت JavaScript-rendered."""

from __future__ import annotations

import os
import re
from typing import Any

from core_engine.config import get_settings
from core_engine.services.pricing_scraper import (
    _truncate_raw_text,
    clean_text,
    merge_scraped_pages,
    normalize_persian_digits,
    scrape_pricing_from_html,
)

AUTH_MARKERS = (
    "در حال بررسی جلسه کاربری",
    "در حال بررسی دسترسی",
    "بدون نقش",
    "ورود",
    "login",
    "sign in",
    "signin",
    "authenticate",
    "authentication",
    "احراز هویت",
    "وارد شوید",
    "session",
)

TABLE_SELECTORS = (
    "table",
    "[role='table']",
    "table tbody tr",
)


def _detect_auth_required(title: str, html: str, visible_text: str) -> bool:
    combined = clean_text(f"{title}\n{visible_text}\n{html[:5000]}").lower()
    if "در حال بررسی جلسه کاربری" in combined:
        return True
    if "در حال بررسی دسترسی" in combined:
        return True
    if "بدون نقش" in combined and "موردی برای نمایش وجود ندارد" in combined:
        return True

    auth_patterns = (
        r"\blogin\b",
        r"\bsign[\s-]?in\b",
        r"احراز\s*هویت",
        r"وارد\s*شوید",
    )
    for pattern in auth_patterns:
        if re.search(pattern, combined, re.IGNORECASE):
            return True

    if "password" in combined and ("username" in combined or "email" in combined):
        return True

    return False


async def _wait_for_pricing_table(page, timeout_ms: int) -> bool:
    for selector in TABLE_SELECTORS:
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            continue
    return False


async def _scroll_pricing_table_for_more_rows(page, wait_ms: int, max_scrolls: int = 15) -> int:
    previous_count = 0
    for _ in range(max_scrolls):
        current_count = await page.locator("table tr:has(td)").count()
        if current_count > 0 and current_count <= previous_count:
            break
        previous_count = current_count
        await page.evaluate(
            """
            () => {
                const table = document.querySelector('table');
                if (table) {
                    let parent = table.parentElement;
                    while (parent && parent !== document.body) {
                        if (parent.scrollHeight > parent.clientHeight + 20) {
                            parent.scrollTop = parent.scrollHeight;
                            return;
                        }
                        parent = parent.parentElement;
                    }
                    table.scrollIntoView({ block: 'end' });
                }
                window.scrollTo(0, document.body.scrollHeight);
            }
            """
        )
        await page.wait_for_timeout(wait_ms)
    return previous_count


async def _collect_page_skus(page) -> set[str]:
    skus: set[str] = set()
    rows = page.locator("table tr:has(td)")
    count = await rows.count()
    for index in range(count):
        row = rows.nth(index)
        cells = row.locator("td")
        cell_count = await cells.count()
        if cell_count < 5:
            continue
        sku_index = 4
        if cell_count >= 11:
            sku_index = 4
        sku_text = clean_text(await cells.nth(sku_index).inner_text()).upper()
        if sku_text.startswith("AFK-"):
            skus.add(sku_text)
    return skus


async def _read_pagination_state(page) -> dict[str, int]:
    spans = page.locator("span")
    count = await spans.count()
    for index in range(count):
        text = clean_text(await spans.nth(index).inner_text())
        normalized = normalize_persian_digits(text)
        match = re.search(r"صفحه\s*(\d+)\s*از\s*(\d+)", normalized)
        if match:
            return {
                "current_page": int(match.group(1)),
                "total_pages": int(match.group(2)),
            }
    return {}


async def _find_next_page_button(page):
    pagination_container = page.locator("div.flex.items-center.gap-2").filter(
        has_text=re.compile(r"صفحه")
    )
    if await pagination_container.count() > 0:
        enabled_buttons = pagination_container.locator("button:not([disabled])")
        if await enabled_buttons.count() > 0:
            return enabled_buttons.last

    selectors = [
        "div:has(span:text-matches('صفحه')) button:not([disabled]):has(svg.lucide-chevron-left)",
        "button:not([disabled]):has(svg.lucide-chevron-left)",
        "button:has-text('بعدی')",
        "button:has-text('Next')",
        "button:has-text('next')",
        "[aria-label*='بعدی']",
        "[aria-label*='next' i]",
        "[aria-label*='Next' i]",
        "table ~ nav button:not([disabled])",
        ".pagination button:not([disabled])",
        "[class*='pagination'] button:not([disabled])",
        "nav button:not([disabled])",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        count = await locator.count()
        for index in range(count):
            button = locator.nth(index)
            try:
                if not await button.is_visible():
                    continue
                if not await button.is_enabled():
                    continue
                text = clean_text(await button.inner_text()).lower()
                aria = clean_text(await button.get_attribute("aria-label") or "").lower()
                label = f"{text} {aria}"
                if any(token in label for token in ("بعدی", "next", "›", "»", "→", "chevron")):
                    return button
            except Exception:
                continue

    pagination_buttons = page.locator(
        "table ~ * button:not([disabled]), [class*='pagination'] button:not([disabled])"
    )
    count = await pagination_buttons.count()
    if count >= 2:
        return pagination_buttons.nth(count - 1)
    return None


async def _has_next_page_button(page) -> bool:
    state = await _read_pagination_state(page)
    if state:
        return state["current_page"] < state["total_pages"]

    button = await _find_next_page_button(page)
    if button is None:
        return False
    try:
        return await button.is_enabled()
    except Exception:
        return False


async def _click_next_page(page) -> bool:
    button = await _find_next_page_button(page)
    if button is None:
        return False
    try:
        if not await button.is_enabled():
            return False
        await button.click()
        return True
    except Exception:
        return False


async def _wait_for_table_change(
    page,
    previous_skus: set[str],
    wait_ms: int,
    *,
    previous_page: int | None = None,
) -> bool:
    await page.wait_for_timeout(wait_ms)
    for _ in range(5):
        state = await _read_pagination_state(page)
        if (
            previous_page is not None
            and state.get("current_page")
            and state["current_page"] > previous_page
        ):
            return True
        current_skus = await _collect_page_skus(page)
        if current_skus and current_skus != previous_skus:
            return True
        await page.wait_for_timeout(wait_ms)
    return False


async def scrape_all_pricing_pages_with_playwright(
    max_pages: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    url = settings.PRICING_API_URL
    source = "playwright_html_scraper"
    resolved_max_pages = max_pages if max_pages is not None else settings.PRICING_PLAYWRIGHT_MAX_PAGES
    page_wait_ms = settings.PRICING_PLAYWRIGHT_PAGE_WAIT_MS
    timeout_ms = settings.PRICING_PLAYWRIGHT_TIMEOUT_MS
    headless = settings.PRICING_PLAYWRIGHT_HEADLESS
    profile_dir = os.path.abspath(settings.PRICING_PLAYWRIGHT_PROFILE_DIR)
    os.makedirs(profile_dir, exist_ok=True)

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        return {
            "success": False,
            "source": source,
            "source_url": url,
            "auth_required": False,
            "items": [],
            "item_count": 0,
            "warnings": [],
            "error": f"Playwright is not installed: {exc}",
        }

    try:
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=headless,
                locale="fa-IR",
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                except Exception:
                    await page.wait_for_timeout(min(timeout_ms, 5000))

                title = await page.title()
                visible_text = clean_text(await page.inner_text("body"))
                html = await page.content()
                auth_required = _detect_auth_required(title, html, visible_text)
                if auth_required:
                    return {
                        "success": False,
                        "source": source,
                        "source_url": url,
                        "auth_required": True,
                        "items": [],
                        "item_count": 0,
                        "warnings": [
                            "Pricing page requires authentication or session is not ready",
                        ],
                        "page_title": title,
                        "visible_text_preview": visible_text[:500],
                        "debug_summary": {
                            "tables_found": 0,
                            "pagination_detected": False,
                            "pages_scraped": 0,
                            "rows_checked": 0,
                            "valid_table_rows": 0,
                            "invalid_rows": 0,
                            "block_scraping_used": False,
                            "items_extracted_before_dedupe": 0,
                            "duplicates_removed": 0,
                            "invalid_items_removed": 0,
                            "items_extracted": 0,
                            "playwright_rendered": True,
                        },
                    }

                await _wait_for_pricing_table(page, timeout_ms)
                page_results: list[dict[str, Any]] = []
                pagination_detected = False
                pages_scraped = 0
                seen_page_signatures: set[frozenset[str]] = set()

                for page_number in range(1, resolved_max_pages + 1):
                    await _scroll_pricing_table_for_more_rows(page, page_wait_ms)
                    current_skus = await _collect_page_skus(page)
                    signature = frozenset(current_skus)
                    if signature and signature in seen_page_signatures:
                        break
                    if signature:
                        seen_page_signatures.add(signature)

                    html = await page.content()
                    page_result = scrape_pricing_from_html(
                        html,
                        url,
                        source=source,
                        page_number=page_number,
                        allow_block_scraping=False,
                        dedupe=False,
                    )
                    page_results.append(page_result)
                    pages_scraped = page_number

                    if page_number >= resolved_max_pages:
                        break

                    has_next = await _has_next_page_button(page)
                    if not has_next:
                        break

                    previous_page_state = await _read_pagination_state(page)
                    previous_page_number = previous_page_state.get("current_page")
                    previous_skus = set(current_skus)
                    clicked = await _click_next_page(page)
                    if not clicked:
                        break

                    pagination_detected = True
                    changed = await _wait_for_table_change(
                        page,
                        previous_skus,
                        page_wait_ms,
                        previous_page=previous_page_number,
                    )
                    if not changed and not await _has_next_page_button(page):
                        break

                merged = merge_scraped_pages(
                    page_results,
                    pagination_detected=pagination_detected,
                    pages_scraped=pages_scraped,
                )
                merged["auth_required"] = False
                merged["page_title"] = title
                merged["visible_text_preview"] = visible_text[:500]
                merged["rendered"] = True
                if merged.get("debug_summary") is not None:
                    merged["debug_summary"]["playwright_rendered"] = True
                    if pagination_detected is False and pages_scraped <= 1:
                        merged["warnings"] = list(merged.get("warnings") or [])
                        if not any("pagination" in w.lower() for w in merged["warnings"]):
                            merged["warnings"].append(
                                "pagination_detected=false; only the first table page was scraped"
                            )
                return merged
            finally:
                await context.close()
    except Exception as exc:
        return {
            "success": False,
            "source": source,
            "source_url": url,
            "auth_required": False,
            "items": [],
            "item_count": 0,
            "warnings": [],
            "error": f"Playwright fetch failed: {exc}",
        }


async def scrape_pricing_with_playwright() -> dict[str, Any]:
    """سازگاری با کد قبلی — همه صفحات طبق تنظیمات پیش‌فرض."""
    return await scrape_all_pricing_pages_with_playwright()


def preview_playwright_scraper_result(
    result: dict[str, Any],
    *,
    preview_count: int = 5,
) -> dict[str, Any]:
    items = []
    for item in (result.get("items") or [])[:preview_count]:
        copied = dict(item)
        copied["raw_text"] = _truncate_raw_text(str(copied.get("raw_text") or ""))
        items.append(copied)

    return {
        "success": bool(result.get("success")),
        "source": result.get("source", "playwright_html_scraper"),
        "auth_required": bool(result.get("auth_required")),
        "item_count": int(result.get("item_count") or 0),
        "items": items,
        "warnings": list(result.get("warnings") or []),
        "debug_summary": result.get("debug_summary"),
        "page_title": result.get("page_title"),
        "error": result.get("error"),
        "visible_text_preview": result.get("visible_text_preview"),
    }


def get_playwright_profile_status() -> dict[str, Any]:
    """وضعیت پوشه profile — بدون نمایش cookie یا session data."""
    settings = get_settings()
    profile_dir = os.path.abspath(settings.PRICING_PLAYWRIGHT_PROFILE_DIR)
    exists = os.path.isdir(profile_dir)
    file_count = 0
    if exists:
        for _root, _dirs, files in os.walk(profile_dir):
            file_count += len(files)

    return {
        "profile_dir": profile_dir,
        "exists": exists,
        "has_files": file_count > 0,
        "file_count": file_count,
    }
