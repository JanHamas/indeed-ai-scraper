"""
my_actor/scrapedo.py
Scrape.do Web Scraping API client with multi-account rotation.

Scrape.do's API is a plain HTTPS GET endpoint: you call
https://api.scrape.do/ with `token` and `url` (url-encoded) as query
params, and it returns the target page's raw HTML/content directly in
the response body — no JSON envelope to unwrap (unlike Decodo).

One aiohttp.ClientSession is shared across all workers.
On failure or rate-limit (429/402/401/403), the next account is tried
automatically. When all accounts are exhausted, a warning is printed
and an exception is raised.
"""
from __future__ import annotations

import asyncio
import urllib.parse
from typing import Optional

import aiohttp
from apify import Actor

from .config import SCRAPEDO_ACCOUNTS, API_URL, ScraperSettings


class AccountRotator:
    """
    Round-robin Scrape.do account rotator with per-account failure tracking.

    All workers share one instance. On each fetch():
      1. Pick the next non-failed account.
      2. GET Scrape.do's endpoint with that account's token.
      3. On 429/402/401/403 → mark account failed, try next.
      4. On success → clear failure flag for that account.
      5. If all accounts fail → reset failure set, log a warning, raise.
    """

    def __init__(self) -> None:
        self._accounts = list(SCRAPEDO_ACCOUNTS)
        self._idx = 0
        self._lock = asyncio.Lock()
        self._failed: set[int] = set()

    # ── Internal helpers ────────────────────────────────────────────────────

    async def _pick(self) -> int:
        """Return the index of the next available account (round-robin)."""
        async with self._lock:
            available = [i for i in range(len(self._accounts)) if i not in self._failed]
            if not available:
                # All accounts failed — reset and start over
                self._failed.clear()
                available = list(range(len(self._accounts)))
            idx = available[self._idx % len(available)]
            self._idx = (self._idx + 1) % len(available)
            return idx

    # ── Public API ──────────────────────────────────────────────────────────

    async def fetch(
        self,
        url: str,
        session: aiohttp.ClientSession,
        headers: Optional[dict] = None,
        render: bool = False,
    ) -> str:
        """
        Fetch `url` through Scrape.do, rotating accounts on failure.

        `headers`, if given, are forwarded to the *target* site as real
        HTTP request headers, with `customHeaders=true` set so Scrape.do
        passes them through untouched.
        `render=True` spins up Scrape.do's headless browser to execute
        JS — matches what `headless="html"` gave you on Decodo.

        Tries every available account once before raising.
        """
        tried: set[int] = set()
        last_exc: Exception = RuntimeError("All Scrape.do accounts failed")

        while len(tried) < len(self._accounts):
            idx = await self._pick()
            if idx in tried:
                break
            tried.add(idx)

            account = self._accounts[idx]
            label = f"Account {idx + 1} ({account['name']})"

            params = {
                "token": account["token"],
                "url": url,
                "render": str(render).lower(),
            }
            if headers:
                params["customHeaders"] = "true"

            try:
                async with session.get(
                    API_URL,
                    params=params,
                    headers=headers or None,
                    timeout=aiohttp.ClientTimeout(total=ScraperSettings.REQUEST_TIMEOUT),
                ) as resp:

                    if resp.status == 429:
                        Actor.log.warning(
                            f"⚠️ {label} hit rate limit (429) — trying next account. "
                            "If this keeps happening, buy more quota or add more accounts."
                        )
                        async with self._lock:
                            self._failed.add(idx)
                        continue

                    if resp.status in (401, 402, 403):
                        Actor.log.warning(
                            f"🛑 {label} returned {resp.status} — "
                            "token may be invalid or plan may be FINISHED. "
                            f"Please check your Scrape.do subscription for: {account['name']}"
                        )
                        async with self._lock:
                            self._failed.add(idx)
                        continue

                    resp.raise_for_status()
                    content = await resp.text()

                    async with self._lock:
                        self._failed.discard(idx)
                    return content

            except aiohttp.ClientResponseError as e:
                Actor.log.warning(f"⚠️ {label} HTTP {e.status} on {url}: {e.message}")
                last_exc = e
            except asyncio.TimeoutError:
                Actor.log.warning(f"⚠️ {label} timed out on {url}")
                last_exc = asyncio.TimeoutError(f"Timeout on {url}")
            except Exception as e:
                Actor.log.warning(f"⚠️ {label} unexpected error on {url}: {e}")
                last_exc = e

        raise last_exc


# ── Module-level singleton shared by all workers ───────────────────────────────
rotator = AccountRotator()