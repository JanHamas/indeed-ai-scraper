"""
my_actor/job_scraper.py
Scrapes full job details from a single Indeed job page.
Field names match ScraperSettings.extraction_fields exactly.
"""
from __future__ import annotations

import asyncio
import random
import re
import urllib.parse
from datetime import datetime, timezone

from apify import Actor
from playwright.async_api import Page

from .config import ScraperSettings
from .helpers import (
    ScraperConfig,
    simulate_human_behavior,
    extract_salary_job_types,
    extract_rating_and_reviews,
    extract_external_apply_link,
    resolve_redirect,
    scrape_company_details as fetch_company_details,
    check_remote_status,
    push_job_data,
)


async def process_filter_jobs(
    page: Page,
    url: str,
    percentage: float,
    config: ScraperConfig,
) -> bool:
    """
    Scrape full job details from an Indeed job URL.
    Returns True if saved successfully, False otherwise.
    """
    data: dict = {field: "" for field in ScraperSettings.extraction_fields}

    # ── Static / known-at-call-time fields ───────────────────────────────────
    data[""]                = False
    data["url"]                     = url
    data["urlInput"]                = url
    data["jobMatch"]                = percentage
    data["scrapedAt"]               = datetime.now(timezone.utc).isoformat()
    data["searchInput/country"]     = config.search_country
    data["searchInput/location"]    = config.search_location
    data["searchInput/position"]    = config.about_me  # target titles used for this run

    # ── Load page with retries ────────────────────────────────────────────────
    for attempt in range(3):
        try:
            await page.goto(url, wait_until="load")
            break
        except Exception as e:
            Actor.log.warning(f"⏳ Attempt {attempt + 1}/3 failed: {url} | {e}")
            if attempt < 2:
                await asyncio.sleep(random.randint(2, 5))
    else:
        Actor.log.error(f"❌ All retries failed: {url}")
        return False

    await simulate_human_behavior(page)

    # ── Ignore-related keyword filter ─────────────────────────────────────────
    try:
        if config.ignore_related:
            content = await page.locator("body").inner_text()
            if any(kw in content for kw in config.ignore_related):
                Actor.log.info(f"⏭ Skipped (ignore_related): {url}")
                if ScraperSettings.skip_ignore_related:
                    return False
    except Exception as e:
        Actor.log.error(f"❌ ignore_related check failed: {e}")
        return False

    try:
        # ── Check page is valid ───────────────────────────────────────────────
        page_title = await page.title()
        if "not found" in page_title.lower():
            Actor.log.info(f"⏭ Job removed/expired: {url}")
            return False

        # ── Company ───────────────────────────────────────────────────────────
        company = await _get_company(page)
        if not company:
            Actor.log.warning(f"⚠️ No company found: {url}")
            return False
        data["company"] = company

        # ── Position name ─────────────────────────────────────────────────────
        position_locator = page.locator(
            '[data-testid="jobsearch-JobInfoHeader-title"] span'
        ).first
        position_text = await position_locator.text_content()
        if not position_text:
            Actor.log.warning(f"⚠️ No position title found: {url}")
            return False
        data["positionName"]         = position_text.strip()
        data["searchInput/position"] = position_text.strip()  # actual scraped title

        # ── Unique fingerprint check ──────────────────────────────────────────
        if config.is_duplicate_fingerprint(data["positionName"], data["company"]):
            Actor.log.info(
                f"⏭ Duplicate skipped: '{data['positionName']}' @ '{data['company']}'"
            )
            return False

        # ── Salary & Job Type ─────────────────────────────────────────────────
        # extract_salary_job_types returns (salary, jt0, jt1, jt2, jt3)
        # We collapse all job type tokens into a single comma-separated string
        salary, jt0, jt1, jt2, jt3 = await extract_salary_job_types(page)
        data["salary"]  = salary
        data["jobType"] = ", ".join(t for t in [jt0, jt1, jt2, jt3] if t)

        # ── Location ──────────────────────────────────────────────────────────
        try:
            loc = page.locator('[data-testid="inlineHeader-companyLocation"]').first
            data["location"] = (await loc.inner_text()).strip()
        except Exception:
            data["location"] = ""

        # ── Apply type ────────────────────────────────────────────────────────
        apply_exists       = await page.locator('button[aria-label*="Apply on company site"]').count() > 0
        data["applyType"]  = "CS Apply" if apply_exists else "Easy Apply"

        # ── External apply link (+ optional redirect resolution) ──────────────
        raw_apply_link              = await extract_external_apply_link(page)
        data["externalApplyLink"]   = raw_apply_link

        if config.follow_apply_redirect and raw_apply_link:
            resolved = await resolve_redirect(page, raw_apply_link)
            if resolved != raw_apply_link:
                data["externalApplyLink"] = resolved
                Actor.log.info(f"🔗 Resolved apply link: {resolved}")

        # ── Benefits ──────────────────────────────────────────────────────────
        benefit_items = await page.locator('[data-testid="benefits-test"] ul li').all()
        benefits = ""
        for li in benefit_items:
            text = await li.inner_text()
            if text:
                benefits += f"{text.strip()}\n"
        data["benefits"] = benefits.strip()

        # ── Description (plain text + HTML) ───────────────────────────────────
        desc_loc = page.locator("#jobDescriptionText").first
        if await desc_loc.count() > 0:
            data["description"]     = (await desc_loc.inner_text()).strip()
            data["descriptionHTML"] = (await desc_loc.inner_html()).strip()
        else:
            data["description"]     = ""
            data["descriptionHTML"] = ""

        # ── Remote status ─────────────────────────────────────────────────────
        remote_badge = ""
        try:
            container = page.locator('[data-testid="jobsearch-CompanyInfoContainer"]').first
            if await container.count() > 0:
                keywords = {"remote", "hybrid", "in-person", "on-site", "on site"}
                for div in await container.locator("div").all():
                    text = (await div.inner_text()).strip().lower()
                    if text in keywords:
                        remote_badge = text
                        break
        except Exception:
            pass
        data["isRemote"] = check_remote_status(
            data["description"], data["location"], remote_badge
        )

        # ── Job ID ────────────────────────────────────────────────────────────
        params      = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
        data["id"]  = params.get("jk", [""])[0]
        if not data["id"]:
            Actor.log.warning(f"⚠️ No job_id found: {url}")

        # ── Posting date ──────────────────────────────────────────────────────
        posted_at, posting_date_parsed = await _extract_posted_date(page)
        data["postedAt"]           = posted_at
        data["postingDateParsed"]  = posting_date_parsed

        # ── Expiry ────────────────────────────────────────────────────────────
        expired_loc      = page.locator(":text-is('This job has expired on Indeed')").first
        is_expired       = await expired_loc.is_visible()
        data["isExpired"] = True if is_expired else False
        if is_expired and ScraperSettings.skip_expired:
            return False

        # ── Rating & Reviews ──────────────────────────────────────────────────
        rr                    = await extract_rating_and_reviews(page)
        data["rating"]        = float(rr.get("rating") or 0)
        data["reviewsCount"]  = int(rr.get("review_count") or 0)

        # ── Company Indeed URL ────────────────────────────────────────────────
        data["companyIndeedUrl"] = await _get_company_indeed_url(page)

        # ── Company details (optional — visits /cmp/<slug>) ───────────────────
        if config.scrape_company_details:
            company_info = await fetch_company_details(page, data["company"])
            data["companySize"]        = company_info.get("company_size", "")
            data["companyIndustry"]    = company_info.get("company_industry", "")
            data["companyDescription"] = company_info.get("company_description", "")

        # ── searchInput composite ─────────────────────────────────────────────
        data["searchInput"] = {
            "country":  data["searchInput/country"],
            "location": data["searchInput/location"],
            "position": data["searchInput/position"],
        }

        # ── Push to Apify dataset ─────────────────────────────────────────────
        await push_job_data(data, config)
        Actor.log.info(
            f"✅ Extracted: {data['positionName']} @ {data['company']}"
            + (f" → {percentage}%" if config.ai_matching_enabled else "")
        )
        return True

    except Exception as e:
        Actor.log.error(f"❌ Extraction failed: {url} | {e}")
        return False


