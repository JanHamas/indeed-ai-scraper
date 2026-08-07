"""
my_actor/gsheet.py
Google Sheets upload — service account mode (credentials hardcoded per user request).
"""
from __future__ import annotations

import json
import re
from typing import Any

import gspread

# ⚠️ Hardcoded service account credential — keep this file out of any public repo.
_SERVICE_ACCOUNT_INFO = {
    "type": "service_account",
    "project_id": "indeed-leads-467810",
    "private_key_id": "65119335ce7dd5205524aea8d3ac721e3a8c6bdc",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCoHA7FAB1Dt7bc\nuSPzKg5tPTr6Y2qT23LgCkggG/UV8jlAH2gKaUuDF9F3axxn55gjZPnn0BaOJcT9\nxLzHTcQ8uvp8QjBJKcGbiwKCv5HQVrZT3ZqQ+GSfWImYLIX8XobkKo/mFERj7708\nyJrajOmZg2Bf+cwLlQT9XRAkIu8Kg2Zgo9MlZGVTqqYlcg+Hu6Ehatu3YDzxJgaL\nlHImI/Z4vXuBS4Ehxqcjd+AxD3baYEyjFlnJbQrdhGz9PKLnhYmwUIm5jPB3rrKK\n2FpR32BYORKP8DoXy/3MiMczlt/Iki2HLmU3yzr4a9GY1ng6dfar8VZVBegx3N8E\n9DWIKTCDAgMBAAECggEAUN7GgCCQ822ia0YpNCYMMKLfB2rh72UHOx3XGpM4cKlI\nvz8chr2mlNbVbnQ5gwaXWpeN3OVupE0pGccV5W7Usz5jl3kjz15mA9R4jbtogufj\n6C5X19uWVE18S5dHkWNL0uFivhUL3WOMDnyWegKFsdLQfvslHIFAmD23sRqZEd3i\nO0f16RH1xHZRzT7D3Q7EHdjMCEoOfFkC+RZFNUgl1yhq49zg8/XMFMd1D3DGvdRz\naxSRqfJj6Jiyf5xb7PrdOG5Z19KX9GXJWYPFbph7TCAMueH9UAbTWbmS3inRCVMl\nJY2WB0nW2ROAM4V+18KgKeahapYKc4h+A8QN/yNXAQKBgQDe4sa8v1/OaAclmtiv\nKtQn0H7vPT8FEHC3+3RO0lk28E0A28+rfmviqLaHHQjsaCMmfsnHYJOJ9Wgb4fZr\nTy1Sy6aoMx+ag1MpuqK32tOFundKxSDRO48LZ8I8HMhp07IAcu/SDWj3FGEdMgbN\n3FWBqGb9d27rXCWsReKx18H3AwKBgQDBFez+OZYRt1Y+/fKVBc/TtAb0zrkz3Poj\nQhLqjDDXGliSxuAJgIN5DEUUli7IPBW5aQ0sP1LJD7LtH0dTQ/BK8GzEdk4oG+NP\nuA9Ab+bBI4gSmC0Qe1lMhX3jfnHmJEM+oxPsfKH1IqcJijV2mbdBMhgcr7Mn27FC\nDwJt6NvogQKBgQDLNUjqdzS7M/8oGuhptAufjSjdqCJX1KhgJYLiBkfOngImwUGy\nYl2sVhOsVh13pG4/v6LqAzQguLzFhxuqtJJnBUOZ4Jp0vjzJM9787yagquPuKJIG\nEV4WkO/27GsQiTCl1iSuhwlnE46DFsQ7ViIjR0021HgtX2L9kO3hJQwzzwKBgBvy\nnoEVuMELtnIbs9caJkDQWAlrOqdlHtenorFMZW1dJODp5Fe7wEvvGLioHFFjUQmr\nSPrUl5j+qrljw6ErvkY6kqPFM/7eOoK5c4uyJsZh7Do0yKEQGZbX46bgEIBtU0Zn\nuGSMjay7vU3GMYLfMQVAyPit/dKRHBEhtwpNoDcBAoGBALMjeALiNh/t5D9edZCX\nQlNiT/OYG002XIA39GHO1Q48Z45MrZXDUHzAoZhwiW6xQAWaXQqwGumBh5H/qzr3\nzu9grkKh//eVjtWTcYcOnGF9+aqViMpxwD/plDp8/NtTUSRp57UnD6KB5MUwp4B5\ny7C0jXNE24kU5AnikDmE2B+d\n-----END PRIVATE KEY-----\n",
    "client_email": "python-api@indeed-leads-467810.iam.gserviceaccount.com",
    "client_id": "116252941819710743933",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/python-api%40indeed-leads-467810.iam.gserviceaccount.com",
    "universe_domain": "googleapis.com",
}

# ── All fields we want written to the sheet, in column order. Supports
# "parent/child" paths (e.g. "searchInput/country") to pull a value out of
# a nested dict without flattening the whole job record. ────────────────────
EXTRACTION_FIELDS: list[str] = [
    "id", "url", "positionName", "company", "companyIndeedUrl",
    "location", "salary", "jobType", "isRemote", "description",
    "descriptionHTML", "postedAt", "postingDateParsed", "applyType",
    "externalApplyLink", "benefits", "rating", "reviewsCount",
    "isExpired", "jobMatch", "scrapedAt",
    "searchInput/country", "searchInput/location", "searchInput/position",
    "searchInput",
]


def _extract_workbook_id(link: str) -> str | None:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", link)
    if not match:
        return None
    wb_id = match.group(1)
    return wb_id if len(wb_id) >= 33 else None


def _get_field(job: dict, field: str) -> Any:
    """Look up `field` on `job`, supporting 'parent/child' nested paths."""
    if "/" in field:
        parent, child = field.split("/", 1)
        parent_val = job.get(parent)
        if isinstance(parent_val, dict):
            return parent_val.get(child, "")
        return ""
    return job.get(field, "")


def _cell_value(value: Any) -> str:
    """Coerce any job field value into something Sheets can display."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value)
    return str(value)


def _job_to_row(job: dict) -> list[str]:
    return [_cell_value(_get_field(job, field)) for field in EXTRACTION_FIELDS]


async def upload_to_google_sheet(
    link: str,
    jobs: list[dict],
    log: Any,
) -> None:
    """
    Upload scraped jobs to a Google Sheet via service account.
    Users must share their sheet with:
      python-api@indeed-leads-467810.iam.gserviceaccount.com  (Editor)

    Every job in `jobs` gets a row, regardless of its `applyType` — jobs are
    just grouped (Easy Apply, then CS Apply, then everything else) and
    sorted by jobMatch within each group for readability. Nothing is
    dropped for not matching an expected applyType value.
    """
    sheet_name = "Indeed_jobs"

    wb_id = _extract_workbook_id(link)
    if not wb_id:
        log.error(f"❌ Invalid Google Sheet URL — cannot extract workbook ID: {link}")
        return

    if not jobs:
        log.warning("⚠️ No jobs to upload to Google Sheets")
        return

    try:
        client = gspread.service_account_from_dict(_SERVICE_ACCOUNT_INFO)
        workbook = client.open_by_key(wb_id)
    except Exception as e:
        log.error(
            f"❌ Could not open Google Sheet. "
            f"Make sure it is shared with python-api@indeed-leads-467810.iam.gserviceaccount.com as Editor. Error: {e}"
        )
        return

    try:
        worksheet = workbook.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = workbook.add_worksheet(title=sheet_name, rows=5000, cols=40)
        log.info(f"📰 Created new worksheet: {sheet_name}")

    def sort_key(job: dict) -> float:
        try:
            return float(job.get("jobMatch") or 0)
        except (TypeError, ValueError):
            return 0.0

    easy_jobs  = sorted((j for j in jobs if j.get("applyType") == "Easy Apply"), key=sort_key, reverse=True)
    cs_jobs    = sorted((j for j in jobs if j.get("applyType") == "CS Apply"), key=sort_key, reverse=True)
    seen_ids   = {id(j) for j in easy_jobs} | {id(j) for j in cs_jobs}
    other_jobs = sorted((j for j in jobs if id(j) not in seen_ids), key=sort_key, reverse=True)

    header = EXTRACTION_FIELDS
    all_rows: list[list[str]] = [header]

    def add_group(label: str, group: list[dict]) -> None:
        if not group:
            return
        all_rows.append([f"── {label} ──"] + [""] * (len(header) - 1))
        all_rows.extend(_job_to_row(job) for job in group)

    add_group("Easy Apply", easy_jobs)
    add_group("CS Apply", cs_jobs)
    add_group("Other", other_jobs)

    try:
        worksheet.clear()
        worksheet.update("A1", all_rows)
        log.info(
            f"✅ Uploaded {len(jobs)} jobs to Google Sheets tab '{sheet_name}' "
            f"({len(easy_jobs)} Easy Apply, {len(cs_jobs)} CS Apply, {len(other_jobs)} other)"
        )
    except Exception as e:
        log.error(f"❌ Failed to write to Google Sheet: {e}")