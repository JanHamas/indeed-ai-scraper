"""
my_actor/brightdata.py
Bright Data Web Unlocker client with multi-account (multi-zone) rotation —
native/proxy access edition.

Bright Data's native proxy access works like a normal authenticated HTTP
proxy: you point aiohttp's `proxy=` at Bright Data's super-proxy
(`brd.superproxy.io:44445`), authenticate with your zone's
username/password via Basic Auth, and request the target `url` exactly
like a direct request — Bright Data's infrastructure handles unblocking,
JS rendering, and CAPTCHA solving transparently and hands back the
target site's real response (status code, headers, body) as if you'd
hit it directly. There's no JSON envelope to unwrap.

Because Web Unlocker terminates TLS itself to inspect/unblock traffic,
the certificate the "target" presents through the tunnel is Bright
Data's own — so certificate verification is disabled for these
requests (this is Bright Data's documented behavior, not a bug).

One aiohttp.ClientSession is shared across all workers.
On failure or rate-limit/auth error (429/407/402/401/403), the next
account is tried automatically. When all accounts are exhausted, a
warning is printed and an exception is raised.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp
from apify import Actor

from .config import BRIGHTDATA_ACCOUNTS, ScraperSettings


class AccountRotator:
    """
    Round-robin Bright Data account (zone) rotator with per-account failure tracking.

    All workers share one instance. On each fetch():
      1. Pick the next non-failed account.
      2. GET `url` through Bright Data's super-proxy, authenticated with
         that account's username/password.
      3. On 429/407/402/401/403 → mark account failed, try next.
      4. On success → clear failure flag for that account.
      5. If all accounts fail → reset failure set, log a warning, raise.
    """

    def __init__(self) -> None:
        self._accounts = list(BRIGHTDATA_ACCOUNTS)
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
        Fetch `url` through Bright Data's Web Unlocker proxy, rotating
        accounts (zones) on failure.

        `headers`, if given, are sent as real HTTP request headers on the
        proxied request — Bright Data forwards them to the target site
        as-is, no special flag needed (unlike Scrape.do's
        `customHeaders=true`).

        `render` is kept for call-site compatibility with the previous
        client, but is a no-op here: Web Unlocker automatically detects
        when a page needs JavaScript rendering and handles it per the
        zone's configuration.

        Tries every available account once before raising.
        """
        tried: set[int] = set()
        last_exc: Exception = RuntimeError("All Bright Data accounts failed")

        while len(tried) < len(self._accounts):
            idx = await self._pick()
            if idx in tried:
                break
            tried.add(idx)

            account = self._accounts[idx]
            label = f"Account {idx + 1} ({account['name']})"

            proxy_url = f"http://{account['host']}:{account['port']}"
            proxy_auth = aiohttp.BasicAuth(account["username"], account["password"])

            try:
                async with session.get(
                    url,
                    proxy=proxy_url,
                    proxy_auth=proxy_auth,
                    headers=headers or None,
                    ssl=False,  # Web Unlocker terminates TLS itself — see module docstring
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

                    if resp.status in (401, 402, 403, 407):
                        Actor.log.warning(
                            f"🛑 {label} returned {resp.status} — "
                            "username/password may be invalid or plan may be FINISHED. "
                            f"Please check your Bright Data subscription for: {account['name']}"
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