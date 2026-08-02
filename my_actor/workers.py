"""
my_actor/workers.py
Listing and processing workers — Decodo HTTP version.

No browser contexts. Workers make HTTP requests through the shared
AccountRotator (Decodo Scraper API) and parse HTML with BeautifulSoup.

Worker split (same ratio as original):
  - 16% primary_listing_worker  → lists then drains filter_queue
  - 84% hybrid_listing_worker   → lists then drains filter_queue
  - concurrency processing_worker → dedicated filter_queue draining
"""
from __future__ import annotations

import asyncio
import random

import aiohttp
from apify import Actor

from .helpers import (
    ScraperConfig,
    parse_listing_cards,
    has_next_page,
    clear_queue,
    flush_batch,
    _flush_shared_batch,
    update_processed_uids,
    _base_url_of,
    purge_queue_beyond,
)
from .scrapedo import rotator
from .job_scraper import process_filter_jobs
from .config import ScraperSettings

# How many consecutive empty pops before a listing worker treats queue as drained
EMPTY_STREAK_LIMIT = 10
EMPTY_POLL_DELAY   = 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Shared Phase 1 — listing
# ─────────────────────────────────────────────────────────────────────────────

async def _run_listing_phase(
    config: ScraperConfig,
    url_queue: asyncio.PriorityQueue,
    filter_queue: asyncio.Queue,
    batch_positions: list,
    batch_links: list,
    batch_uids: list,
    batch_lock: asyncio.Lock,
    worker_id: int,
    session: aiohttp.ClientSession,
) -> None:
    empty_streak = 0

    while True:
        if await config.is_limit_reached():
            break
        if config.is_login_wall:
            clear_queue(url_queue)
            break

        try:
            start, job_search_url = url_queue.get_nowait()
            empty_streak = 0
        except asyncio.QueueEmpty:
            empty_streak += 1
            if empty_streak >= EMPTY_STREAK_LIMIT:
                break
            await asyncio.sleep(EMPTY_POLL_DELAY)
            continue

        base_url = _base_url_of(job_search_url)
        if config.is_past_last_page(base_url, start):
            url_queue.task_done()
            continue

        # ── Fetch the listing page with retries ───────────────────────────────
        html: str | None = None
        for attempt in range(ScraperSettings.MAX_RETRIES):
            try:
                html = await rotator.fetch(job_search_url, session)
                break
            except Exception as e:
                Actor.log.warning(
                    f"⏳ Worker {worker_id} listing attempt {attempt + 1}/"
                    f"{ScraperSettings.MAX_RETRIES} failed for {job_search_url}: {e}"
                )
                if attempt < ScraperSettings.MAX_RETRIES - 1:
                    await asyncio.sleep(
                        random.uniform(ScraperSettings.RETRY_DELAY_MIN, ScraperSettings.RETRY_DELAY_MAX)
                    )

        if html is None:
            Actor.log.error(f"❌ Worker {worker_id} gave up on listing: {job_search_url}")
            url_queue.task_done()
            continue

        Actor.log.info(f"🔍 Worker {worker_id} listing: {job_search_url}")

        # ── Detect end of results ─────────────────────────────────────────────
        if not has_next_page(html):
            if await config.mark_last_page(base_url, start):
                removed = await purge_queue_beyond(url_queue, base_url, start)
                Actor.log.info(
                    f"🛑 Last page (start={start}) for '{base_url}' — "
                    f"removed {removed} pagination URL(s) beyond it"
                )

        # ── Parse job cards ───────────────────────────────────────────────────
        try:
            cards = parse_listing_cards(html)
        except Exception as e:
            Actor.log.warning(f"⚠️ Worker {worker_id} card parse error on {job_search_url}: {e}")
            url_queue.task_done()
            continue

        if not cards:
            url_queue.task_done()
            continue

        # ── Filter and batch ──────────────────────────────────────────────────
        positions_to_add: list[str] = []
        links_to_add:     list[str] = []
        uids_to_add:      list[str] = []
        pushed = 0

        for card in cards:
            if await config.is_limit_reached():
                clear_queue(url_queue)
                break
            uid     = card["uid"]
            company = card["company"]
            if not uid:
                continue
            if not await config.try_add_job(uid, company):
                continue
            positions_to_add.append(card["position"])
            links_to_add.append(card["href"])
            uids_to_add.append(uid)
            pushed += 1

        if pushed:
            Actor.log.info(f"📋 Worker {worker_id} pushed {pushed} jobs to batch")

        should_flush = False
        snap_positions = snap_links = snap_uids = []

        async with batch_lock:
            batch_positions.extend(positions_to_add)
            batch_links.extend(links_to_add)
            batch_uids.extend(uids_to_add)
            if len(batch_positions) >= config.min_match_percentage:
                snap_positions = batch_positions.copy()
                snap_links     = batch_links.copy()
                snap_uids      = batch_uids.copy()
                batch_positions.clear()
                batch_links.clear()
                batch_uids.clear()
                should_flush = True

        if should_flush:
            await flush_batch(config, snap_positions, snap_links, snap_uids, filter_queue)

        url_queue.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# Shared Phase 2 — processing filter_queue
# ─────────────────────────────────────────────────────────────────────────────

async def _run_processing_phase(
    config: ScraperConfig,
    url_queue: asyncio.PriorityQueue,
    filter_queue: asyncio.Queue,
    worker_id: int,
    session: aiohttp.ClientSession,
) -> None:
    while True:
        if config.is_login_wall:
            clear_queue(filter_queue)
            break

        try:
            item = await asyncio.wait_for(filter_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            if (url_queue.empty() and filter_queue.empty()) or await config.is_limit_reached():
                break
            continue
        except asyncio.CancelledError:
            break

        url, pct = item
        try:
            saved = await process_filter_jobs(
                url=url, percentage=pct, config=config,
                filter_queue=filter_queue, session=session,
            )
            if not saved:
                await config.release_slot()
        except Exception as e:
            Actor.log.error(f"❌ Worker {worker_id} processing error: {e}")
            await config.release_slot()
        finally:
            filter_queue.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# primary_listing_worker — lists until url_queue is drained, then processes
# ─────────────────────────────────────────────────────────────────────────────

async def primary_listing_worker(
    config: ScraperConfig,
    url_queue: asyncio.PriorityQueue,
    filter_queue: asyncio.Queue,
    batch_positions: list,
    batch_links: list,
    batch_uids: list,
    batch_lock: asyncio.Lock,
    session: aiohttp.ClientSession,
    worker_id: int = 0,
) -> None:
    try:
        await _run_listing_phase(
            config, url_queue, filter_queue,
            batch_positions, batch_links, batch_uids, batch_lock,
            worker_id, session,
        )
    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} listing phase failed: {e}")

    await _flush_shared_batch(
        config, batch_positions, batch_links, batch_uids, batch_lock, filter_queue
    )

    try:
        Actor.log.info(f"Primary listing worker {worker_id} switched to processing.")
        await _run_processing_phase(config, url_queue, filter_queue, worker_id, session)
    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} processing phase failed: {e}")

    Actor.log.info(f"🧹 Worker {worker_id} done")


# ─────────────────────────────────────────────────────────────────────────────
# hybrid_listing_worker — same lifecycle as primary
# ─────────────────────────────────────────────────────────────────────────────

async def hybrid_listing_worker(
    config: ScraperConfig,
    url_queue: asyncio.PriorityQueue,
    filter_queue: asyncio.Queue,
    batch_positions: list,
    batch_links: list,
    batch_uids: list,
    batch_lock: asyncio.Lock,
    session: aiohttp.ClientSession,
    worker_id: int = 0,
) -> None:
    try:
        await _run_listing_phase(
            config, url_queue, filter_queue,
            batch_positions, batch_links, batch_uids, batch_lock,
            worker_id, session,
        )
    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} listing phase failed: {e}")

    await _flush_shared_batch(
        config, batch_positions, batch_links, batch_uids, batch_lock, filter_queue
    )

    try:
        Actor.log.info(f"Hybrid listing worker {worker_id} switched to processing.")
        await _run_processing_phase(config, url_queue, filter_queue, worker_id, session)
    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} processing phase failed: {e}")

    Actor.log.info(f"🧹 Worker {worker_id} done")


# ─────────────────────────────────────────────────────────────────────────────
# processing_worker — dedicated processing-only worker
# ─────────────────────────────────────────────────────────────────────────────

async def processing_worker(
    config: ScraperConfig,
    url_queue: asyncio.PriorityQueue,
    filter_queue: asyncio.Queue,
    session: aiohttp.ClientSession,
    worker_id: int = 0,
) -> None:
    try:
        await _run_processing_phase(config, url_queue, filter_queue, worker_id, session)
    except Exception as e:
        Actor.log.error(f"❌ Processing worker {worker_id} failed: {e}")

    Actor.log.info(f"🧹 Processing worker {worker_id} done")