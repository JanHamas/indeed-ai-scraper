"""
my_actor/job_scraper.py
Scrapes full job details from a single Indeed job page via Decodo's
Web Scraping API.
Field names match ScraperSettings.extraction_fields exactly.
"""
from __future__ import annotations

import asyncio
import random
import re
import urllib.parse
from datetime import datetime, timezone

import aiohttp
from apify import Actor
from bs4 import BeautifulSoup
from .firecrawl_client import firecrawl as evomi
from .config import ScraperSettings
from .helpers import (
    ScraperConfig,
    push_job_data,
    check_remote_status,
    save_debug_html,
    save_debug_page,
)

from .parse_indeed_embedded_json import parse_indeed_job_from_embedded_json


# ─────────────────────────────────────────────────────────────────────────────
# BeautifulSoup fallback extractors (mirrors Playwright DOM selectors)
# ─────────────────────────────────────────────────────────────────────────────

def _bs_company(soup: BeautifulSoup) -> str:
    for sel in [
        '[data-company-name="true"] a',
        '[data-testid="inlineHeader-companyName"] span a',
        '[data-company-name="true"]',
        '[data-testid="inlineHeader-companyName"]',
    ]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator=" ", strip=True).split("\n")[0].strip()
            if text:
                return text
    return ""


def _bs_company_indeed_url(soup: BeautifulSoup) -> str:
    for sel in [
        '[data-testid="inlineHeader-companyName"] a',
        '[data-company-name="true"] a',
    ]:
        el = soup.select_one(sel)
        if el:
            href = el.get("href", "")
            if href:
                if href.startswith("/"):
                    return "https://www.indeed.com" + href
                return href
    return ""


def _bs_salary_and_job_types(soup: BeautifulSoup) -> tuple[str, list[str]]:
    container = soup.select_one("#salaryInfoAndJobType")
    salary, job_types = "", []
    if not container:
        return salary, job_types

    delimiter_pattern = re.compile(r'\s*[,\/·]\s*|\s+-\s+')
    spans = container.select("span")
    for span in spans:
        text = span.get_text(strip=True)
        if not text:
            continue
        if any(sym in text for sym in ("$", "£", "€")) or "year" in text.lower():
            salary = text
        else:
            tokens = [t.strip() for t in delimiter_pattern.split(text) if t.strip()]
            job_types.extend(tokens)

    if len(spans) == 1 and not salary:
        text = spans[0].get_text(strip=True)
        job_types = [t.strip() for t in delimiter_pattern.split(text) if t.strip()]

    return salary, job_types


def _bs_location(soup: BeautifulSoup) -> str:
    el = soup.select_one('[data-testid="inlineHeader-companyLocation"]')
    return el.get_text(strip=True) if el else ""


def _bs_description(soup: BeautifulSoup) -> tuple[str, str]:
    desc = soup.select_one("#jobDescriptionText")
    if desc:
        return desc.get_text(separator="\n", strip=True), str(desc)
    return "", ""


def _bs_benefits(soup: BeautifulSoup) -> str:
    items = soup.select('[data-testid="benefits-test"] ul li')
    return "\n".join(li.get_text(strip=True) for li in items if li.get_text(strip=True))


def _bs_remote_badge(soup: BeautifulSoup) -> str:
    container = soup.select_one('[data-testid="jobsearch-CompanyInfoContainer"]')
    if not container:
        return ""
    keywords = {"remote", "hybrid", "in-person", "on-site", "on site"}
    for div in container.select("div"):
        text = div.get_text(strip=True).lower()
        if text in keywords:
            return text
    return ""


def _bs_apply_type(soup: BeautifulSoup) -> str:
    btn = soup.select_one('button[aria-label*="Apply on company site"]')
    return "CS Apply" if btn else "Easy Apply"


def _bs_external_apply_link(soup: BeautifulSoup) -> str:
    btn = soup.select_one('button[aria-label*="Apply on company site"]')
    if btn:
        return btn.get("href", "")
    return ""


def _bs_rating_and_reviews(soup: BeautifulSoup) -> dict:
    review_block = soup.select_one(".jobsearch-CompanyReview")
    if not review_block:
        return {"rating": 0.0, "review_count": 0}
    rating = 0.0
    rating_div = review_block.select_one('div[role="img"]')
    if rating_div:
        aria_label = rating_div.get("aria-label", "")
        m = re.search(r'(\d+\.?\d*)', aria_label)
        if m:
            rating = float(m.group(1))
    review_count = 0
    count_span = review_block.select_one("span.css-1t3rggk")
    if count_span:
        m = re.search(r'(\d+)', count_span.get_text(strip=True))
        if m:
            review_count = int(m.group(1))
    return {"rating": rating, "review_count": review_count}


def _bs_posted_date(html: str) -> tuple[str, str]:
    posted_at = ""
    posting_date_parsed = ""
    date_on_indeed: int | None = None

    match = re.search(r'"datePublished":(\d+)', html)
    if match:
        date_on_indeed = int(match.group(1))

    if date_on_indeed:
        posting_date_parsed = (
            datetime.fromtimestamp(date_on_indeed / 1000, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    age_match = re.search(r'"age":"([^"]+)"', html)
    if age_match:
        posted_at = age_match.group(1)

    return posted_at, posting_date_parsed


def _bs_is_expired(soup: BeautifulSoup) -> bool:
    for el in soup.find_all(string=True):
        if "<!-- -->This job has expired on Indeed<!-- -->" in el:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def process_filter_jobs(
    url: str,
    percentage: float,
    config: ScraperConfig,
    filter_queue: asyncio.Queue,
    session: aiohttp.ClientSession,
    attempt: int = 0,
) -> bool:
    """
    Fetch and scrape a single Indeed job page.
    Returns True if the job was saved, False otherwise.

    `attempt` tracks how many times this URL has already been re-queued
    specifically for a "no company found" parse failure (point 6). It is
    carried on the filter_queue item as (url, percentage, attempt) and is
    unrelated to the internal network-retry loop below.
    """
    data: dict = {field: "" for field in ScraperSettings.extraction_fields}

    data["url"]                  = url
    data["jobMatch"]             = percentage
    data["scrapedAt"]            = datetime.now(timezone.utc).isoformat()
    data["searchInput/country"]  = config.search_country
    data["searchInput/location"] = config.search_location
    data["searchInput/position"] = config.about_me

    # ── Fetch with retries ────────────────────────────────────────────────────
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
        # Network-level failure — re-queue as-is, don't touch the
        # "no company found" retry counter, that's a separate concern.
        await filter_queue.put((url, percentage))
        Actor.log.error(f"❌ All retries failed, re-queued: {url}")
        return False

    # Parse once, reuse everywhere
    soup = BeautifulSoup(html, "lxml")

    # ── Ignore-related keyword filter ─────────────────────────────────────────
    if config.ignore_related:
        soup_text = soup.get_text(" ", strip=True).lower()
        matched_kw = next((kw for kw in config.ignore_related if kw in soup_text), None)
        if matched_kw:
            Actor.log.info(f"⏭ Skipped (ignore_related, matched: '{matched_kw}'): {url}")
            if config.skip_ignore_related_jobs:
                await config.release_slot()
                return False

    # Expired
    is_expired = _bs_is_expired(soup)
    data["isExpired"] = True if is_expired else False
    if is_expired and config.skip_expired_jobs:
        Actor.log.info(f"⏭ Skipped (expired): {url}")
        await config.release_slot()
        return False
    
    # ── Try embedded JSON first (fastest path) ────────────────────────────────
    job = parse_indeed_job_from_embedded_json(html)

    if job["title"]:
        data["positionName"]      = job["title"] or ""
        data["company"]           = job["company"] or ""
        data["location"]          = job["location"] or ""
        data["salary"]            = job["salary"] or ""
        data["jobType"]           = job["job_type"] or ""
        data["isRemote"]          = "Remote" if job["is_remote"] else ""
        data["postedAt"]          = job["posted_age_text"] or ""
        data["description"]       = job["description_text"] or ""
        data["descriptionHTML"]   = job["description_html"] or ""
        data["externalApplyLink"] = job["apply_url"] or ""
        data["id"]                = job["job_id"] or ""

        if not data["id"]:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            data["id"] = params.get("jk", [""])[0]

        data["searchInput"] = {
            "country":  data["searchInput/country"],
            "location": data["searchInput/location"],
            "position": data["searchInput/position"],
        }

        try:
            await push_job_data(data, config)
            await config.increment_pushed(1)
            Actor.log.info(
                f"✅ Extracted (json): {data['positionName']} @ {data['company']}"
                + (f" → {percentage}%" if config.ai_matching_enabled else "")
                + f"  |  pushed: {config.pushed_jobs}/{config.max_jobs}"
            )
            return True
        except Exception as e:
            Actor.log.error(f"❌ Push failed (json): {url} | {e}")
            return False

    # ── DOM fallback (BeautifulSoup) ──────────────────────────────────────────
    try:
        soup = BeautifulSoup(html, "lxml")

        # Page title check
        page_title = (soup.find("title") or "")
        page_title_text = page_title.get_text(strip=True).lower() if hasattr(page_title, "get_text") else ""
        if "not found" in page_title_text:
            Actor.log.info(f"⏭ Job removed/expired: {url}")
            await config.release_slot()
            return False

        # Company — point 6: retry up to COMPANY_RETRY_LIMIT times before
        # giving up. The slot stays held (no release_slot()) while retries
        # remain, since the job may still succeed.
        company = _bs_company(soup)
        if not company:
            await config.release_slot()
            await save_debug_page(
                html, url, session, config=config,
                tag="no_company_found", with_screenshot=True,
            )
            return False
        data["company"] = company

        # Position name
        title_el = soup.select_one('[data-testid="jobsearch-JobInfoHeader-title"] span')
        if not title_el:
            Actor.log.warning(f"⚠️ No position title found: {url}")
            return False
        data["positionName"]         = title_el.get_text(strip=True)
        data["searchInput/position"] = data["positionName"]

        # Salary & job type
        salary, job_types = _bs_salary_and_job_types(soup)
        data["salary"]  = salary
        data["jobType"] = ", ".join(job_types)

        # Location
        data["location"] = _bs_location(soup)

        # Apply type
        data["applyType"] = _bs_apply_type(soup)

        # External apply link
        data["externalApplyLink"] = _bs_external_apply_link(soup)

        # Benefits
        data["benefits"] = _bs_benefits(soup)

        # Description
        data["description"], data["descriptionHTML"] = _bs_description(soup)

        # Remote status
        remote_badge = _bs_remote_badge(soup)
        data["isRemote"] = check_remote_status(data["description"], data["location"], remote_badge)

        # Job ID
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        data["id"] = params.get("jk", [""])[0]
        if not data["id"]:
            Actor.log.warning(f"⚠️ No job_id found: {url}")

        # Posted date
        data["postedAt"], data["postingDateParsed"] = _bs_posted_date(html)

        # Rating & reviews
        rr = _bs_rating_and_reviews(soup)
        data["rating"]       = float(rr.get("rating") or 0)
        data["reviewsCount"] = int(rr.get("review_count") or 0)

        # Company Indeed URL
        data["companyIndeedUrl"] = _bs_company_indeed_url(soup)

        # Search input composite
        data["searchInput"] = {
            "country":  data["searchInput/country"],
            "location": data["searchInput/location"],
            "position": data["searchInput/position"],
        }

        await push_job_data(data, config)
        await config.increment_pushed(1)
        Actor.log.info(
            f"✅ Extracted (dom): {data['positionName']} @ {data['company']}"
            + (f" → {percentage}%" if config.ai_matching_enabled else "")
            + f"  |  pushed: {config.pushed_jobs}/{config.max_jobs}"
        )
        return True

    except Exception as e:
        Actor.log.error(f"❌ Extraction failed: {url} | {e}")
        await config.release_slot()
        return False