"""
my_actor/brightdata.py
BrightData Web Unlocker HTTP client with multi-account rotation.

One aiohttp.ClientSession is shared across all workers.
On failure or rate-limit (429/402/403), the next account is tried automatically.
When all accounts are exhausted, a warning is printed and an exception is raised.
"""
from __future__ import annotations

import asyncio
import ssl
from typing import Optional

import aiohttp
from apify import Actor

from .config import BRIGHTDATA_ACCOUNTS, ScraperSettings

# Shared SSL context — disables cert verification (required for BrightData proxy)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


class AccountRotator:
    """
    Round-robin BrightData account rotator with per-account failure tracking.

    All workers share one instance. On each fetch():
      1. Pick the next non-failed account.
      2. Make the request through that account's proxy.
      3. On 429/402/403 → mark account failed, try the next.
      4. On success → clear failure flag for that account.
      5. If all accounts fail → reset failure set, log a warning, raise.
    """

    def __init__(self) -> None:
        self._accounts = list(BRIGHTDATA_ACCOUNTS)
        self._idx = 0
        self._lock = asyncio.Lock()
        self._failed: set[int] = set()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _proxy_url(self, account: dict) -> str:
        return f"https://{account['host']}:{account['port']}"

    def _proxy_auth(self, account: dict) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(account["username"], account["password"])

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
    ) -> str:
        """
        Fetch `url` through BrightData Web Unlocker, rotating accounts on failure.

        Tries every available account once before raising.
        """
        tried: set[int] = set()
        last_exc: Exception = RuntimeError("All BrightData accounts failed")

        while len(tried) < len(self._accounts):
            idx = await self._pick()
            if idx in tried:
                break
            tried.add(idx)

            account = self._accounts[idx]
            label = f"Account {idx + 1} ({account['username'][:35]}…)"

            try:
                async with session.get(
                    url,
                    proxy=self._proxy_url(account),
                    proxy_auth=self._proxy_auth(account),
                    headers=headers or {},
                    timeout=aiohttp.ClientTimeout(total=ScraperSettings.REQUEST_TIMEOUT),
                    ssl=_SSL_CTX,
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
                            "plan may be FINISHED. Please renew your BrightData "
                            f"subscription for: {account['username']}"
                        )
                        async with self._lock:
                            self._failed.add(idx)
                        continue

                    resp.raise_for_status()
                    async with self._lock:
                        self._failed.discard(idx)
                    return await resp.text()

            except aiohttp.ClientResponseError as e:
                Actor.log.warning(f"⚠️ {label} HTTP {e.status} on {url}: {e.message}")
                last_exc = e
            except aiohttp.ClientProxyConnectionError as e:
                Actor.log.warning(f"⚠️ {label} proxy connection error: {e}")
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
