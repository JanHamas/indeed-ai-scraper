"""
my_actor/email_logs.py
Send a short run summary to your own Gmail via SMTP.
"""
from __future__ import annotations

import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from .config import ScraperSettings

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# How many search URLs to list verbatim in the email before truncating.
MAX_URLS_IN_EMAIL = 20

# How many search keywords to list verbatim in the email before truncating.
MAX_KEYWORDS_IN_EMAIL = 25


def send_logs_email(
    subject: str,
    body: str,
    log: Any,
    to_address: str | None = None,
) -> None:
    """
    Send a plain-text email with `subject`/`body` to `to_address`.
    Uses credentials from ScraperSettings.

    Call this at the end of a run (success or failure) with a short
    summary — don't dump full scraped job content into the body, this
    is a debugging tool, not a data pipeline.
    """
    to_address = to_address or ScraperSettings.TO_EMAIL

    msg = MIMEMultipart()
    msg["From"] = ScraperSettings.EMAIL
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(ScraperSettings.EMAIL, ScraperSettings.EMAIL_PASSWORD)
            server.sendmail(ScraperSettings.EMAIL, [to_address], msg.as_string())
    except Exception as e:
        log.warning(f"Failed to send log email: {e}")


def _format_duration(start_time: float) -> str:
    elapsed = int(time.perf_counter() - start_time)
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def _count_by(jobs: list[dict], field: str, value: str) -> int:
    return sum(1 for j in jobs if j.get(field) == value)


def _format_credit_usage(credit_usage: list[dict]) -> list[str]:
    """
    credit_usage is the list returned by FirecrawlClient.get_all_credit_usage():
    [{"name": str, "remaining": int|None, "plan": int|None, "error": str|None}, ...]
    """
    lines: list[str] = []
    lines.append("")
    lines.append("FIRECRAWL CREDITS (per account)")

    total_remaining = 0
    total_plan = 0
    any_ok = False

    name_width = max((len(a["name"]) for a in credit_usage), default=20)
    name_width = max(name_width, 20)

    for acc in credit_usage:
        name = acc.get("name", "unknown")
        if acc.get("error"):
            lines.append(f"  {name:<{name_width}}  ERROR: {acc['error']}")
            continue
        remaining = acc.get("remaining")
        plan = acc.get("plan")
        any_ok = True
        total_remaining += remaining or 0
        total_plan += plan or 0
        lines.append(f"  {name:<{name_width}}  {remaining} / {plan} remaining")

    if any_ok:
        lines.append(f"  {'—' * name_width}")
        lines.append(f"  {'TOTAL':<{name_width}}  {total_remaining} / {total_plan} remaining")

    return lines


def build_run_summary(
    config: Any,
    start_time: float,
    errors: list[str] | None = None,
    run_status: str = "COMPLETED",
    previously_processed_count: int | None = None,
    credit_usage: Optional[list[dict]] = None,
) -> str:
    """
    Build a plain-text run summary email body.

    `previously_processed_count` should be the size of `config.processed_uids`
    captured BEFORE the scrape ran — i.e. just the IDs that came from the
    user's `processed_job_urls` schema input. `config.processed_uids` is also
    used as the in-run dedup set, so by the time the run finishes it has
    every job ID collected THIS run merged in too. If the caller doesn't
    pass this explicitly, we fall back to the (now-inflated) live count.

    `credit_usage`, if provided, is the list returned by
    FirecrawlClient.get_all_credit_usage() — one entry per configured
    account, so the user can see exactly which accounts need topping up.
    """
    jobs = config._saved_jobs
    lines: list[str] = []

    lines.append("INDEED SCRAPER RUN REPORT")
    lines.append("=" * 40)
    lines.append(f"Date:     {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"Duration: {_format_duration(start_time)}")
    lines.append(f"Status:   {run_status}")

    # ── Plan / billing — always shown so free-tier users know why their
    # run was capped, instead of just seeing a lower job count with no
    # explanation. ──────────────────────────────────────────────────────
    is_paying = getattr(config, "is_paying", True)
    if not is_paying:
        lines.append(
            f"Plan:     🆓 FREE TIER — capped at "
            f"{ScraperSettings.FREE_TIER_MAX_JOBS} jobs/run"
        )
    else:
        lines.append("Plan:     💳 Paid")

    lines.append("")
    lines.append("STATISTICS")
    lines.append(f"  Jobs extracted:   {config.extracted_jobs_counter}/{config.max_jobs}")
    lines.append(f"  Jobs pushed:      {config.pushed_jobs}/{config.max_jobs}")
    lines.append(f"  Total saved:       {len(jobs)}")
    lines.append(f"  Billable requests: {config.total_requests}")

    lines.append("")
    lines.append("CONFIGURATION")
    lines.append(f"  Max jobs:               {config.max_jobs}")
    lines.append(f"  Per company limit:      {config.per_company_jobs}")
    lines.append(f"  Concurrency:            {config.concurrency}")
    lines.append(f"  Min match %:            {config.min_match_percentage}%")
    lines.append(f"  Country:                {config.search_country.upper()}")
    lines.append(f"  Location:               {config.search_location or 'All'}")

    # ── search_keywords is only ever authoritative when the user did NOT
    # supply start_urls directly (see get_about_me() in helpers.py). Say so
    # explicitly instead of the old silent "From URLs" placeholder — and
    # always list the actual URLs that were scraped, since those are the
    # real source of truth for what this run did. ──────────────────────
    if config.search_keywords:
        lines.append(f"  Keywords ({len(config.search_keywords)}):")
        for kw in config.search_keywords[:MAX_KEYWORDS_IN_EMAIL]:
            lines.append(f"    {kw}")
        if len(config.search_keywords) > MAX_KEYWORDS_IN_EMAIL:
            lines.append(f"    ... and {len(config.search_keywords) - MAX_KEYWORDS_IN_EMAIL} more")
    else:
        lines.append("  Keywords:               None (used direct start_urls)")

    if config.has_raw_start_urls and config.url_queue:
        lines.append(f"  Search URLs ({len(config.url_queue)}):")
        for u in config.url_queue[:MAX_URLS_IN_EMAIL]:
            lines.append(f"    {u}")
        if len(config.url_queue) > MAX_URLS_IN_EMAIL:
            lines.append(f"    ... and {len(config.url_queue) - MAX_URLS_IN_EMAIL} more")

    # about_me = getattr(config, "about_me", "")
    # if about_me:
    #     lines.append(f"  AI matching text:       {about_me[:200]}")

    ignore_companies = getattr(config, "ignore_companies", None) or getattr(config, "ignore_companies_raw", "")
    ignore_related = getattr(config, "ignore_related", None) or getattr(config, "ignore_related_raw", "")
    lines.append(f"  Ignore companies:       {ignore_companies or 'None'}")
    lines.append(f"  Ignore related:         {ignore_related or 'None'}")

    processed_uids = getattr(config, "processed_uids", None) or set()
    if previously_processed_count is None:
        previously_processed_count = len(processed_uids)
    lines.append(f"  Previously processed:   {previously_processed_count} job ID(s)")

    lines.append("")
    lines.append("FEATURE FLAGS")
    lines.append(f"  AI matching:            {'ON' if config.ai_matching_enabled else 'OFF'}")
    lines.append(f"  Scrape company details: {'ON' if getattr(config, 'scrape_company_details', False) else 'OFF'}")
    lines.append(f"  Save unique only:       {'ON' if getattr(config, 'save_unique_only', False) else 'OFF'}")
    lines.append(f"  Follow apply redirect:  {'ON' if getattr(config, 'follow_apply_redirect', False) else 'OFF'}")
    lines.append(f"  Skip expired jobs:      {'ON' if getattr(config, 'skip_expired_jobs', False) else 'OFF'}")
    lines.append(f"  Skip ignore-related:    {'ON' if getattr(config, 'skip_ignore_related_jobs', False) else 'OFF'}")

    if config.ignored_companies_seen:
        ignored = sorted(config.ignored_companies_seen)
        lines.append("")
        lines.append(f"IGNORED COMPANIES ({len(ignored)})")
        for company in ignored[:20]:
            lines.append(f"  {company}")
        if len(ignored) > 20:
            lines.append(f"  ... and {len(ignored) - 20} more")

    if credit_usage:
        lines.extend(_format_credit_usage(credit_usage))

    if errors:
        lines.append("")
        lines.append(f"ERRORS ({len(errors)})")
        for error in errors[:10]:
            lines.append(f"  {error}")
        if len(errors) > 10:
            lines.append(f"  ... and {len(errors) - 10} more errors")

    if config.google_sheet_url:
        lines.append("")
        lines.append(f"Google Sheet: {config.google_sheet_url}")

    lines.append("")
    lines.append("-" * 40)
    lines.append("Automated message from your Indeed Scraper.")

    return "\n".join(lines)