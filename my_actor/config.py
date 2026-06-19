"""
src/config.py — Static settings for the Apify actor.
All runtime values come from Actor input (see main.py).
"""
from __future__ import annotations


class ScraperSettings:
    # ── Concurrency cap ───────────────────────────────────────────────────────
    max_concurrency = 10

    # ── Batch sizes ───────────────────────────────────────────────────────────
    get_percentages_batch_size = 30   # jobs scored per AI call
    UID_FLUSH_SIZE             = 30   # UIDs buffered before saving to KV store

    # ── Browser context rotation ──────────────────────────────────────────────
    context_rotate_limit = 400        # requests per context before recreation

    # ── Skip flags ────────────────────────────────────────────────────────────
    skip_ignore_related = True
    skip_expired        = True

    # ── Google Sheets ─────────────────────────────────────────────────────────
    gsheet_scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    # ── Fields written to Apify dataset (Actor.push_data) ────────────────────
    extraction_fields = [
        "position", "company", "url", "salary",
        "jt0", "jt1", "jt2", "jt3",
        "location", "apply_type", "benefits", "description",
        "job_id", "is_expired", "is_remote",
        "external_apply_link", "rating", "review_count",
        "job_match", "ignore_related",
    ]

    # ── Fields written to Google Sheets ──────────────────────────────────────
    export_fields = [
        "position", "company", "url", "salary",
        "jt0", "is_remote", "location", "apply_type",
        "benefits", "description", "rating", "review_count",
        "job_match", "job_id", "external_apply_link",
        "ignore_related", "is_expired",
    ]
