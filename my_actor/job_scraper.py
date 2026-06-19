"""
src/job_scraper.py
Scrapes full job details from a single Indeed job page.
Replaces data_dispatcher.emit() with Actor.push_data() via push_job_data().
"""
from __future__ import annotations

import asyncio
import random
import re
import urllib.parse

from apify import Actor
from playwright.async_api import Page

from .config import ScraperSettings
from .helpers import (
    ScraperConfig,
    simulate_human_behavior,
    extract_salary_job_types,
    extract_rating_and_reviews,
    extract_external_apply_link,
    check_remote_status,
    push_job_data,
)


async def process_filter_jobs(
    page: Page,
    link: str,
    percentage: float,
    config: ScraperConfig,
) -> bool:
    """
    Scrape full job details from an Indeed job URL.
    Returns True if extracted successfully, False otherwise.
    """
    fields = ScraperSettings.extraction_fields
    data   = {field: "" for field in fields}
    data.update({"url": link, "job_match": percentage})

    # ── Load page with retries ────────────────────────────────────────────────
    for attempt in range(3):
        try:
            await page.goto(link, wait_until="load")
            break
        except Exception as e:
            Actor.log.warning(f"⏳ Attempt {attempt + 1}/3 failed: {link} | {e}")
            if attempt < 2:
                await asyncio.sleep(random.randint(2, 5))
    else:
        Actor.log.error(f"❌ All retries failed: {link}")
        return False

    await simulate_human_behavior(page)

    # ── Ignore-related keyword filter ─────────────────────────────────────────
    try:
        if config.ignore_related:
            content = await page.locator("body").inner_text()
            if any(kw in content for kw in config.ignore_related):
                Actor.log.info(f"⏭ Skipped (ignore_related): {link}")
                if ScraperSettings.skip_ignore_related:
                    return False
                data["ignore_related"] = "True"
    except Exception as e:
        Actor.log.error(f"❌ ignore_related check failed: {e}")
        return False

    # ── Extract data ──────────────────────────────────────────────────────────
    try:
        page_title = await page.title()
        if "not found" in page_title.lower():
            Actor.log.info(f"⏭ Job removed/expired: {link}")
            return False

        # ── Company ───────────────────────────────────────────────────────────
        company = await _get_company(page)
        if not company:
            Actor.log.warning(f"⚠️ No company found: {link}")
            return False
        data["company"] = company

        # ── Position ──────────────────────────────────────────────────────────
        position_locator = page.locator(
            '[data-testid="jobsearch-JobInfoHeader-title"] span'
        ).first
        position_text = await position_locator.text_content()
        if not position_text:
            Actor.log.warning(f"⚠️ No position title found: {link}")
            return False
        data["position"] = position_text.strip()

        # ── Salary & Job Types ────────────────────────────────────────────────
        (
            data["salary"],
            data["jt0"],
            data["jt1"],
            data["jt2"],
            data["jt3"],
        ) = await extract_salary_job_types(page)

        # ── Location ──────────────────────────────────────────────────────────
        try:
            loc = page.locator('[data-testid="inlineHeader-companyLocation"]').first
            data["location"] = (await loc.inner_text()).strip()
        except Exception:
            data["location"] = ""

        # ── Apply type ────────────────────────────────────────────────────────
        apply_exists = (
            await page.locator('button[aria-label*="Apply on company site"]').count() > 0
        )
        data["apply_type"]           = "CS Apply" if apply_exists else "Easy Apply"
        data["external_apply_link"]  = await extract_external_apply_link(page)

        # ── Benefits ──────────────────────────────────────────────────────────
        benefit_items = await page.locator('[data-testid="benefits-test"] ul li').all()
        benefits = ""
        for li in benefit_items:
            text = await li.inner_text()
            if text:
                benefits += f"{text.strip()} \n"
        data["benefits"] = benefits

        # ── Description ───────────────────────────────────────────────────────
        desc_loc = page.locator("#jobDescriptionText").first
        data["description"] = (
            (await desc_loc.inner_text()) if await desc_loc.count() > 0 else ""
        )

        # ── Remote badge ──────────────────────────────────────────────────────
        remote_badge = ""
        try:
            container = page.locator(
                '[data-testid="jobsearch-CompanyInfoContainer"]'
            ).first
            if await container.count() > 0:
                keywords = {"remote", "hybrid", "in-person", "on-site", "on site"}
                for div in await container.locator("div").all():
                    text = (await div.inner_text()).strip().lower()
                    if text in keywords:
                        remote_badge = text
                        break
        except Exception:
            pass

        data["is_remote"] = check_remote_status(
            data["description"], data["location"], remote_badge
        )

        # ── Job ID ────────────────────────────────────────────────────────────
        params        = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
        data["job_id"] = params.get("jk", [""])[0]
        if not data["job_id"]:
            Actor.log.warning(f"⚠️ No job_id found: {link}")

        # ── Expiry ────────────────────────────────────────────────────────────
        expired_loc = page.locator(":text-is('This job has expired on Indeed')").first
        is_expired  = await expired_loc.is_visible()
        data["is_expired"] = "True" if is_expired else ""
        if is_expired and ScraperSettings.skip_expired:
            return False

        # ── Rating & Reviews ──────────────────────────────────────────────────
        rr = await extract_rating_and_reviews(page)
        data["rating"]       = float(rr.get("rating") or 0)
        data["review_count"] = int(rr.get("review_count") or 0)

        # ── Push to Apify dataset ─────────────────────────────────────────────
        await push_job_data(data, config)
        Actor.log.info(f"✅ Extracted: {data['position']} @ {data['company']} → {percentage}%")
        return True

    except Exception as e:
        Actor.log.error(f"❌ Extraction failed: {link} | {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Company name extractor
# ─────────────────────────────────────────────────────────────────────────────
async def _get_company(page: Page) -> str | None:
    try:
        await page.wait_for_selector('div[data-company-name="true"]', timeout=8000)
    except Exception:
        return None

    selectors = [
        'div[data-company-name="true"] a',
        '[data-testid="inlineHeader-companyName"] span a',
        'div[data-company-name="true"]',
        '[data-testid="inlineHeader-companyName"]',
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).strip().split("\n")[0].strip()
                if text:
                    return text
        except Exception:
            continue
    return None
