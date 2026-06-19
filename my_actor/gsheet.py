"""
src/gsheet.py
Google Sheets upload — credentials come from actor input dict,
not a local file path (Apify actors have no persistent filesystem).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import gspread
import pytz
from google.oauth2 import service_account


def _extract_workbook_id(link: str) -> str | None:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", link)
    if not match:
        return None
    wb_id = match.group(1)
    return wb_id if len(wb_id) >= 33 else None


async def upload_to_google_sheet(
    link: str,
    credentials_dict: dict,       # full service-account JSON as a dict (from actor input)
    scopes: list[str],
    sheet_name: str,
    jobs: list[dict],             # list of job dicts already in memory
    log: Any,
) -> None:
    """
    Upload scraped jobs to Google Sheets.

    credentials_dict — the service account JSON parsed into a Python dict.
    Pass it directly in actor input as `google_sheets_credentials`.
    jobs             — all scraped jobs buffered in config._saved_jobs.
    """
    # ── Auth ──────────────────────────────────────────────────────────────────
    try:
        creds    = service_account.Credentials.from_service_account_info(
            credentials_dict, scopes=scopes
        )
        client   = gspread.authorize(creds)
        wb_id    = _extract_workbook_id(link)
        if not wb_id:
            log.error(f"❌ Invalid Google Sheet URL — cannot extract workbook ID: {link}")
            return
        workbook = client.open_by_key(wb_id)
    except Exception as e:
        log.error(f"❌ Google Sheet auth error: {e}")
        return

    # ── Split by apply type ───────────────────────────────────────────────────
    export_fields = [
        "position", "company", "url", "salary", "jt0", "is_remote",
        "location", "apply_type", "benefits", "description", "rating",
        "review_count", "job_match", "job_id", "external_apply_link",
        "ignore_related", "is_expired",
    ]

    easy_jobs = sorted(
        [j for j in jobs if j.get("apply_type") == "Easy Apply"],
        key=lambda j: j.get("job_match", 0), reverse=True,
    )
    cs_jobs = sorted(
        [j for j in jobs if j.get("apply_type") == "CS Apply"],
        key=lambda j: j.get("job_match", 0), reverse=True,
    )

    # ── Get / create worksheet ────────────────────────────────────────────────
    try:
        worksheet = workbook.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = workbook.add_worksheet(title=sheet_name, rows=5000, cols=40)
        log.warning(f"📰 Created new worksheet: {sheet_name}")

    # ── Build rows ────────────────────────────────────────────────────────────
    def jobs_to_rows(job_list: list[dict]) -> list[list]:
        rows = []
        for job in job_list:
            row = []
            for f in export_fields:
                val = job.get(f, "")
                if hasattr(val, "strftime"):
                    val = val.strftime("%m/%d/%Y %I:%M %p")
                row.append(val if val is not None else "")
            rows.append(row)
        return rows

    pk_tz    = pytz.timezone("Asia/Karachi")
    now      = datetime.now(pk_tz)
    date_str = now.strftime("%m/%d/%Y")
    time_str = now.strftime("%I:%M %p")

    all_rows = (
        [["Easy Apply", time_str, date_str]]
        + jobs_to_rows(easy_jobs)
        + [[]]
        + [["CS Apply", time_str, date_str]]
        + jobs_to_rows(cs_jobs)
    )

    try:
        existing  = worksheet.get_all_values()
        start_row = len(existing) + 2
        worksheet.update(range_name=f"A{start_row}", values=all_rows)
        log.info(
            f"✅ Wrote {len(easy_jobs)} Easy Apply + {len(cs_jobs)} CS Apply jobs | {link}"
        )
    except Exception as e:
        log.error(f"❌ Error writing to Google Sheet: {e}")
        return

    # ── Formatting ────────────────────────────────────────────────────────────
    try:
        sheet_id = worksheet._properties["sheetId"]
        num_rows = len(all_rows)
        workbook.batch_update({
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId":       sheet_id,
                            "startRowIndex": start_row - 1,
                            "endRowIndex":   start_row - 1 + num_rows,
                        },
                        "cell":   {"userEnteredFormat": {"wrapStrategy": "OVERFLOW_CELL"}},
                        "fields": "userEnteredFormat.wrapStrategy",
                    }
                },
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId":    sheet_id,
                            "dimension":  "ROWS",
                            "startIndex": start_row - 1,
                            "endIndex":   start_row - 1 + num_rows,
                        },
                        "properties": {"pixelSize": 21},
                        "fields":     "pixelSize",
                    }
                },
            ]
        })
    except Exception as e:
        log.warning(f"⚠️ Row formatting warning: {e}")
