"""
src/workers.py
listing_worker and processing_worker — ported to Apify (no Django).
"""
from __future__ import annotations

import asyncio
import random
from urllib.parse import urljoin, urlparse, parse_qs

from apify import Actor
from playwright.async_api import Browser

from .config import ScraperSettings
from .helpers import (
    ScraperConfig,
    create_context,
    open_jobs_search_page,
    simulate_human_behavior,
    get_total_jobs,
    build_and_enqueue_jobs_search_urls,
    clear_queue,
    flush_batch,
    _flush_shared_batch,
    update_processed_uids,
)
from .job_scraper import process_filter_jobs


# ─────────────────────────────────────────────────────────────────────────────
# Context rotation helper (shared by both workers)
# ─────────────────────────────────────────────────────────────────────────────
async def _maybe_rotate_context(
    current_context,
    current_page,
    config: ScraperConfig,
    playwright_proxy: dict | None,
    worker_id: int,
    phase: str,
    browser: Browser,
    account_cookies: list | None = None,
):
    context_id = id(current_context)
    async with config.context_lock:
        count = config._context_requests.get(context_id, 0) + 1
        config._context_requests[context_id] = count

    if count < ScraperSettings.context_rotate_limit:
        return current_context, current_page

    Actor.log.info(
        f"🔄 Worker {worker_id} [{phase}] hit {count} requests — rotating context"
    )
    await current_context.close()
    async with config.context_lock:
        config._context_requests.pop(context_id, None)

    new_ctx = await create_context(browser, playwright_proxy)
    if account_cookies:
        await new_ctx.add_cookies(account_cookies)
    async with config.context_lock:
        config._context_requests[id(new_ctx)] = 0

    new_page = await new_ctx.new_page()
    Actor.log.info(f"✅ Worker {worker_id} [{phase}] — fresh context + page ready")
    return new_ctx, new_page


# ─────────────────────────────────────────────────────────────────────────────
# listing_worker
# ─────────────────────────────────────────────────────────────────────────────
async def listing_worker(
    browser: Browser,
    config: ScraperConfig,
    url_queue: asyncio.Queue,
    filter_queue: asyncio.Queue,
    batch_positions: list,
    batch_links: list,
    batch_uids: list,
    batch_lock: asyncio.Lock,
    playwright_proxy: dict | None,
    worker_id: int = 0,
) -> None:
    context = await create_context(browser, playwright_proxy)
    async with config.context_lock:
        config._context_requests[id(context)] = 0

    page = await context.new_page()

    # ── Phase 1: Listing ──────────────────────────────────────────────────────
    try:
        IDLE_TIMEOUT, POLL_INTERVAL = 60, 0.5
        idle_elapsed = 0

        while True:
            if await config.is_limit_reached():
                clear_queue(url_queue)
                break

            try:
                job_search_url = url_queue.get_nowait()
                idle_elapsed   = 0
            except asyncio.QueueEmpty:
                await asyncio.sleep(POLL_INTERVAL)
                idle_elapsed += POLL_INTERVAL
                if idle_elapsed >= IDLE_TIMEOUT:
                    await _flush_shared_batch(
                        config, batch_positions, batch_links, batch_uids, batch_lock, filter_queue
                    )
                    break
                continue

            try:
                context, page = await _maybe_rotate_context(
                    context, page, config, playwright_proxy, worker_id, "listing", browser
                )

                await open_jobs_search_page(page, job_search_url, url_queue)
                await simulate_human_behavior(page)

                # Enqueue pagination URLs on first page
                try:
                    parsed = urlparse(page.url)
                    params = parse_qs(parsed.query)
                    if "start" not in params:
                        total_jobs = await get_total_jobs(page)
                        await build_and_enqueue_jobs_search_urls(
                            total_jobs, job_search_url, url_queue
                        )
                        Actor.log.info(
                            f"🌐 Opened: {total_jobs}+ jobs found in {job_search_url}"
                        )
                except Exception as e:
                    Actor.log.warning(f"⚠️ Pagination error: {e}")

                # Scrape job cards
                try:
                    companies_slc = await page.query_selector_all(
                        '.cardOutline:not([aria-hidden="true"]) [data-testid="company-name"]'
                    )
                    positions_slc = await page.query_selector_all(
                        '.cardOutline:not([aria-hidden="true"]) .jobTitle'
                    )
                    links_slc = await page.query_selector_all(
                        '.cardOutline:not([aria-hidden="true"]) tr td a'
                    )
                    if len(companies_slc) != len(positions_slc) or len(companies_slc) != len(links_slc):
                        Actor.log.warning(f"⚠️ Selector mismatch on {page.url}")
                except Exception as e:
                    Actor.log.warning(f"⚠️ Card selection error: {e}")
                    url_queue.task_done()
                    continue

                uids    = [await link.get_attribute("data-jk") for link in links_slc]
                missing = [i for i, u in enumerate(uids) if not u]
                if missing:
                    Actor.log.warning(f"⚠️ {len(missing)} cards missing data-jk on {page.url}")

                pushed           = 0
                positions_to_add = []
                links_to_add     = []
                uids_to_add      = []

                for company, position, link, uid in zip(
                    companies_slc, positions_slc, links_slc, uids
                ):
                    if await config.is_limit_reached():
                        clear_queue(url_queue)
                        break
                    if not uid:
                        continue

                    company_text  = await company.inner_text()
                    position_text = await position.inner_text()
                    link_href     = urljoin("https://indeed.com", await link.get_attribute("href"))

                    if not await config.try_add_job(uid, company_text):
                        continue

                    positions_to_add.append(position_text)
                    links_to_add.append(link_href)
                    uids_to_add.append(uid)
                    pushed += 1

                if pushed:
                    Actor.log.info(f"📋 Worker {worker_id} pushed {pushed} jobs to batch")

                # Append to shared batch; flush if large enough
                should_flush = False
                snap_positions = snap_links = snap_uids = []

                async with batch_lock:
                    batch_positions.extend(positions_to_add)
                    batch_links.extend(links_to_add)
                    batch_uids.extend(uids_to_add)

                    if len(batch_positions) >= ScraperSettings.get_percentages_batch_size:
                        snap_positions = batch_positions.copy()
                        snap_links     = batch_links.copy()
                        snap_uids      = batch_uids.copy()
                        batch_positions.clear()
                        batch_links.clear()
                        batch_uids.clear()
                        should_flush = True

                if should_flush:
                    await flush_batch(config, snap_positions, snap_links, snap_uids, filter_queue)

            except Exception as e:
                Actor.log.error(f"❌ Worker {worker_id} listing loop error: {e}")
            finally:
                url_queue.task_done()

    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} listing phase failed: {e}")

    # ── Phase 2: Drain filter_queue ───────────────────────────────────────────
    try:
        while True:
            if filter_queue.empty() and url_queue.empty():
                break
            if filter_queue.empty():
                await asyncio.sleep(0.1)
                continue
            try:
                item = await asyncio.wait_for(filter_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            link, pct = item
            context, page = await _maybe_rotate_context(
                context, page, config, playwright_proxy, worker_id, "processing", browser
            )
            try:
                await process_filter_jobs(
                    page=page,
                    link=link,
                    percentage=pct,
                    config=config,
                )
            except Exception as e:
                Actor.log.error(f"❌ Worker {worker_id} processing error: {e}")
            finally:
                filter_queue.task_done()

    except Exception as e:
        Actor.log.error(f"❌ Worker {worker_id} processing phase failed: {e}")

    finally:
        async with config.context_lock:
            config._context_requests.pop(id(context), None)
        await context.close()
        Actor.log.info(f"🧹 Worker {worker_id} cleaned up")


# ─────────────────────────────────────────────────────────────────────────────
# processing_worker  (dedicated — only processes filter_queue)
# ─────────────────────────────────────────────────────────────────────────────
async def processing_worker(
    browser: Browser,
    config: ScraperConfig,
    url_queue: asyncio.Queue,
    filter_queue: asyncio.Queue,
    playwright_proxy: dict | None,
    worker_id: int = 0,
) -> None:
    context = await create_context(browser, playwright_proxy)
    async with config.context_lock:
        config._context_requests[id(context)] = 0

    page = await context.new_page()

    try:
        while True:
            if filter_queue.empty() and url_queue.empty():
                break
            if filter_queue.empty():
                await asyncio.sleep(0.1)
                continue
            try:
                item = await asyncio.wait_for(filter_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            link, pct = item

            context_id = id(context)
            async with config.context_lock:
                count = config._context_requests.get(context_id, 0) + 1
                config._context_requests[context_id] = count

            if count >= ScraperSettings.context_rotate_limit:
                Actor.log.info(f"🔄 Processing worker {worker_id} rotating context at {count}")
                await context.close()
                async with config.context_lock:
                    config._context_requests.pop(context_id, None)
                context = await create_context(browser, playwright_proxy)
                async with config.context_lock:
                    config._context_requests[id(context)] = 0
                page = await context.new_page()

            try:
                await process_filter_jobs(
                    page=page,
                    link=link,
                    percentage=pct,
                    config=config,
                )
            except Exception as e:
                Actor.log.error(f"❌ Processing worker {worker_id} error: {e}")
            finally:
                filter_queue.task_done()

    except Exception as e:
        Actor.log.error(f"❌ Processing worker {worker_id} failed: {e}")
    finally:
        async with config.context_lock:
            config._context_requests.pop(id(context), None)
        await context.close()
        Actor.log.info(f"🧹 Processing worker {worker_id} cleaned up")
