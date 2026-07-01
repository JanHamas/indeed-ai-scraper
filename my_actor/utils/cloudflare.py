"""
my_actor/utils/cloudflare.py
Cloudflare Turnstile bypasser — Apify Actor version.
"""
from __future__ import annotations

import asyncio
import json
import os

from apify import Actor
from playwright.async_api import Page
from twocaptcha import TwoCaptcha


class CloudflareBypasser:
    def __init__(self, page: Page):
        self.page     = page
        self.api_key  = os.getenv("2CAPTCHA_API_KEY")
        self.captured_params: dict | None = None
        self._console_listener = None

    # ── Public entry point ────────────────────────────────────────────────────
    async def detect_and_bypass(self) -> bool:
        """
        Returns True  → challenge was present and successfully bypassed.
        Returns False → no challenge detected (fast path) OR bypass failed.
        """
        if not await self.page.locator("text='Additional Verification Required'").is_visible(timeout=0):
            return False  # fast path — no challenge present

        Actor.log.info("[CF] Cloudflare challenge detected — attempting bypass")

        params = await self._intercept_captcha_params()
        if not params:
            Actor.log.error("[CF] Could not intercept Turnstile params — bypass failed")
            return False

        token = await self._solve_async(params)
        if not token:
            Actor.log.error("[CF] 2Captcha returned no token — bypass failed")
            return False

        await self._submit_token(token)
        await asyncio.sleep(5)
        Actor.log.info("[CF] Cloudflare bypass succeeded ✅")
        return True

    # ── Step 1: intercept Turnstile render() call via console ─────────────────
    async def _intercept_captcha_params(self) -> dict | None:
        self.captured_params = None

        intercept_script = """
        console.clear = () => console.log("Console was cleared");
        let resolved = false;
        const intervalID = setInterval(() => {
            if (window.turnstile && !resolved) {
                clearInterval(intervalID);
                resolved = true;
                window.turnstile.render = (a, b) => {
                    const params = {
                        sitekey:   b.sitekey,
                        pageurl:   window.location.href,
                        data:      b.cData,
                        pagedata:  b.chlPageData,
                        action:    b.action,
                        userAgent: navigator.userAgent
                    };
                    console.log('intercepted-params:' + JSON.stringify(params));
                    window.cfCallback = b.callback;
                };
            }
        }, 50);
        """

        def _on_console(msg):
            if "intercepted-params:" in msg.text:
                try:
                    json_str = msg.text.split("intercepted-params:", 1)[1].strip()
                    self.captured_params = json.loads(json_str)
                except Exception as e:
                    Actor.log.warning(f"[CF] JSON parse error on console message: {e}")

        self._console_listener = _on_console
        self.page.on("console", self._console_listener)

        for attempt in range(1, 6):
            if self.captured_params:
                break
            Actor.log.info(f"[CF] Waiting for Turnstile params — attempt {attempt}/5")
            await self.page.reload()
            await self.page.evaluate(intercept_script)
            await asyncio.sleep(3)

        self.page.remove_listener("console", self._console_listener)
        self._console_listener = None
        return self.captured_params

    # ── Step 2: solve via 2Captcha (off-thread so event loop stays free) ─────
    async def _solve_async(self, params: dict) -> str | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._solve_sync, params)

    def _solve_sync(self, params: dict) -> str | None:
        if not self.api_key:
            Actor.log.error("[CF] 2CAPTCHA_API_KEY not set — cannot solve captcha")
            return None
        try:
            solver = TwoCaptcha(self.api_key)
            result = solver.turnstile(
                sitekey  = params["sitekey"],
                url      = params["pageurl"],
                action   = params.get("action"),
                data     = params.get("data"),
                pagedata = params.get("pagedata"),
                useragent= params.get("userAgent"),
            )
            return result.get("code")
        except Exception as e:
            Actor.log.error(f"[CF] 2Captcha error: {str(e).split('—')[-1].strip()}")
            return None

    # ── Step 3: inject token back into the page ───────────────────────────────
    async def _submit_token(self, token: str) -> None:
        await self.page.evaluate(f"""() => {{
            if (typeof window.cfCallback === 'function') {{
                window.cfCallback("{token}");
            }}
        }}""")