"""
Indeed Scraper — Apify Actor
Entry point: src/main.py
"""
from __future__ import annotations

import asyncio
from apify import Actor
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .config import ScraperSettings
from .helpers import (
    ScraperConfig,
    ProxyRotator,
    load_scraper_config,
    load_proxies_from_text,
    extract_job_ids_from_urls,
    build_indeed_search_urls,
    indeed_login,
    showstartinginfo,
    status_logger,
    _flush_shared_batch,
)
from .workers import listing_worker, processing_worker
from .gsheet import upload_to_google_sheet


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
        concurrency      = min(int(actor_input.get("concurrency", 5)), ScraperSettings.max_concurrency)
        headless         = actor_input.get("headless", True)
        min_match_pct    = int(actor_input.get("min_match_percentage", 40))

        # ── Feature flags ─────────────────────────────────────────────────────
        scrape_company_details = bool(actor_input.get("scrape_company_details", False))
        save_unique_only       = bool(actor_input.get("save_unique_only", True))
        follow_apply_redirect  = bool(actor_input.get("follow_apply_redirect", False))

        # ── Search builder fields (used when start_urls is empty) ─────────────
        search_keywords_raw    = actor_input.get("search_keywords", "").strip()
        search_location        = actor_input.get("search_location", "").strip()
        search_country         = actor_input.get("search_country", "us").strip().lower()
        max_results_per_search = int(actor_input.get("max_results_per_search", 0))

        # Parse keywords — one per line
        search_keywords = [k.strip() for k in search_keywords_raw.splitlines() if k.strip()]

        # ── Google Sheets ─────────────────────────────────────────────────────
        google_sheet_url = actor_input.get("google_sheet_url", "")
        sheet_name       = actor_input.get("sheet_name", "Indeed Jobs")

        # ── Proxy setup ───────────────────────────────────────────────────────
        proxy_list_raw     = actor_input.get("proxy_list", "").strip()
        apify_proxy_config = actor_input.get("proxy_config", {})

        proxies: list[str] = []
        apify_playwright_proxy: dict | None = None

        if proxy_list_raw:
            proxies = load_proxies_from_text(proxy_list_raw)
            Actor.log.info(f"🌐 Using user-supplied proxy list ({len(proxies)} proxies)")
        elif apify_proxy_config:
            proxy_cfg_obj = await Actor.create_proxy_configuration(
                actor_proxy_input=apify_proxy_config
            )
            if proxy_cfg_obj:
                new_url = await proxy_cfg_obj.new_url()
                apify_playwright_proxy = {"server": new_url}
                Actor.log.info("🌐 Using Apify managed proxy")
        else:
            Actor.log.info("🌐 No proxy configured — using direct connection")

        proxy_rotator = ProxyRotator(proxies) if proxies else None

        # ── Indeed account cookies ────────────────────────────────────────────
        account_cookies: list[dict] = actor_input.get("account_cookies", [])
        indeed_email    = actor_input.get("indeed_email", "").strip()
        indeed_password = actor_input.get("indeed_password", "").strip()

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
                max_results=max_results_per_search,
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
            proxy_rotator=proxy_rotator,
            search_keywords=search_keywords,
            search_location=search_location,
            search_country=search_country,
            max_results_per_search=max_results_per_search,
            scrape_company_details=scrape_company_details,
            save_unique_only=save_unique_only,
            follow_apply_redirect=follow_apply_redirect,
            google_sheet_url=google_sheet_url,
            sheet_name=sheet_name,
            headless=headless,
            proxy_config=apify_proxy_config,
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

            # ── Auto-login when credentials given but no cookies ──────────────
            if not account_cookies and indeed_email and indeed_password:
                Actor.log.info("🔐 Credentials provided — performing Indeed login...")
                login_cookies = await indeed_login(
                    browser=browser,
                    config=config,
                    playwright_proxy=apify_playwright_proxy,
                    email=indeed_email,
                    password=indeed_password,
                )
                if login_cookies:
                    config.account_cookies = login_cookies
                    Actor.log.info(
                        f"✅ Login succeeded — {len(login_cookies)} cookies captured"
                    )
                else:
                    Actor.log.warning(
                        "⚠️ Login returned no cookies — proceeding without auth"
                    )
            elif not account_cookies and not indeed_email:
                Actor.log.warning(
                    "⚠️ No cookies and no credentials — scraping without login. "
                    "Indeed may show limited results or trigger CAPTCHA."
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

            await asyncio.gather(*listing_tasks, *processing_tasks, return_exceptions=True)

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