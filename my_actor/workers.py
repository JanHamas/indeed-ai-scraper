"""
my_actor/workers.py
Listing and processing workers — Bright Data Web Unlocker HTTP version.

No browser contexts. Workers make HTTP requests through the shared
AccountRotator (Bright Data Web Unlocker API) and parse HTML with BeautifulSoup.

Worker split (see main.py):
  - listing workers (primary + hybrid) are hard-capped at
    ScraperSettings.MAX_LISTING_WORKERS (point 3)
  - processing workers are NOT capped by a fixed number — main.py sizes
    them off this run's fair share of live Firecrawl capacity instead
    (point 3)
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
    save_debug_html

)
from .firecrawl_client import firecrawl as evomi
from .job_scraper import process_filter_jobs
from .config import ScraperSettings


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

        try:
            item = await asyncio.wait_for(filter_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            if (url_queue.empty() and filter_queue.empty()) or await config.is_limit_reached():
                break
            continue
        except asyncio.CancelledError:
            break

        # Third element is the "no company found" retry counter (point 6).
        url, pct = item
        try:
            await process_filter_jobs(
                url=url, percentage=pct, config=config,
                session=session,

            )
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
        while True:
            # Stay alive for relisting if processing worker miss or skipp jobs eg, expired etc
            # Done for real: we've already pushed everything required.
            if config.pushed_jobs >= config.max_jobs:
                break

            # Nothing left to list — nothing more this worker can do.
            if url_queue.empty():
                break

            # Slots are full right now, but the queue still has pages and
            # pushed_jobs hasn't caught up — a release_slot() from a
            # skipped/expired job downstream could free a slot. Wait
            # instead of exiting, then re-check.
            if await config.is_limit_reached():
                await asyncio.sleep(.5)
                continue
        
            try:
                start, job_search_url = url_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
    
            base_url = _base_url_of(job_search_url)
            if config.is_past_last_page(base_url, start):
                url_queue.task_done()
                continue
    
            # ── Fetch the listing page with retries ───────────────────────────────
            html: str | None = None
            for attempt in range(ScraperSettings.MAX_RETRIES):
                try:
                    html = await evomi.fetch(job_search_url, session, config=config)
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
            # save_debug_html(html, job_search_url, tag=f"w{worker_id}") 
    
            Actor.log.info(f"🔍 Worker {worker_id} listing: {job_search_url}")
            # ── Parse job cards FIRST ───────────────────────────────────────────
            try:
                cards = parse_listing_cards(html)
            except Exception as e:
                Actor.log.warning(f"⚠️ Worker {worker_id} card parse error on {job_search_url}: {e}")
                url_queue.task_done()
                continue

            if not cards:
                # No cards found. This could genuinely be an empty results page,
                # OR a bad/blocked fetch (bot-check page, truncated content, etc).
                # Either way, we must NOT purge future pagination URLs off this
                # signal alone — that's how legitimate pages 3-34 got silently
                # deleted from the queue for every keyword in this run.
                Actor.log.warning(
                    f"⚠️ Worker {worker_id} found 0 cards on {job_search_url} — "
                    f"treating as a failed/empty fetch, NOT marking as last page."
                )
                url_queue.task_done()
                continue

            # ── Only now, with confirmed real content on the page, check pagination ──
            if not has_next_page(html):
                if await config.mark_last_page(base_url, start):
                    removed = await purge_queue_beyond(url_queue, base_url, start)
                    Actor.log.info(
                        f"🛑 Last page (start={start}) for '{base_url}' — "
                        f"removed {removed} pagination URL(s) beyond it"
                    )
    
            # ── Filter and batch ──────────────────────────────────────────────────
            positions_to_add: list[str] = []
            links_to_add:     list[str] = []
            uids_to_add:      list[str] = []
            pushed = 0
    
            for card in cards:
                if await config.is_limit_reached():
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
    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} listing phase failed: {e}")

    await _flush_shared_batch(
        config, batch_positions, batch_links, batch_uids, batch_lock, filter_queue
    )

    try:
        Actor.log.info(f"Primary listing worker {worker_id} switched to processing."
                       f"url_queue empty: {url_queue.empty()} | "
                       f"pushed jobs: {config.pushed_jobs}"
                       )
        await _run_processing_phase(config, url_queue, filter_queue, worker_id, session)
    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} processing phase failed: {e}")



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
        while True:
            # Direct switch to processing worker once first time listing reached to max jobs
            if await config.is_limit_reached() or (url_queue.empty() or config.pushed_jobs >= config.max_jobs):
                break
            try:
                start, job_search_url = url_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
    
            base_url = _base_url_of(job_search_url)
            if config.is_past_last_page(base_url, start):
                url_queue.task_done()
                continue
    
            # ── Fetch the listing page with retries ───────────────────────────────
            html: str | None = None
            for attempt in range(ScraperSettings.MAX_RETRIES):
                try:
                    html = await evomi.fetch(job_search_url, session, config=config)
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
            # save_debug_html(html, job_search_url, tag=f"w{worker_id}") 
    
            Actor.log.info(f"🔍 Worker {worker_id} listing: {job_search_url}")
    
            # ── Parse job cards FIRST ───────────────────────────────────────────
            try:
                cards = parse_listing_cards(html)
            except Exception as e:
                Actor.log.warning(f"⚠️ Worker {worker_id} card parse error on {job_search_url}: {e}")
                url_queue.task_done()
                continue

            if not cards:
                # No cards found. This could genuinely be an empty results page,
                # OR a bad/blocked fetch (bot-check page, truncated content, etc).
                # Either way, we must NOT purge future pagination URLs off this
                # signal alone — that's how legitimate pages 3-34 got silently
                # deleted from the queue for every keyword in this run.
                Actor.log.warning(
                    f"⚠️ Worker {worker_id} found 0 cards on {job_search_url} — "
                    f"treating as a failed/empty fetch, NOT marking as last page."
                )
                url_queue.task_done()
                continue

            # ── Only now, with confirmed real content on the page, check pagination ──
            if not has_next_page(html):
                if await config.mark_last_page(base_url, start):
                    removed = await purge_queue_beyond(url_queue, base_url, start)
                    Actor.log.info(
                        f"🛑 Last page (start={start}) for '{base_url}' — "
                        f"removed {removed} pagination URL(s) beyond it"
                    )
            # ── Filter and batch ──────────────────────────────────────────────────
            positions_to_add: list[str] = []
            links_to_add:     list[str] = []
            uids_to_add:      list[str] = []
            pushed = 0
    
            for card in cards:
                if await config.is_limit_reached():
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
    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} listing phase failed: {e}")

    await _flush_shared_batch(
        config, batch_positions, batch_links, batch_uids, batch_lock, filter_queue
    )

    try:
        Actor.log.info(f"Hybrid listing worker {worker_id} switched to processing."
                       f"url_queue empty: {url_queue.empty()} | is_limit: {await config.is_limit_reached()}")
        await _run_processing_phase(config, url_queue, filter_queue, worker_id, session)
    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} processing phase failed: {e}")

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