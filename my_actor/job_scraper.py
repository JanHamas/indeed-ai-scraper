"""
my_actor/job_scraper.py
Scrapes full job details from a single Indeed job page via Decodo's
Web Scraping API.

Field names match ScraperSettings.extraction_fields.

Single-source design: all fields are pulled from the embedded GraphQL
job object in window._rootProps (see parse_indeed_rootprops.py). No
BeautifulSoup / DOM scraping, no ld+json parsing — those were both
partial views of the same data and the DOM path was what broke on
Indeed's 2026 RNW template (rating/review scraping silently returned
0/0). The GraphQL object is template-agnostic and far more complete.
"""
from __future__ import annotations

import asyncio
import random
import urllib.parse
from datetime import datetime, timezone

import aiohttp
from apify import Actor

from .firecrawl_client import firecrawl as evomi
from .config import ScraperSettings
from . import helpers
from .parse_indeed_rootprops import parse_indeed_job

# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def process_filter_jobs(
    url: str,
    percentage: float,
    config: helpers.ScraperConfig,
    session: aiohttp.ClientSession,
) -> bool:
    """
    Fetch and scrape a single Indeed job page.
    Returns True if the job was saved, False otherwise.
    """
    data: dict = {field: "" for field in ScraperSettings.extraction_fields}

    data["url"]                  = url
    data["jobMatch"]             = percentage
    data["scrapedAt"]            = datetime.now(timezone.utc).isoformat()
    data["searchInput/country"]  = config.search_country
    data["searchInput/location"] = config.search_location
    data["searchInput/position"] = config.about_me

    # ── Fetch with retries ──────────────────────────────────────────────
    html: str | None = None
    for net_attempt in range(ScraperSettings.MAX_RETRIES):
        try:
            html = await evomi.fetch(url, session, config=config)
            break
        except Exception as e:
            Actor.log.warning(f"⏳ Attempt {net_attempt + 1}/{ScraperSettings.MAX_RETRIES} failed: {url} | {e}")
            if net_attempt < ScraperSettings.MAX_RETRIES - 1:
                await asyncio.sleep(
                    random.uniform(ScraperSettings.RETRY_DELAY_MIN, ScraperSettings.RETRY_DELAY_MAX)
                )

    if html is None:
        await config.release_slot()
        Actor.log.error(f"❌ All retries failed slot released: {url}")
        return False

    soup_text = html.lower()  # cheap membership check, no DOM parse needed

    # ── Ignore-related keyword filter ───────────────────────────────────
    if config.ignore_related:
        matched_kw = next((kw for kw in config.ignore_related if kw.lower() in soup_text), None)
        if matched_kw:
            Actor.log.info(f"⏭ Skipped (ignore_related, matched: '{matched_kw}'): {url}")
            if config.skip_ignore_related_jobs:
                await config.release_slot()
                return False

    # ── Parse everything from the embedded GraphQL job object ──────────
    job = parse_indeed_job(html)

    if job.get("expired") and config.skip_expired_jobs:
        Actor.log.info(f"⏭ Skipped (expired): {url}")
        await config.release_slot()
        return False

    data["isExpired"]         = bool(job.get("expired"))
    data["id"]                = job.get("job_id") or ""
    data["positionName"]      = job.get("title") or ""
    data["company"]           = job.get("company") or ""
    data["companyIndeedUrl"]  = job.get("company_url") or ""
    data["location"]          = job.get("location") or ""
    data["salary"]            = job.get("salary") or ""
    data["jobType"]           = job.get("job_type") or ""
    data["isRemote"]          = job.get("remote_type") or ("Remote" if job.get("is_remote") else "")
    data["postedAt"]          = job.get("posted_age_text") or ""
    data["postingDateParsed"] = job.get("date_posted_iso") or ""
    data["description"]       = job.get("description_text") or ""
    data["descriptionHTML"]   = job.get("description_html") or ""
    data["externalApplyLink"] = job.get("apply_url") or ""
    data["applyType"]         = job.get("apply_type") or ""
    data["benefits"]          = "\n".join(job.get("benefits") or [])
    data["rating"]            = float(job.get("rating") or 0)
    data["reviewsCount"]      = int(job.get("review_count") or 0)

    if not data["id"]:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        data["id"] = params.get("jk", [""])[0]

    data["searchInput"] = {
        "country":  data["searchInput/country"],
        "location": data["searchInput/location"],
        "position": data["searchInput/position"],
    }

    try:
        await helpers.push_job_data(data, config)
        await config.increment_pushed(1)
        Actor.log.info(
            f"✅ Extracted: {data['positionName']} @ {data['company']}"
            + (f" → {percentage}%" if config.ai_matching_enabled else "")
            + f"  |  pushed: {config.pushed_jobs}/{config.max_jobs}"
        )
        return True
    except Exception as e:
        Actor.log.error(f"❌ Push failed (json): {url} | {e}")
        return False