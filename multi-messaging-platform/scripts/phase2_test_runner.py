"""Phase 2 end-to-end test runner for import and campaign draft flows."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from openpyxl import Workbook

BASE_URL = "http://localhost:8001"
TEST_DIR = Path(__file__).resolve().parents[1] / "test_fixtures" / "phase2"
RESULTS: list[dict] = []
_PHONE_SEQ = 1_000_000


def unique_phone() -> str:
    global _PHONE_SEQ
    _PHONE_SEQ += 1
    return f"0912{_PHONE_SEQ:07d}"


def record(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "passed": passed, "detail": detail})
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def make_xlsx(filename: str, headers: list[str], rows: list[list]) -> Path:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    path = TEST_DIR / filename
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def preview_file(path: Path) -> tuple[int, dict]:
    with path.open("rb") as handle:
        response = httpx.post(
            f"{BASE_URL}/imports/contacts/preview",
            files={"file": (path.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=30,
        )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}
    return response.status_code, body


def commit_file(preview_body: dict) -> tuple[int, dict]:
    payload = {
        "file_path": preview_body["file_path"],
        "original_file_name": preview_body["original_file_name"],
        "stored_file_name": preview_body["stored_file_name"],
        "sheet_name": preview_body.get("sheet_name"),
        "uploaded_by": "phase2_test",
    }
    response = httpx.post(
        f"{BASE_URL}/imports/contacts/commit",
        json=payload,
        timeout=30,
    )
    return response.status_code, response.json()


def campaign_from_import(import_batch_id: int) -> tuple[int, dict]:
    payload = {
        "import_batch_id": import_batch_id,
        "title": "Phase2 Test Campaign",
        "platform": "bale",
        "template_text": "سلام {first_name}",
        "use_gpt": False,
        "include_products": False,
    }
    response = httpx.post(f"{BASE_URL}/campaigns/from-import", json=payload, timeout=30)
    return response.status_code, response.json()


def test_health() -> None:
    response = httpx.get(f"{BASE_URL}/health", timeout=10)
    record("health", response.status_code == 200 and response.json().get("status") == "ok")


def test_valid_excel() -> int | None:
    p1, p2 = unique_phone(), unique_phone()
    path = make_xlsx(
        "valid_contacts.xlsx",
        ["نام", "نام خانوادگی", "موبایل"],
        [["علی", "احمدی", p1], ["رضا", "محمدی", p2]],
    )
    status, body = preview_file(path)
    preview = body.get("preview", {})
    ok = (
        status == 200
        and preview.get("valid_rows_count") == 2
        and preview.get("errors") == []
    )
    record("1_valid_excel_preview", ok, f"status={status}, valid={preview.get('valid_rows_count')}")
    if not ok:
        return None

    status, commit = commit_file(body)
    ok = status == 200 and commit.get("status") == "committed" and commit.get("created_contacts_count") == 2
    record("1_valid_excel_commit", ok, json.dumps(commit, ensure_ascii=False))
    if not ok:
        return None

    batch_id = commit["import_batch_id"]
    status, camp = campaign_from_import(batch_id)
    ok = status == 200 and camp.get("contacts_attached_count") == 2
    record("1_valid_excel_campaign", ok, json.dumps(camp, ensure_ascii=False))
    return batch_id


def test_invalid_phone() -> None:
    path = make_xlsx(
        "invalid_phone.xlsx",
        ["نام", "نام خانوادگی", "موبایل"],
        [["خراب", "تست", "12345"], ["سالم", "کاربر", unique_phone()]],
    )
    status, body = preview_file(path)
    rows = body.get("preview", {}).get("rows", [])
    invalid = [r for r in rows if r.get("error_code") == "invalid_phone"]
    valid = [r for r in rows if r.get("status") == "valid"]
    record(
        "2_invalid_phone_preview",
        status == 200 and len(invalid) == 1 and len(valid) == 1,
        f"invalid={len(invalid)}, valid={len(valid)}",
    )

    status, commit = commit_file(body)
    record(
        "2_invalid_phone_commit",
        status == 200 and commit.get("created_contacts_count") == 1,
        json.dumps(commit, ensure_ascii=False),
    )


def test_missing_phone() -> None:
    path = make_xlsx(
        "missing_phone.xlsx",
        ["نام", "نام خانوادگی", "موبایل"],
        [["بدون", "شماره", ""], ["دارای", "شماره", unique_phone()]],
    )
    status, body = preview_file(path)
    rows = body.get("preview", {}).get("rows", [])
    missing = [r for r in rows if r.get("error_code") == "missing_phone"]
    record(
        "3_missing_phone_preview",
        status == 200 and len(missing) == 1,
        f"missing={len(missing)}",
    )
    status, commit = commit_file(body)
    record(
        "3_missing_phone_commit",
        status == 200 and commit.get("created_contacts_count") == 1,
        json.dumps(commit, ensure_ascii=False),
    )


def test_internal_duplicate() -> None:
    phone = unique_phone()
    path = make_xlsx(
        "internal_duplicate.xlsx",
        ["نام", "نام خانوادگی", "موبایل"],
        [["اول", "کاربر", phone], ["دوم", "تکراری", phone]],
    )
    status, body = preview_file(path)
    rows = body.get("preview", {}).get("rows", [])
    dup = [r for r in rows if r.get("status") == "duplicate"]
    valid = [r for r in rows if r.get("status") == "valid"]
    record(
        "4_internal_duplicate_preview",
        status == 200 and len(dup) == 1 and len(valid) == 1,
        f"dup={len(dup)}, valid={len(valid)}",
    )
    status, commit = commit_file(body)
    record(
        "4_internal_duplicate_commit",
        status == 200 and commit.get("created_contacts_count") == 1,
        json.dumps(commit, ensure_ascii=False),
    )


def test_db_duplicate() -> None:
    phone = unique_phone()
    path = make_xlsx(
        "db_duplicate.xlsx",
        ["نام", "نام خانوادگی", "موبایل"],
        [["تکرار", "دیتابیس", phone]],
    )
    status1, body1 = preview_file(path)
    status2, commit1 = commit_file(body1)
    _, commit2 = commit_file(body1)
    record(
        "5_db_duplicate",
        commit1.get("created_contacts_count") == 1
        and commit2.get("created_contacts_count") == 0
        and commit2.get("duplicate_rows_count") == 1,
        f"commit1={json.dumps(commit1, ensure_ascii=False)} commit2={json.dumps(commit2, ensure_ascii=False)}",
    )


def test_missing_phone_column() -> None:
    path = make_xlsx(
        "no_phone_column.xlsx",
        ["نام", "نام خانوادگی"],
        [["علی", "احمدی"], ["رضا", "محمدی"]],
    )
    status, body = preview_file(path)
    errors = body.get("preview", {}).get("errors", [])
    codes = [e.get("code") for e in errors]
    record(
        "6_missing_phone_column",
        status == 200 and "missing_required_phone_column" in codes,
        f"status={status}, errors={errors}",
    )


def test_invalid_extension() -> None:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    path = TEST_DIR / "bad_file.txt"
    path.write_text("not an excel file", encoding="utf-8")
    with path.open("rb") as handle:
        response = httpx.post(
            f"{BASE_URL}/imports/contacts/preview",
            files={"file": (path.name, handle, "text/plain")},
            timeout=10,
        )
    record("7_invalid_extension", response.status_code == 400, f"status={response.status_code}")


def test_oversized_file() -> None:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    path = TEST_DIR / "oversized.xlsx"
  # minimal xlsx header then pad
    make_xlsx("oversized_base.xlsx", ["نام", "موبایل"], [["تست", "09120000000"]])
    base = TEST_DIR / "oversized_base.xlsx"
    data = base.read_bytes()
    path.write_bytes(data + b"0" * (21 * 1024 * 1024))
    with path.open("rb") as handle:
        response = httpx.post(
            f"{BASE_URL}/imports/contacts/preview",
            files={"file": (path.name, handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            timeout=60,
        )
    record("8_oversized_file", response.status_code == 413, f"status={response.status_code}")


def test_blacklisted_and_opted_out() -> None:
    p1, p2 = unique_phone(), unique_phone()
    path = make_xlsx(
        "campaign_filter.xlsx",
        ["نام", "نام خانوادگی", "موبایل"],
        [["فیلتر", "یک", p1], ["فیلتر", "دو", p2]],
    )
    status, body = preview_file(path)
    if status != 200:
        record("9_blacklisted", False, f"preview failed status={status}")
        record("10_opted_out", False, "skipped due to preview failure")
        return

    status, commit = commit_file(body)
    batch_id = commit.get("import_batch_id")
    if status != 200 or not batch_id:
        record("9_blacklisted", False, json.dumps(commit, ensure_ascii=False))
        record("10_opted_out", False, "skipped due to commit failure")
        return

    import subprocess

    subprocess.run(
        [
            "docker", "exec", "mmp_postgres", "psql", "-U", "mmp_user", "-d", "mmp_db",
            "-c",
            f"UPDATE contacts SET blacklisted = true WHERE source_import_id = {batch_id} "
            f"AND phone_e164 = (SELECT phone_e164 FROM contacts WHERE source_import_id = {batch_id} ORDER BY id LIMIT 1);",
        ],
        check=True,
        capture_output=True,
    )
    status, camp = campaign_from_import(batch_id)
    ok_blacklisted = (
        status == 200
        and camp.get("contacts_attached_count") == 1
        and camp.get("skipped_contacts_count") == 1
    )
    record(
        "9_blacklisted",
        ok_blacklisted,
        json.dumps(camp, ensure_ascii=False),
    )

    subprocess.run(
        [
            "docker", "exec", "mmp_postgres", "psql", "-U", "mmp_user", "-d", "mmp_db",
            "-c",
            f"UPDATE contacts SET blacklisted = false, consent_status = 'blocked' "
            f"WHERE source_import_id = {batch_id} AND phone_e164 = (SELECT phone_e164 FROM contacts WHERE source_import_id = {batch_id} ORDER BY id LIMIT 1);",
        ],
        check=True,
        capture_output=True,
    )
    status, camp2 = campaign_from_import(batch_id)
    ok_opted_out = (
        status == 200
        and camp2.get("contacts_attached_count") == 1
        and camp2.get("skipped_contacts_count") == 1
    )
    record(
        "10_opted_out",
        ok_opted_out,
        json.dumps(camp2, ensure_ascii=False),
    )


def main() -> int:
    test_health()
    batch_id = test_valid_excel()
    test_invalid_phone()
    test_missing_phone()
    test_internal_duplicate()
    test_db_duplicate()
    test_missing_phone_column()
    test_invalid_extension()
    test_oversized_file()
    test_blacklisted_and_opted_out()

    passed = sum(1 for r in RESULTS if r["passed"])
    total = len(RESULTS)
    print(f"\n=== SUMMARY: {passed}/{total} passed ===")
    report_path = Path(__file__).resolve().parents[1] / "docs" / "phase2_test_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Phase 2 Test Report\n", f"Summary: **{passed}/{total} passed**\n\n"]
    for item in RESULTS:
        mark = "✅" if item["passed"] else "❌"
        lines.append(f"- {mark} **{item['name']}**")
        if item["detail"]:
            lines.append(f"  - {item['detail']}")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {report_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
