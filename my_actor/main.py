"""
Indeed Job Scraper — Apify Actor
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
    load_scraper_config,
    showstartinginfo,
    status_logger,
    _flush_shared_batch,
)
from .workers import listing_worker, processing_worker
from .gsheet import upload_to_google_sheet


async def main() -> None:
    async with Actor:
        # ── Read actor input ──────────────────────────────────────────────────
        actor_input = await Actor.get_input() or {}

        url_queue_raw       = actor_input.get("start_urls", [])
        about_me            = actor_input.get("about_me", "")
        ignore_companies    = actor_input.get("ignore_companies", "")
        ignore_related      = actor_input.get("ignore_related", "")
        max_jobs            = int(actor_input.get("max_jobs", 50))
        per_company_jobs    = int(actor_input.get("per_company_jobs", 5))
        min_match_pct       = int(actor_input.get("min_match_percentage", 30))
        concurrency         = min(int(actor_input.get("concurrency", 3)), ScraperSettings.max_concurrency)
        google_sheet_url    = actor_input.get("google_sheet_url", "")
        sheet_name          = actor_input.get("sheet_name", "Indeed Jobs")
        headless            = actor_input.get("headless", True)
        proxy_config        = actor_input.get("proxy_config", {})   # Apify proxy config dict

        # start_urls can be a list of strings OR list of {"url": "..."} dicts
        url_list: list[str] = []
        for entry in url_queue_raw:
            if isinstance(entry, str):
                url_list.append(entry.strip())
            elif isinstance(entry, dict):
                url_list.append(entry.get("url", "").strip())
        url_list = [u for u in url_list if u]

        if not url_list:
            Actor.log.error("No start_urls provided — exiting.")
            return

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
            google_sheet_url=google_sheet_url,
            sheet_name=sheet_name,
            headless=headless,
            proxy_config=proxy_config,
        )

        await showstartinginfo(config)

        # ── Shared state ──────────────────────────────────────────────────────
        url_queue    = asyncio.Queue()
        filter_queue = asyncio.Queue()

        batch_positions: list[str] = []
        batch_links:     list[str] = []
        batch_uids:      list[str] = []
        batch_lock       = asyncio.Lock()

        for url in config.url_queue:
            await url_queue.put(url)

        stop_event = asyncio.Event()

        # ── Launch browser ────────────────────────────────────────────────────
        async with Stealth().use_async(async_playwright()) as pw:

            # Build proxy args for Playwright if proxy_config supplied
            playwright_proxy = None
            if proxy_config:
                # Apify proxy_config example:
                #   {"useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"]}
                # For Playwright we need server/username/password.
                # Actor.create_proxy_configuration() returns those values.
                proxy_cfg_obj = await Actor.create_proxy_configuration(
                    actor_proxy_input=proxy_config
                )
                if proxy_cfg_obj:
                    new_url = await proxy_cfg_obj.new_url()
                    playwright_proxy = {"server": new_url}

            browser = await pw.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            Actor.log.info(
                f"🚀 Launching {concurrency} listing + {concurrency} processing workers"
            )

            # ── Status logger ─────────────────────────────────────────────────
            status_task = asyncio.create_task(status_logger(config, stop_event))

            # ── Listing workers ───────────────────────────────────────────────
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
                        playwright_proxy=playwright_proxy,
                        worker_id=i,
                    )
                )
                for i in range(concurrency)
            ]

            # ── Processing workers ────────────────────────────────────────────
            processing_tasks = [
                asyncio.create_task(
                    processing_worker(
                        browser=browser,
                        config=config,
                        url_queue=url_queue,
                        filter_queue=filter_queue,
                        playwright_proxy=playwright_proxy,
                        worker_id=concurrency + i,
                    )
                )
                for i in range(concurrency)
            ]

            await asyncio.gather(*listing_tasks, *processing_tasks, return_exceptions=True)

            # ── Final flush ───────────────────────────────────────────────────
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
            gs_cred = actor_input.get("google_sheets_credentials", {})
            if gs_cred:
                await upload_to_google_sheet(
                    link=gs_url,
                    credentials_dict=gs_cred,
                    scopes=ScraperSettings.gsheet_scopes,
                    sheet_name=config.sheet_name,
                    jobs=config._saved_jobs,
                    log=Actor.log,
                )
            else:
                Actor.log.warning(
                    "⚠️ google_sheet_url provided but no google_sheets_credentials in input — skipping upload"
                )
        else:
            Actor.log.info("⏭️ No Google Sheet URL — skipping upload")

        Actor.log.info("✅ Actor finished successfully")
