"""
my_actor/run_registry.py
Replaces actor_lock.py.

How it works, in plain terms:
- actor_lock.py used to let only ONE run happen at a time, anywhere —
  everyone else waited. That's gone.
- Instead, every run "checks in" to a shared Key-Value store (same trick
  as before: a *named* store is shared across every run/container, so it
  works as a mailbox). Each run writes its run-id + a timestamp.
- Before starting work, a run counts how many OTHER run-ids are checked
  in and still "alive" (heartbeated recently), and divides Firecrawl's
  total capacity by that count:
    - 1 active run   -> 100% of firecrawl.max_concurrency
    - 2 active runs  -> ~50% each
    - 3 active runs  -> ~33% each
    - etc.
- Each run heartbeats (refreshes its timestamp) every 45s while working,
  so the others can see it's still alive. If a run crashes without
  checking out, its entry goes stale after 3 minutes and stops counting
  against everyone else's share — no manual cleanup, no 1-hour freeze
  like the old lock had.
- NEW: `ScraperSettings.CONCURRENT_MAX_USERS` is now actually enforced.
  `join()` will not register a run while that many OTHER runs are already
  active — it polls every `USER_QUEUE_POLL_SECONDS` and waits its turn
  instead. Once a slot frees up (someone finishes or goes stale), the
  waiting run joins and immediately gets folded into the fair-share split
  above. This is what makes user #3 (and beyond) queue in a chain behind
  the first CONCURRENT_MAX_USERS runs, rather than piling on and diluting
  everyone's share.

NOTE: the split is calculated once at startup, not continuously
rebalanced mid-run. If Run B joins while Run A is already going, Run A
keeps its original (larger) concurrency until it happens to hit
Firecrawl's real per-account rate limit — at which point
firecrawl_client.py's existing 429 backoff naturally throttles it back
down. So it self-corrects within a request or two, just not instantly.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

from apify import Actor

from .config import ScraperSettings

REGISTRY_STORE_NAME = "scraper-registry"
REGISTRY_KEY = "active_runs"
STALE_AFTER_SECONDS = 180   # no heartbeat in 3 min => treated as dead/crashed
HEARTBEAT_SECONDS = 45
_LOCAL_RUN_ID = f"local-{uuid.uuid4().hex[:8]}"  # stable for this process's lifetime


class RunRegistry:
    def __init__(self) -> None:
        self._store = None
        self.run_id = os.getenv("APIFY_ACTOR_RUN_ID") or _LOCAL_RUN_ID
        self._heartbeat_task: asyncio.Task | None = None
        self._joined = False

    async def _get_store(self):
        if self._store is None:
            self._store = await Actor.open_key_value_store(name=REGISTRY_STORE_NAME)
        return self._store

    async def _read_active(self, store) -> dict:
        """Returns {run_id: last_heartbeat_ts} for runs still within the stale window."""
        runs = await store.get_value(REGISTRY_KEY) or {}
        now = time.time()
        return {rid: ts for rid, ts in runs.items() if now - ts <= STALE_AFTER_SECONDS}

    async def join(self) -> None:
        """
        Register this run as active. Call once at startup, before doing any
        work. Blocks (polling) while `ScraperSettings.CONCURRENT_MAX_USERS`
        other runs are already active — point 1 & 4.
        """
        store = await self._get_store()
        announced_wait = False

        while True:
            runs = await self._read_active(store)
            if self.run_id in runs or len(runs) < ScraperSettings.CONCURRENT_MAX_USERS:
                break
            if not announced_wait:
                Actor.log.info(
                    f"⏸️ {len(runs)} run(s) already active "
                    f"(limit: {ScraperSettings.CONCURRENT_MAX_USERS}) — "
                    f"run '{self.run_id}' is queued and will start as soon as "
                    f"a slot frees up…"
                )
                announced_wait = True
            await asyncio.sleep(ScraperSettings.USER_QUEUE_POLL_SECONDS)

        runs[self.run_id] = time.time()
        await store.set_value(REGISTRY_KEY, runs)
        self._joined = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat(store))
        if announced_wait:
            Actor.log.info(f"▶️ Slot freed — run '{self.run_id}' starting now")
        Actor.log.info(f"🙋 Registered run '{self.run_id}' — {len(runs)} run(s) active total")

    async def _heartbeat(self, store) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                runs = await self._read_active(store)
                runs[self.run_id] = time.time()
                await store.set_value(REGISTRY_KEY, runs)
        except asyncio.CancelledError:
            pass

    async def active_run_count(self) -> int:
        """How many runs (including this one) are currently alive. Always >= 1."""
        store = await self._get_store()
        runs = await self._read_active(store)
        return max(1, len(runs))

    async def leave(self) -> None:
        """Unregister this run. Always call in a `finally` block."""
        if not self._joined:
            return
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        store = await self._get_store()
        runs = await self._read_active(store)
        runs.pop(self.run_id, None)
        await store.set_value(REGISTRY_KEY, runs)
        Actor.log.info(f"👋 Unregistered run '{self.run_id}' — {len(runs)} run(s) still active")


# Module-level singleton, same usage pattern as the old `scraper_lock`
run_registry = RunRegistry()