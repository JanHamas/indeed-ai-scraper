"""
my_actor/gsheet.py
Google Sheets upload — service account mode (credentials hardcoded per user request).
"""
from __future__ import annotations

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
    Upload scraped jobs to a Google Sheet via service account.
    Users must share their sheet with:
      python-api@indeed-leads-467810.iam.gserviceaccount.com  (Editor)
    """
    wb_id = _extract_workbook_id(link)
    if not wb_id:
        log.error(f"❌ Invalid Google Sheet URL — cannot extract workbook ID: {link}")
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