"""
Indeed Scraper — Apify Actor
Entry point: my_actor/main.py
"""
from __future__ import annotations

import asyncio, json
from apify import Actor
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .utils.config import ScraperSettings
from .utils.helpers import (
    load_scraper_config,
    load_proxies_from_text,
    extract_job_ids_from_urls,
    build_indeed_search_urls,
    showstartinginfo,
    status_logger,
    _flush_shared_batch,
)
from .utils.workers import listing_worker, processing_worker
from .utils.gsheet import upload_to_google_sheet


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}

        # ── Core config ───────────────────────────────────────────────────────
        url_queue_raw    = actor_input.get("start_urls", [])
        about_me         = actor_input.get("about_me", "").strip()
        ignore_companies = actor_input.get("ignore_companies", "")
        ignore_related   = actor_input.get("ignore_related", "")
        max_jobs         = int(actor_input.get("max_jobs", 50))
        per_company_jobs = int(actor_input.get("per_company_jobs", 5))
        concurrency      = min(int(actor_input.get("concurrency", 5)), ScraperSettings.MAX_CONCURRENCY)
        min_match_pct    = int(actor_input.get("min_match_percentage", 40))
        proxies_path = ScraperSettings.PROXIES_PATH
        proxies_state = ScraperSettings.PROXIES_STATE
        headless         = ScraperSettings.HEADLESS

        # ── Feature flags ─────────────────────────────────────────────────────
        scrape_company_details = bool(actor_input.get("scrape_company_details", False))
        save_unique_only       = bool(actor_input.get("save_unique_only", True))
        follow_apply_redirect  = bool(actor_input.get("follow_apply_redirect", False))

        # ── Search builder fields (used when start_urls is empty) ─────────────
        search_keywords_raw    = actor_input.get("search_keywords", "").strip()
        search_location        = actor_input.get("search_location", "").strip()
        search_country         = actor_input.get("search_country", "us").strip().lower()

        # Parse keywords — one per line
        search_keywords = [k.strip() for k in search_keywords_raw.splitlines() if k.strip()]

        # ── Google Sheets ─────────────────────────────────────────────────────
        google_sheet_url = actor_input.get("google_sheet_url", "")
        sheet_name       = actor_input.get("sheet_name", "Indeed Jobs")

        # ── Proxy setup ───────────────────────────────────────────────────────
        proxy_list_raw     = actor_input.get("proxy_list", "").strip()
        proxies: list[str] = []

        if proxy_list_raw:
            proxies = load_proxies_from_text(proxy_list_raw)
            Actor.log.info(f"🌐 Using user-supplied proxy list ({len(proxies)} proxies)")
        else:
            Actor.log.info("🌐 No proxy configured by user — using built in.")

        # ── Indeed account cookies ────────────────────────────────────────────
        account_cookies: list[dict] = actor_input.get("account_cookies", [])

        # ── Processed job URLs → extract IDs to skip ─────────────────────────
        processed_urls_raw: list = actor_input.get("processed_job_urls", [])
        processed_url_list: list[str] = []
        for entry in processed_urls_raw:
            if isinstance(entry, str):
                processed_url_list.append(entry.strip())
            elif isinstance(entry, dict):
                processed_url_list.append(entry.get("url", "").strip())
        processed_uids = extract_job_ids_from_urls([u for u in processed_url_list if u])

        # ── Resolve URL list ──────────────────────────────────────────────────
        # Priority 1: explicit start_urls
        url_list: list[str] = []
        for entry in url_queue_raw:
            if isinstance(entry, str):
                url_list.append(entry.strip())
            elif isinstance(entry, dict):
                url_list.append(entry.get("url", "").strip())
        url_list = [u for u in url_list if u]

        # Priority 2: build from keywords/location/country
        if not url_list:
            if not search_keywords:
                Actor.log.error(
                    "❌ Nothing to scrape — provide either 'start_urls' (Indeed search URLs) "
                    "or 'search_keywords' (positions/keywords to search for)."
                )
                return
            url_list = build_indeed_search_urls(
                keywords=search_keywords,
                location=search_location,
                country=search_country,
            )
            Actor.log.info(
                f"🔧 No start_urls provided — built {len(url_list)} URL(s) "
                f"from {len(search_keywords)} keyword(s)"
            )

        # ── Build config ──────────────────────────────────────────────────────
        config = load_scraper_config(
            url_list=url_list,
            about_me=about_me,
            ignore_companies_raw=ignore_companies,
            ignore_related_raw=ignore_related,
            max_jobs=max_jobs,
            per_company_jobs=per_company_jobs,
            min_match_percentage=min_match_pct,
            concurrency=concurrency,
            processed_uids=processed_uids,
            account_cookies=account_cookies,
            search_keywords=search_keywords,
            search_location=search_location,
            search_country=search_country,
            scrape_company_details=scrape_company_details,
            save_unique_only=save_unique_only,
            follow_apply_redirect=follow_apply_redirect,
            google_sheet_url=google_sheet_url,
            sheet_name=sheet_name,
            headless=headless,
            proxies=proxies,
            proxies_path=proxies_path,
            proxies_state=proxies_state
        )

        # ── Launch browser ────────────────────────────────────────────────────
        async with Stealth().use_async(async_playwright()) as pw:

            browser = await pw.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            # ── Auto-login when cookies given  ──────────────
            if not account_cookies:
                Actor.log.warning(
                    "⚠️ No cookies — scraping without login. "
                    "Indeed may show limited results, redirect to login page or trigger CAPTCHA."
                )

            await showstartinginfo(config)

            # ── Shared queues and batch buffers ───────────────────────────────
            url_queue    = asyncio.Queue()
            filter_queue = asyncio.Queue()

            batch_positions: list[str] = []
            batch_links:     list[str] = []
            batch_uids:      list[str] = []
            batch_lock       = asyncio.Lock()

            for url in config.url_queue:
                await url_queue.put(url)

            stop_event = asyncio.Event()

            Actor.log.info(
                f"🚀 Launching {concurrency} listing + {concurrency} processing workers"
            )

            status_task = asyncio.create_task(status_logger(config, stop_event))

            listing_tasks = [
                asyncio.create_task(
                    listing_worker(
                        browser=browser,
                        config=config,
                        url_queue=url_queue,
                        filter_queue=filter_queue,
                        batch_positions=batch_positions,
                        batch_links=batch_links,
                        batch_uids=batch_uids,
                        batch_lock=batch_lock,
                        worker_id=i,
                    )
                )
                for i in range(concurrency)
            ]

            processing_tasks = [
                asyncio.create_task(
                    processing_worker(
                        browser=browser,
                        config=config,
                        url_queue=url_queue,
                        filter_queue=filter_queue,
                        worker_id=concurrency + i,
                    )
                )
                for i in range(concurrency)
            ]

            results = await asyncio.gather(*listing_tasks, *processing_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    Actor.log.error(f"❌ Task {i} crashed: {result!r}")

            await _flush_shared_batch(
                config, batch_positions, batch_links, batch_uids, batch_lock, filter_queue
            )

            stop_event.set()
            await status_task

            Actor.log.info(
                f"🏁 All workers done  |  ✅ saved: {config.extracted_jobs_counter}/{config.max_jobs}"
            )
            await browser.close()
            Actor.log.info("🔒 Browser closed")

        # ── Google Sheets upload ──────────────────────────────────────────────
        gs_url = (config.google_sheet_url or "").strip()
        if gs_url:
            if config._saved_jobs:
                Actor.log.info(f"📊 Uploading {len(config._saved_jobs)} jobs to Google Sheets...")
                await upload_to_google_sheet(
                    link=gs_url,
                    sheet_name=config.sheet_name,
                    jobs=config._saved_jobs,
                    log=Actor.log,
                )
            else:
                Actor.log.warning("⚠️ No jobs to upload to Google Sheets")
        else:
            Actor.log.info("⏭️ No Google Sheet URL — skipping upload")

        Actor.log.info("✅ Actor finished successfully")