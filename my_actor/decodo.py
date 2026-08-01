"""
my_actor/decodo.py
Decodo Web Scraping API client with multi-account rotation.

Unlike BrightData's Web Unlocker (a proxy you route requests through),
Decodo's Scraper API is a plain HTTPS endpoint: you POST the target URL
(plus options) to https://scraper-api.decodo.com/v2/scrape with HTTP Basic
auth, and it returns JSON containing the rendered page in
`results[0]["content"]`.

One aiohttp.ClientSession is shared across all workers.
On failure or rate-limit (429/402/403 — from Decodo itself, or reflected
in the per-result status_code), the next account is tried automatically.
When all accounts are exhausted, a warning is printed and an exception is
raised.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

import aiohttp
from apify import Actor

from .config import DECODO_ACCOUNTS, API_URL, ScraperSettings


class AccountRotator:
    """
    Round-robin Decodo account rotator with per-account failure tracking.

    All workers share one instance. On each fetch():
      1. Pick the next non-failed account.
      2. POST to Decodo's Scraper API using that account's Authorization header.
      3. On 429/402/403 (request-level or result-level) → mark account failed, try next.
      4. On success → clear failure flag for that account.
      5. If all accounts fail → reset failure set, log a warning, raise.
    """

    def __init__(self) -> None:
        self._accounts = list(DECODO_ACCOUNTS)
        self._idx = 0
        self._lock = asyncio.Lock()
        self._failed: set[int] = set()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _request_headers(self, account: dict) -> dict:
        return {
            "Accept":        "application/json",
            "Content-Type":  "application/json",
            "Authorization": account["authorization"],
        }

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

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch(
        self,
        url: str,
        session: aiohttp.ClientSession,
        headers: Optional[dict] = None,
        headless: str = "html",
    ) -> str:
        """
        Fetch `url` through Decodo's Scraper API, rotating accounts on failure.

        `headers`, if given, are forwarded to the *target* site as
        Decodo's `headers` scrape option (NOT used for Decodo's own auth).
        `headless="html"` renders JS and returns the final HTML — matches
        what the old proxy-based fetch effectively gave the parsers.

        Tries every available account once before raising.
        """
        tried: set[int] = set()
        last_exc: Exception = RuntimeError("All Decodo accounts failed")

        payload: dict = {"url": url, "headless": headless}
        if headers:
            payload["headers"] = headers

        while len(tried) < len(self._accounts):
            idx = await self._pick()
            if idx in tried:
                break
            tried.add(idx)

            account = self._accounts[idx]
            label = f"Account {idx + 1} ({account['name']})"

            try:
                async with session.post(
                    API_URL,
                    json=payload,
                    headers=self._request_headers(account),
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

                    if resp.status in (402, 403):
                        Actor.log.warning(
                            f"🛑 {label} returned {resp.status} — "
                            "plan may be FINISHED. Please renew your Decodo "
                            f"subscription for: {account['name']}"
                        )
                        async with self._lock:
                            self._failed.add(idx)
                        continue

                    resp.raise_for_status()
                    body = await resp.json()

                    results = body.get("results") or []
                    if not results:
                        raise RuntimeError(f"Decodo returned no results for {url}: {body}")

                    result = results[0]
                    target_status = result.get("status_code")

                    if target_status == 429:
                        Actor.log.warning(
                            f"⚠️ {label} target site rate-limited (429) — trying next account."
                        )
                        async with self._lock:
                            self._failed.add(idx)
                        continue

                    if target_status in (402, 403):
                        Actor.log.warning(
                            f"🛑 {label} target returned {target_status} — trying next account."
                        )
                        async with self._lock:
                            self._failed.add(idx)
                        continue

                    content = result.get("content", "")
                    async with self._lock:
                        self._failed.discard(idx)
                    return content

            except aiohttp.ClientResponseError as e:
                Actor.log.warning(f"⚠️ {label} HTTP {e.status} on {url}: {e.message}")
                last_exc = e
            except (json.JSONDecodeError, aiohttp.ContentTypeError) as e:
                Actor.log.warning(f"⚠️ {label} bad JSON response on {url}: {e}")
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