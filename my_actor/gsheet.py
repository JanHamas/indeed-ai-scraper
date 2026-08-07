"""
my_actor/gsheet.py
Google Sheets upload — PUBLIC URL mode.
The sheet must be shared as "Anyone with the link can edit".
No service account credentials required.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import gspread
import requests
from google.auth.credentials import AnonymousCredentials
from gspread import Client


def _extract_workbook_id(link: str) -> str | None:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", link)
    if not match:
        return None
    wb_id = match.group(1)
    return wb_id if len(wb_id) >= 33 else None


async def upload_to_google_sheet(
    link: str,
    jobs: list[dict],
    log: Any,
) -> None:
    sheet_name = "Indeed_jobs"
    """
    Upload scraped jobs to a publicly-editable Google Sheet.

    Requirements:
      1. Open the Google Sheet.
      2. Click Share → Change to "Anyone with the link" → set role to "Editor".
      3. Copy the sheet URL and paste it into the actor input.
    """
    wb_id = _extract_workbook_id(link)
    if not wb_id:
        log.error(f"❌ Invalid Google Sheet URL — cannot extract workbook ID: {link}")
        return

    try:
        session = requests.Session()
        client = Client(auth=AnonymousCredentials(), session=session)
        workbook = client.open_by_key(wb_id)
    except Exception as e:
        log.error(
            f"❌ Could not open Google Sheet. "
            f"Make sure it is shared as 'Anyone with the link can edit'. Error: {e}"
        )
        return

    extraction_fields = [
        "positionName", "company", "url", "salary", "jobType", "isRemote",
        "location", "applyType", "benefits", "description", "rating",
        "reviewsCount", "jobMatch", "id", "externalApplyLink",
        "isExpired", "postedAt",
    ]

    easy_jobs = sorted(
        [j for j in jobs if j.get("applyType") == "Easy Apply"],
        key=lambda j: j.get("jobMatch", 0), reverse=True,
    )
    cs_jobs = sorted(
        [j for j in jobs if j.get("applyType") == "CS Apply"],
        key=lambda j: j.get("jobMatch", 0), reverse=True,
    )

    try:
        worksheet = workbook.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = workbook.add_worksheet(title=sheet_name, rows=5000, cols=40)
        log.warning(f"📰 Created new worksheet: {sheet_name}")

    def jobs_to_rows(job_list: list[dict]) -> list[list]:
        rows = []
        for job in job_list:
            row = []
            for f in extraction_fields:
                val = job.get(f, "")
                if isinstance(val, dict):
                    val = str(val)
                elif isinstance(val, bool):
                    val = str(val)
                row.append(val if val is not None else "")
            rows.append(row)
        return rows

    header = extraction_fields

    all_rows = [header]
    if easy_jobs:
        all_rows.append(["── Easy Apply ──"] + [""] * (len(header) - 1))
        all_rows.extend(jobs_to_rows(easy_jobs))
    if cs_jobs:
        all_rows.append(["── CS Apply ──"] + [""] * (len(header) - 1))
        all_rows.extend(jobs_to_rows(cs_jobs))

    try:
        worksheet.clear()
        worksheet.update("A1", all_rows)
        log.info(f"✅ Uploaded {len(jobs)} jobs to Google Sheets tab '{sheet_name}'")
    except Exception as e:
        log.error(f"❌ Failed to write to Google Sheet: {e}")
