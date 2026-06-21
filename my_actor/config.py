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
    context_rotate_limit = 1000       # requests per context before recreation

    # ── Skip flags ────────────────────────────────────────────────────────────
    skip_ignore_related = True
    skip_expired        = True

    # ── Google Sheets (public URL mode) ──────────────────────────────────────
    gsheet_scopes = ["https://www.googleapis.com/auth/spreadsheets"]

    # ── Indeed URLs ───────────────────────────────────────────────────────────
    indeed_login_url = "https://secure.indeed.com/account/login"

    # Map of country codes → Indeed base domains
    indeed_country_domains: dict[str, str] = {
        "us":  "https://www.indeed.com",
        "uk":  "https://uk.indeed.com",
        "ca":  "https://ca.indeed.com",
        "au":  "https://au.indeed.com",
        "in":  "https://www.indeed.co.in",
        "de":  "https://de.indeed.com",
        "fr":  "https://fr.indeed.com",
        "sg":  "https://www.indeed.com.sg",
        "pk":  "https://www.indeed.com.pk",
        "nl":  "https://www.indeed.nl",
        "es":  "https://www.indeed.es",
        "it":  "https://it.indeed.com",
        "br":  "https://www.indeed.com.br",
        "mx":  "https://www.indeed.com.mx",
        "nz":  "https://nz.indeed.com",
        "jp":  "https://jp.indeed.com",
        "ae":  "https://www.indeed.ae",
        "sa":  "https://www.indeed.com.sa",
    }

    # ── Fields written to Apify dataset (Actor.push_data) ────────────────────
    extraction_fields = [
        "position", "company", "url", "salary",
        "jt0", "jt1", "jt2", "jt3",
        "location", "apply_type", "benefits", "description",
        "job_id", "is_expired", "is_remote",
        "external_apply_link", "resolved_apply_link",
        "rating", "review_count",
        "company_size", "company_industry", "company_description",
        "job_match", "ignore_related",
    ]

    # ── Fields written to Google Sheets ──────────────────────────────────────
    export_fields = [
        "position", "company", "url", "salary",
        "jt0", "is_remote", "location", "apply_type",
        "benefits", "description", "rating", "review_count",
        "company_size", "company_industry",
        "job_match", "job_id", "external_apply_link", "resolved_apply_link",
        "ignore_related", "is_expired",
    ]