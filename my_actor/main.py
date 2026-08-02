"""
Indeed Scraper — Apify Actor (Decodo Web Scraping API edition)
Entry point: my_actor/main.py
"""
from __future__ import annotations

import asyncio

import aiohttp
from apify import Actor

from .config import ScraperSettings
from .helpers import (
    load_scraper_config,
    expand_to_paginated_urls,
    extract_job_ids_from_urls,
    build_indeed_search_urls,
    showstartinginfo,
    status_logger,
    _flush_shared_batch,
    sanitize_indeed_url,
)
from .workers import primary_listing_worker, hybrid_listing_worker, processing_worker
from .gsheet import upload_to_google_sheet

PAGES_PER_QUERY = ScraperSettings.PAGES_PER_QUERY


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
        concurrency      = min(int(actor_input.get("concurrency", 15)), ScraperSettings.MAX_CONCURRENCY)
        min_match_pct    = int(actor_input.get("min_match_percentage", 0))

        # ── Feature flags ─────────────────────────────────────────────────────
        scrape_company_details   = bool(actor_input.get("scrape_company_details", False))
        save_unique_only         = bool(actor_input.get("save_unique_only", True))
        follow_apply_redirect    = bool(actor_input.get("follow_apply_redirect", False))
        skip_expired_jobs        = bool(actor_input.get("skip_expired_jobs", False))
        skip_ignore_related_jobs = bool(actor_input.get("skip_ignore_related_jobs", False))

        # ── Search builder fields ─────────────────────────────────────────────
        search_keywords_raw = actor_input.get("search_keywords", "").strip()
        search_location     = actor_input.get("search_location", "").strip()
        search_country      = actor_input.get("search_country", "us").strip().lower()
        date_filter         = actor_input.get("date_filter", "any").strip()
        search_keywords     = [k.strip() for k in search_keywords_raw.splitlines() if k.strip()]

        # ── Google Sheets ─────────────────────────────────────────────────────
        google_sheet_url = actor_input.get("google_sheet_url", "")
        sheet_name       = actor_input.get("sheet_name", "Indeed Jobs")

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
        url_list: list[str] = []
        for entry in url_queue_raw:
            if isinstance(entry, str):
                url_list.append(entry.strip())
            elif isinstance(entry, dict):
                url_list.append(entry.get("url", "").strip())
        url_list = [sanitize_indeed_url(u) for u in url_list if u]

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
                date_filter=date_filter,
            )
            Actor.log.info(
                f"🔧 No start_urls provided — built {len(url_list)} URL(s) "
                f"from {len(search_keywords)} keyword(s)"
            )

        # ── Force sort=date on every URL, regardless of source ────────────────
        url_list = [u for u in url_list]
        Actor.log.info(f"📅 Applied sort=date to {len(url_list)} search URL(s)")

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
            skip_expired_jobs=skip_expired_jobs,
            skip_ignore_related_jobs=skip_ignore_related_jobs,
            google_sheet_url=google_sheet_url,
            sheet_name=sheet_name,
        )

        if not account_cookies:
            Actor.log.warning(
                "⚠️ No cookies — scraping without login. "
                "Indeed may show limited results or redirect to login."
            )

        await showstartinginfo(config)

        # ── Shared queues ─────────────────────────────────────────────────────
        url_queue    = asyncio.PriorityQueue()
        filter_queue = asyncio.Queue()

        paginated_urls = expand_to_paginated_urls(
            config.url_queue, pages_per_query=PAGES_PER_QUERY
        )
        for start, url in paginated_urls:
            await url_queue.put((start, url))

        Actor.log.info(
            f"📥 Queued {len(paginated_urls)} pagination URL(s) "
            f"({PAGES_PER_QUERY} per seed) across {len(config.url_queue)} quer(y/ies)"
        )

        batch_positions: list[str] = []
        batch_links:     list[str] = []
        batch_uids:      list[str] = []
        batch_lock = asyncio.Lock()
        stop_event = asyncio.Event()

        Actor.log.info(
            f"🚀 Launching {concurrency} listing + {concurrency} processing workers "
            f"via Decodo Web Scraping API"
        )

        # ── Shared aiohttp session (one for all workers) ──────────────────────
        connector = aiohttp.TCPConnector(
            limit=concurrency * 4,          # total connection pool size
            limit_per_host=concurrency * 2, # per-host limit
        )
        async with aiohttp.ClientSession(connector=connector) as session:

            status_task = asyncio.create_task(status_logger(config, stop_event))

            if concurrency <= 5:
                n_primary = 1
                print(n_primary)
            else:
                n_primary = 2
                print(n_primary)
            n_hybrid  = concurrency - n_primary           

            primary_listing_tasks = [
                asyncio.create_task(
                    primary_listing_worker(
                        config=config,
                        url_queue=url_queue,
                        filter_queue=filter_queue,
                        batch_positions=batch_positions,
                        batch_links=batch_links,
                        batch_uids=batch_uids,
                        batch_lock=batch_lock,
                        session=session,
                        worker_id=i,
                    )
                )
                for i in range(n_primary)
            ]

            hybrid_listing_tasks = [
                asyncio.create_task(
                    hybrid_listing_worker(
                        config=config,
                        url_queue=url_queue,
                        filter_queue=filter_queue,
                        batch_positions=batch_positions,
                        batch_links=batch_links,
                        batch_uids=batch_uids,
                        batch_lock=batch_lock,
                        session=session,
                        worker_id=n_primary + i,
                    )
                )
                for i in range(n_hybrid)
            ]

            processing_tasks = [
                asyncio.create_task(
                    processing_worker(
                        config=config,
                        url_queue=url_queue,
                        filter_queue=filter_queue,
                        session=session,
                        worker_id=concurrency + i,
                    )
                )
                for i in range(concurrency)
            ]

            results = await asyncio.gather(
                *primary_listing_tasks, *hybrid_listing_tasks, *processing_tasks,
                return_exceptions=True,
            )
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    Actor.log.error(f"❌ Task {i} crashed: {result!r}")

        # ── Final flush ───────────────────────────────────────────────────────
        await _flush_shared_batch(
            config, batch_positions, batch_links, batch_uids, batch_lock, filter_queue
        )

        stop_event.set()
        await status_task

        Actor.log.info(
            f"🏁 All workers done  |  ✅ saved: {config.extracted_jobs_counter}/{config.max_jobs}"
        )

        # ── Google Sheets upload ──────────────────────────────────────────────
        gs_url = (config.google_sheet_url or "").strip()
        if gs_url:
            if config._saved_jobs:
                Actor.log.info(f"📊 Uploading {len(config._saved_jobs)} jobs to Google Sheets…")
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