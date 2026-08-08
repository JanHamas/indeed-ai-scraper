"""
Indeed Scraper — Apify Actor (Firecrawl edition)
Entry point: my_actor/main.py
"""
from __future__ import annotations

import asyncio
import time

import aiohttp
from apify import Actor

from .config import ScraperSettings
from .email_logs import build_run_summary, send_logs_email
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
    _get_matcher,
)
from .workers import primary_listing_worker, hybrid_listing_worker, processing_worker
from .gsheet import upload_to_google_sheet

PAGES_PER_QUERY = ScraperSettings.PAGES_PER_QUERY

# ── Free-tier limit: Apify accounts on the free plan (isPaying == False)
# never get more than this many jobs per run, no matter what max_jobs they
# ask for. ────────────────────────────────────────────────────────────────

async def _resolve_max_jobs(requested_max_jobs: int) -> tuple[int, bool]:
    try:
        me = await Actor.apify_client.user("me").get()
        is_paying = bool((me or {}).get("isPaying", False))
    except Exception as e:
        Actor.log.warning(f"⚠️ Could not verify Apify plan — defaulting to free-tier limit: {e}")
        is_paying = False

    if is_paying:
        Actor.log.info(f"💳 Paid Apify plan detected — max_jobs stays at {requested_max_jobs}")
        return requested_max_jobs, True

    effective = min(requested_max_jobs, ScraperSettings.FREE_TIER_MAX_JOBS)
    Actor.log.info("=" * 80)
    Actor.log.info(f"🆓 FREE-TIER APIFY ACCOUNT — max {ScraperSettings.FREE_TIER_MAX_JOBS} jobs per run")
    Actor.log.info(f"   ↳ This run will collect up to {effective} job(s)")
    if requested_max_jobs > ScraperSettings.FREE_TIER_MAX_JOBS:
        Actor.log.info(f"   ↳ You requested {requested_max_jobs} — upgrade your plan to raise this limit")
    Actor.log.info("=" * 80)

    await Actor.set_status_message(
        f"🆓 Free-tier account: capped at {ScraperSettings.FREE_TIER_MAX_JOBS} jobs/run "
        f"(this run: {effective})"
    )
    return effective, False


async def _safe_get_credit_usage() -> list[dict] | None:
    """
    Look up remaining Firecrawl credits for every configured account, for
    inclusion in the run-summary email. Best-effort only — a failure here
    (network hiccup, one dead key, etc.) should never prevent the actual
    run summary from being sent.
    """
    try:
        async with aiohttp.ClientSession() as session:
            return await firecrawl.get_all_credit_usage(session)
    except Exception as e:
        Actor.log.warning(f"⚠️ Could not fetch Firecrawl credit usage: {e}")
        return None


async def main() -> None:
    async with Actor:
        # ── join() blocks until fewer than CONCURRENT_MAX_USERS
        # runs are active, then this run gets folded into the fair-share
        # split of Firecrawl's capacity.
        await run_registry.join()
        try:
            await _run()
        finally:
            await run_registry.leave()


async def _run() -> None:
    start_time = time.perf_counter()
    actor_input = await Actor.get_input() or {}

    # ── Core config ──────────────────────────────────────────────────────
    url_queue_raw    = actor_input.get("start_urls", [])
    ignore_companies = actor_input.get("ignore_companies", "")
    ignore_related   = actor_input.get("ignore_related", "")
    max_jobs, is_paying = await _resolve_max_jobs(int(actor_input.get("max_jobs", 50)))
    per_company_jobs = int(actor_input.get("per_company_jobs", 5))
    min_match_pct    = int(actor_input.get("min_match_percentage", 0))

    # ── Concurrency: split Firecrawl's total capacity fairly across every
    # currently-active run. 1 run = 100%, 2 runs = ~50% each, etc. Never
    # goes below 1.
    active_runs = await run_registry.active_run_count()
    fair_share  = max(1, firecrawl.max_concurrency // active_runs)
    concurrency = min(int(actor_input.get("concurrency", 15)), fair_share)

    Actor.log.info(
        f"⚖️  {active_runs} run(s) active right now — this run gets "
        f"{concurrency}/{firecrawl.max_concurrency} Firecrawl slot(s)"
    )

    # ── Feature flags ────────────────────────────────────────────────────
    ai_matching_enabled      = bool(actor_input.get("ai_matching_enabled", True))
    scrape_company_details   = bool(actor_input.get("scrape_company_details", False))
    save_unique_only         = bool(actor_input.get("save_unique_only", True))
    follow_apply_redirect    = bool(actor_input.get("follow_apply_redirect", False))
    skip_expired_jobs        = bool(actor_input.get("skip_expired_jobs", False))
    skip_ignore_related_jobs = bool(actor_input.get("skip_ignore_related_jobs", False))

    # ── Search builder fields ───────────────────────────────────────────
    search_keywords_raw = actor_input.get("search_keywords", "").strip()
    search_location     = actor_input.get("search_location", "").strip()
    search_country      = actor_input.get("search_country", "us").strip().lower()
    date_filter         = actor_input.get("date_filter", "any").strip()
    search_keywords     = [k.strip() for k in search_keywords_raw.splitlines() if k.strip()]

    # ── Google Sheets ────────────────────────────────────────────────────
    google_sheet_url = actor_input.get("google_sheet_url", "")

    # ── Processed job URLs → extract IDs to skip ────────────────────────
    processed_urls_raw: list = actor_input.get("processed_job_urls", [])
    processed_url_list: list[str] = []
    for entry in processed_urls_raw:
        if isinstance(entry, str):
            processed_url_list.append(entry.strip())
        elif isinstance(entry, dict):
            processed_url_list.append(entry.get("url", "").strip())
    processed_uids = extract_job_ids_from_urls([u for u in processed_url_list if u])

    # ── Resolve URL list ─────────────────────────────────────────────────
    url_list: list[str] = []
    for entry in url_queue_raw:
        if isinstance(entry, str):
            url_list.append(entry.strip())
        elif isinstance(entry, dict):
            url_list.append(entry.get("url", "").strip())
    url_list = [sanitize_indeed_url(u) for u in url_list if u]

    # ── has_raw_start_urls is True only when the USER supplied start_urls
    # directly in the input — not when we built url_list ourselves below
    # from search_keywords. This flag decides precedence between the two
    # inputs (see get_about_me() in helpers.py). ───────────────────────
    has_raw_start_urls = bool(url_queue_raw)

    if has_raw_start_urls and search_keywords:
        # Both inputs were given. Today, direct start_urls win: url_list
        # stays exactly what the user pasted, search_keywords is NOT used
        # to build additional URLs, and the AI-matching "about_me" text is
        # pulled from the q= param on each start_url instead of from
        # search_keywords. Logged explicitly so this isn't a silent
        # surprise when both fields are filled in.
        Actor.log.info(
            "⚠️ Both 'start_urls' and 'search_keywords' were provided — "
            "start_urls take priority this run. search_keywords is ignored "
            "for building the URL list and for AI-matching text (which is "
            "instead pulled from the q= param on each start_url)."
        )

    if not url_list:
        if not search_keywords:
            msg = (
                "❌ Nothing to scrape — provide either 'start_urls' (Indeed search URLs) "
                "or 'search_keywords' (positions/keywords to search for)."
            )
            Actor.log.error(msg)
            await asyncio.to_thread(
                send_logs_email,
                subject="❌ Indeed Scraper FAILED - no input",
                body=msg,
                log=Actor.log,
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

    # ── AI matching free-text: prefer the user's keyword lines; fall back
    # to the `q=` term on every search URL.
    about_me = get_about_me(search_keywords, url_list, has_raw_start_urls=has_raw_start_urls)

    # ── Build config ─────────────────────────────────────────────────────
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
        is_paying=is_paying,
    )

    await showstartinginfo(config)

    # ── Snapshot BEFORE scraping starts. `config.processed_uids` is also
    # the in-run dedup set (see `try_add_job` in helpers.py), so it grows
    # with every job seen this run. We want the report to reflect only the
    # IDs the user pasted into `processed_job_urls`, not that inflated
    # post-run count. ────────────────────────────────────────────────────
    previously_processed_count = len(config.processed_uids)

    try:
        await _scrape(config)
    except Exception as e:
        Actor.log.error(f"❌ Fatal error during scrape: {type(e).__name__}: {e}")
        credit_usage = await _safe_get_credit_usage()
        body = build_run_summary(
            config, start_time, errors=[str(e)], run_status="FAILED",
            previously_processed_count=previously_processed_count,
            credit_usage=credit_usage,
        )
        await asyncio.to_thread(
            send_logs_email,
            subject="❌ Indeed Scraper FAILED",
            body=body,
            log=Actor.log,
        )
        raise

    # ── Google Sheets upload ────────────────────────────────────────────
    gs_url = (config.google_sheet_url or "").strip()
    if gs_url:
        if config._saved_jobs:
            Actor.log.info(f"📊 Uploading {len(config._saved_jobs)} jobs to Google Sheets…")
            await upload_to_google_sheet(link=gs_url, jobs=config._saved_jobs, log=Actor.log)
        else:
            Actor.log.warning("⚠️ No jobs to upload to Google Sheets")
    else:
        Actor.log.info("⏭️ No Google Sheet URL — skipping upload")

    Actor.log.info("✅ Actor finished successfully")
    Actor.log.info(f"Total time taken: {time.perf_counter() - start_time}")

    # ── Pull remaining credits for every configured Firecrawl account so
    # the run-summary email shows exactly which ones need topping up.
    # Best-effort: never blocks the success email from going out. ───────
    credit_usage = await _safe_get_credit_usage()

    body = build_run_summary(
        config, start_time, run_status="COMPLETED",
        previously_processed_count=previously_processed_count,
        credit_usage=credit_usage,
    )
    await asyncio.to_thread(
        send_logs_email,
        subject=f"✅ Indeed Scraper Complete - {len(config._saved_jobs)} Jobs Saved",
        body=body,
        log=Actor.log,
    )


async def _scrape(config) -> None:
    """Run all listing/processing workers for this config until done."""

    url_queue    = asyncio.PriorityQueue()
    filter_queue = asyncio.Queue()

    paginated_urls = expand_to_paginated_urls(config.url_queue, pages_per_query=PAGES_PER_QUERY)
    for start, url in paginated_urls:
        await url_queue.put((start, url))

    # ── Warm up the AI matching model in the background, in parallel with
    # the first listing page fetches. With the model baked into the Docker
    # image at build time, this is now just an in-memory load (a few
    # seconds) rather than a cold network download — but we still don't
    # want the first flush_batch() call to be the one paying that cost
    # serially and blocking the pipeline. If AI matching is off, or
    # about_me ends up empty, config.ai_matching_enabled is False and this
    # is a no-op — no torch/sentence-transformers import happens at all.
    warmup_task = None
    if config.ai_matching_enabled:
        warmup_task = asyncio.create_task(_get_matcher())

    batch_positions: list[str] = []
    batch_links:     list[str] = []
    batch_uids:      list[str] = []
    batch_lock = asyncio.Lock()
    stop_event = asyncio.Event()

    concurrency = config.concurrency
    # ── Listing workers hard-capped at MAX_LISTING_WORKERS (10).
    # Processing workers scale directly with this run's fair-share
    # concurrency, so they speed up or slow down as accounts run out of
    # credit or other runs join/leave.
    n_listing    = min(concurrency, ScraperSettings.MAX_LISTING_WORKERS)
    n_processing = concurrency
    n_primary    = 1 if n_listing <= 5 else 2
    n_hybrid     = n_listing - n_primary

    connector = aiohttp.TCPConnector(
        limit=concurrency * 4,
        limit_per_host=concurrency * 2,
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        status_task = asyncio.create_task(status_logger(config, stop_event))

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

        stop_event.set()
        await status_task

        # ── Make sure the warm-up task is settled before we finish up.
        # In the normal case it completed ages ago (it only needed a few
        # seconds, workers ran for much longer) — this just surfaces any
        # load error explicitly instead of letting it resurface later,
        # confusingly, inside a flush_batch() call.
        if warmup_task:
            try:
                await warmup_task
            except Exception as e:
                Actor.log.warning(f"⚠️ Model warm-up task failed: {e}")

    # ── Final flush ──────────────────────────────────────────────────────
    await _flush_shared_batch(
        config, batch_positions, batch_links, batch_uids, batch_lock, filter_queue
    )

    Actor.log.info(
        f"🏁 All workers done  |  ✅ saved: {config.extracted_jobs_counter}/{config.max_jobs}"
    )
    # ── This is the number to bill the user on — every real Firecrawl API
    # call attempt made for this run, including retries triggered by strict
    # filters, expired-page skips, or "no company found" re-fetches.
    Actor.log.info(f"📡 Total billable requests this run: {config.total_requests}")