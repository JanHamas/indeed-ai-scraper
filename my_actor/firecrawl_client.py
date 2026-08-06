"""
my_actor/firecrawl_client.py
Firecrawl API client — replaces evomi_client.py.

How it works, in plain terms:
- Each Firecrawl account can do 10 requests per minute, and all 10 are
  allowed to fire at once (no need to space them out).
- You can have several accounts (see ScraperSettings.accounts). Requests
  are spread across all of them round-robin, so total speed = 10 x
  (number of accounts that still have credit).
- Add a 5th account to config.py and the scraper automatically gets
  faster next run — nothing else to change (point 4).
- If an account runs out of credits (Firecrawl returns HTTP 402), that
  account is taken out of rotation and its name is printed once so you
  know which one to top up or delete (point 2).
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

import aiohttp
from apify import Actor

from .config import ScraperSettings

FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
REQUESTS_PER_MINUTE_PER_ACCOUNT = 10
WINDOW_SECONDS = 60


class _AccountLimiter:
    """Tracks recent request times for ONE account (rolling 60s window)."""

    def __init__(self, name: str, api_key: str) -> None:
        self.name = name
        self.api_key = api_key
        self.exhausted = False
        self._recent: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def wait_for_slot(self) -> None:
        """Blocks only if this account already used its 10 requests in the last 60s."""
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._recent and now - self._recent[0] >= WINDOW_SECONDS:
                    self._recent.popleft()
                if len(self._recent) < REQUESTS_PER_MINUTE_PER_ACCOUNT:
                    self._recent.append(now)
                    return
                await asyncio.sleep(WINDOW_SECONDS - (now - self._recent[0]) + 0.1)


class FirecrawlClient:
    """One shared instance, used by every worker (same pattern as the old `evomi`)."""

    def __init__(self) -> None:
        self._accounts: list[_AccountLimiter] = [
            _AccountLimiter(name, key) for name, key in ScraperSettings.accounts.items()
        ]
        self._next = 0
        self._pick_lock = asyncio.Lock()

    @property
    def active_accounts(self) -> list[_AccountLimiter]:
        return [a for a in self._accounts if not a.exhausted]

    @property
    def max_concurrency(self) -> int:
        """Dynamic concurrency = 10 x number of accounts that still have credit (point 4)."""
        active = len(self.active_accounts) or 1
        return active * REQUESTS_PER_MINUTE_PER_ACCOUNT

    async def _next_account(self) -> _AccountLimiter:
        async with self._pick_lock:
            active = self.active_accounts
            if not active:
                raise RuntimeError(
                    "🚫 All Firecrawl accounts are out of credits. Add a new account "
                    "to config.py or top up credits, then restart the scraper."
                )
            self._next %= len(active)
            account = active[self._next]
            self._next += 1
            return account

    async def fetch(self, url: str, session: aiohttp.ClientSession) -> str:
        """Fetch `url` HTML through Firecrawl using whichever account has a free slot."""
        attempt = 0
        while True:
            account = await self._next_account()
            await account.wait_for_slot()

            headers = {
                "Authorization": f"Bearer {account.api_key}",
                "Content-Type": "application/json",
            }
            payload = {"url": url, "formats": ["rawHtml"]}

            try:
                async with session.post(
                    FIRECRAWL_ENDPOINT,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=ScraperSettings.REQUEST_TIMEOUT),
                ) as resp:

                    if resp.status == 402:
                        account.exhausted = True
                        Actor.log.info(
                            f"💳 Account '{account.name}' is out of credits — removed from "
                            f"rotation. ({len(self.active_accounts)} account(s) still active)"
                        )
                        continue  # try again with a different account

                    if resp.status == 429:
                        # Actor.log.info(f"⏳ '{account.name}' hit its rate limit, waiting a bit…")
                        await asyncio.sleep(5)
                        continue

                    if resp.status >= 400:
                        text = await resp.text()
                        raise RuntimeError(f"Firecrawl HTTP {resp.status}: {text[:300]}")

                    body = await resp.json()
                    html = (body.get("data") or {}).get("rawHtml", "")
                    if not html:
                        raise RuntimeError("Firecrawl returned no HTML")
                    return html

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                attempt += 1
                if attempt >= ScraperSettings.MAX_RETRIES:
                    raise
                Actor.log.warning(
                    f"⚠️ Fetch attempt {attempt}/{ScraperSettings.MAX_RETRIES} "
                    f"failed for {url}: {e} — retrying"
                )
                await asyncio.sleep(ScraperSettings.RETRY_DELAY_MIN)


# Module-level singleton shared by all workers — same usage pattern as `evomi` before
firecrawl = FirecrawlClient()