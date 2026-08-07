"""
Indeed Scraper — Apify Actor (Firecrawl edition)
Entry point: my_actor/main.py
"""
from __future__ import annotations

import asyncio

import aiohttp
from apify import Actor

from .config import ScraperSettings
from .firecrawl_client import firecrawl
from .run_registry import run_registry
from .helpers import (
    load_scraper_config,
    expand_to_paginated_urls,
    extract_job_ids_from_urls,
    build_indeed_search_urls,
    showstartinginfo,
    status_logger,
    _flush_shared_batch,
    sanitize_indeed_url,
    get_about_me,
)
from .workers import primary_listing_worker, hybrid_listing_worker, processing_worker
from .gsheet import upload_to_google_sheet

PAGES_PER_QUERY = ScraperSettings.PAGES_PER_QUERY


async def main() -> None:
    async with Actor:
        # ── Point 1 & 4: join() now blocks until fewer than
        # CONCURRENT_MAX_USERS runs are active, then this run gets folded
        # into the fair-share split of Firecrawl's capacity.
        await run_registry.join()
        try:
            await _run()
        finally:
            await run_registry.leave()

import time
async def _run() -> None:
        actor_input = await Actor.get_input() or {}
        start_time = time.perf_counter()
        # ── Core config ───────────────────────────────────────────────────────
        url_queue_raw    = actor_input.get("start_urls", [])
        ignore_companies = actor_input.get("ignore_companies", "")
        ignore_related   = actor_input.get("ignore_related", "")
        max_jobs         = int(actor_input.get("max_jobs", 50))
        per_company_jobs = int(actor_input.get("per_company_jobs", 5))
        min_match_pct    = int(actor_input.get("min_match_percentage", 0))

        # ── Concurrency: split Firecrawl's total capacity fairly across every
        # currently-active run (point 2 & 3 & 4). 1 run = 100%, 2 runs = ~50%
        # each, 3 runs = ~33% each, etc. Never goes below 1. (A 4th+ run
        # never gets here concurrently with more than CONCURRENT_MAX_USERS-1
        # others, since run_registry.join() makes it wait its turn first.)
        active_runs = await run_registry.active_run_count()
        fair_share  = max(1, firecrawl.max_concurrency // active_runs)
        concurrency = min(int(actor_input.get("concurrency", 15)), fair_share)

        Actor.log.info(
            f"⚖️  {active_runs} run(s) active right now — this run gets "
            f"{concurrency}/{firecrawl.max_concurrency} Firecrawl slot(s)"
        )

        # ── Feature flags ─────────────────────────────────────────────────────
        ai_matching_enabled      = bool(actor_input.get("ai_matching_enabled", True))
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

        # ── AI matching free-text: prefer the user's keyword lines; fall back
        # to the `q=` term on every search URL. Uses `url_list` (already
        # resolved to plain sanitized strings above) rather than the raw
        # `url_queue_raw` input, which can contain dicts and would break
        # get_about_me()'s URL parsing. ─────────────────────────────────────
        about_me = get_about_me(search_keywords, url_list)

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
            search_keywords=search_keywords,
            search_location=search_location,
            search_country=search_country,
            ai_matching_enabled=ai_matching_enabled,
            scrape_company_details=scrape_company_details,
            save_unique_only=save_unique_only,
            follow_apply_redirect=follow_apply_redirect,
            skip_expired_jobs=skip_expired_jobs,
            skip_ignore_related_jobs=skip_ignore_related_jobs,
            google_sheet_url=google_sheet_url,
            sheet_name=sheet_name,
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

        # ── Point 3: listing workers hard-capped at MAX_LISTING_WORKERS (10).
        # Processing workers are NOT given a fixed cap — they're sized to
        # `concurrency`, i.e. however much of Firecrawl's total capacity this
        # run currently has a fair-share claim to, so they scale up or down
        # automatically as accounts run out of credit or other runs join/leave.
        n_listing = min(concurrency, ScraperSettings.MAX_LISTING_WORKERS)
        n_processing = concurrency

        Actor.log.info(
            f"🚀 Launching {n_listing} listing (capped at "
            f"{ScraperSettings.MAX_LISTING_WORKERS}) + {n_processing} processing "
            f"worker(s) via Firecrawl ({len(firecrawl.active_accounts)} account(s) active)"
        )

        # ── Shared aiohttp session (one for all workers) ──────────────────────
        connector = aiohttp.TCPConnector(
            limit=concurrency * 4,          # total connection pool size
            limit_per_host=concurrency * 2, # per-host limit
        )
        async with aiohttp.ClientSession(connector=connector) as session:

            status_task = asyncio.create_task(status_logger(config, stop_event))

            if n_listing <= 5:
                n_primary = 1
            else:
                n_primary = 2
            n_hybrid = n_listing - n_primary

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
                        worker_id=n_listing + i,
                    )
                )
                for i in range(n_processing)
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

        # ── Point 2: this is the number to bill the user on — every real
        # Firecrawl API call attempt made for this run, including retries
        # triggered by strict filters, expired-page skips, or "no company
        # found" re-fetches. ─────────────────────────────────────────────────
        Actor.log.info(f"📡 Total billable Firecrawl requests this run: {config.total_requests}")

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
        Actor.log.info(f"Total time taken: {time.perf_counter() - start_time}")