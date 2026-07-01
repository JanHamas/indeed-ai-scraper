"""
src/helpers.py
All dataclasses, config builder, and utility functions.
"""
from __future__ import annotations

import asyncio
import os
import random
import re, requests
from dataclasses import dataclass, field
from datetime import datetime, timezone 
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, quote_plus
from dotenv import load_dotenv

from apify import Actor
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import ScraperSettings
load_dotenv()
from playwright.async_api import Browser, Page
import json
from pathlib import Path
from . import fingerprint as fg_generator

# ─────────────────────────────────────────────────────────────────────────────
# Indeed search URL builder
# ─────────────────────────────────────────────────────────────────────────────

def build_indeed_search_urls(
    keywords:    list[str],
    location:    str,
    country:     str,
) -> list[str]:
    """
    Build Indeed search URLs from keywords + location + country.
    One URL per keyword line.
    Country must be a key in ScraperSettings.indeed_country_domains.
    """
    base_domain = ScraperSettings.indeed_country_domains.get(
        country.lower().strip(), ScraperSettings.indeed_country_domains["us"]
    )
    urls = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        params = {"q": kw}
        if location and location.strip():
            params["l"] = location.strip()
        # max_results caps pagination — enqueue only up to this many results
        # We pass it through metadata on the URL itself via a custom fragment
        base = f"{base_domain}/jobs?{urlencode(params)}"
        urls.append(base)
    Actor.log.info(
        f"🔧 Built {len(urls)} search URL(s) for {len(keywords)} keyword(s) "
        f"| country={country} | location='{location}'"
    )
    return urls


# ─────────────────────────────────────────────────────────────────────────────
# User provded proxies loader
# ─────────────────────────────────────────────────────────────────────────────
from urllib.parse import urlparse  # add if not already imported

def _parse_proxy_entry(raw: str) -> list[str] | None:
    """Normalize any supported proxy format → [host, port, user, pwd] (user/pwd may be '')."""
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "socks5://")):
        try:
            p = urlparse(raw)
            if not p.hostname or not p.port:
                return None
            return [p.hostname, str(p.port), p.username or "", p.password or ""]
        except Exception:
            return None
    parts = raw.split(":")
    if len(parts) == 4:
        return parts
    if len(parts) == 2:
        return [parts[0], parts[1], "", ""]
    return None


def is_proxy_alive(ip: str, port: str, user: str = "", pwd: str = "", timeout: int = 8) -> bool:
    """Quick connectivity check — run off the event loop via asyncio.to_thread."""
    try:
        proxy_url = f"http://{user}:{pwd}@{ip}:{port}" if user else f"http://{ip}:{port}"
        r = requests.get("https://api.ipify.org", proxies={"https": proxy_url}, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False
    
def load_proxies_from_text(raw_text: str) -> list[str]:
    """
    Parse a newline-separated proxy list.
    Supports: http://user:pass@host:port  |  host:port:user:pass  |  host:port
    """
    proxies = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("http://", "https://", "socks5://")):
            proxies.append(line)
            continue
        parts = line.split(":")
        if len(parts) == 4:
            host, port, user, password = parts
            proxies.append(f"http://{user}:{password}@{host}:{port}")
        elif len(parts) == 2:
            proxies.append(f"http://{line}")
        else:
            Actor.log.warning(f"⚠️ Unrecognised proxy format, skipping: {line}")
    Actor.log.info(f"📋 Loaded {len(proxies)} proxies from input")
    return proxies
# ─────────────────────────────────────────────────────────────────────────────
# Indeed job-ID extractor (for "already processed" URL list)
# ─────────────────────────────────────────────────────────────────────────────

def extract_job_ids_from_urls(url_list: list[str]) -> set[str]:
    """Extract jk= param from Indeed job-detail URLs."""
    ids: set[str] = set()
    for url in url_list:
        url = url.strip()
        if not url:
            continue
        try:
            params = parse_qs(urlparse(url).query)
            jk = params.get("jk", [""])[0]
            if jk:
                ids.add(jk)
        except Exception:
            pass
    if ids:
        Actor.log.info(f"📂 Extracted {len(ids)} job IDs from processed_job_urls input")
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# SemanticMatcher (optional — only loaded when about_me is provided)
# ─────────────────────────────────────────────────────────────────────────────

if os.getenv("APP_ENV") != "local":
    import torch
    from sentence_transformers import SentenceTransformer
    from sentence_transformers.util import batch_to_device, cos_sim

    _UNIVERSAL_ABBR: Dict[str, str] = {
        r'\bsr\.?\b':     'senior',
        r'\bjr\.?\b':     'junior',
        r'\bmgr\.?\b':    'manager',
        r'\bdir\.?\b':    'director',
        r'\bassoc\.?\b':  'associate',
        r'\bspec\.?\b':   'specialist',
        r'\bcoord\.?\b':  'coordinator',
        r'\beng\.?\b':    'engineer',
        r'\bdev\.?\b':    'developer',
        r'\barch\.?\b':   'architect',
        r'\brep\.?\b':    'representative',
        r'\bvp\b':        'vice president',
        r'\bsvp\b':       'senior vice president',
        r'\bcto\b':       'chief technology officer',
        r'\bceo\b':       'chief executive officer',
        r'\bcoo\b':       'chief operating officer',
        r'\bcfo\b':       'chief financial officer',
    }

    _NOISE_RE = re.compile(
        r'\(.*?\)|\[.*?\]'
        r'|\b(remote|hybrid|onsite|on-site|contract|part[\s\-]?time'
        r'|full[\s\-]?time|us only|usa only|w2|c2c|1099'
        r'|urgent|immediate|opening|opportunity|position|role'
        r'|new grad|entry.level|experienced)\b',
        re.IGNORECASE,
    )
    _SEP_RE        = re.compile(r'[-–—|·•/\\]')
    _WHITESPACE_RE = re.compile(r'\s+')

    def _extract_abbr_from_user_text(about_me: str) -> Dict[str, str]:
        abbr_map: Dict[str, str] = {}
        paren_re = re.compile(r'\b([A-Za-z][A-Za-z0-9\-\.]{0,10})\s*\(([^)]{2,60})\)')
        for m in paren_re.finditer(about_me):
            left, right = m.group(1).strip(), m.group(2).strip()
            ls = sum(1 for c in left  if c.isupper()) / max(len(left),  1)
            rs = sum(1 for c in right if c.isupper()) / max(len(right), 1)
            if len(left) <= 8 and ls >= rs:
                abbr, expansion = left, right
            elif len(right) <= 8 and rs > ls:
                abbr, expansion = right, left
            else:
                continue
            if re.match(r'^[A-Z][A-Za-z0-9\-]{1,7}$', abbr):
                abbr_map[r'\b' + re.escape(abbr) + r'\b'] = expansion.lower()
        camel_split = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
        for token in re.findall(r'\b[A-Z][a-zA-Z]{2,15}\b', about_me):
            parts = camel_split.split(token)
            spaced = ' '.join(parts).lower()
            if spaced != token.lower() and len(parts) > 1:
                abbr_map[r'\b' + re.escape(token) + r'\b'] = spaced
        symbol_re = re.compile(
            r'\b([A-Za-z][A-Za-z0-9]*(?:[#\+\&][A-Za-z0-9]*)+)\b|(\.[A-Z][A-Za-z0-9]+)'
        )
        for m in symbol_re.finditer(about_me):
            symbol = (m.group(1) or m.group(2)).strip()
            if not symbol or len(symbol) > 12:
                continue
            normalized = re.sub(r'[#\+\.\&]', '', symbol).lower()
            if normalized and normalized != symbol.lower():
                abbr_map[re.escape(symbol)] = normalized
        return abbr_map

    def _compile_patterns(abbr_map: Dict[str, str]) -> List[Tuple[re.Pattern, str]]:
        compiled = []
        for pattern, replacement in abbr_map.items():
            try:
                compiled.append((re.compile(pattern, re.IGNORECASE), replacement))
            except re.error:
                pass
        return compiled

    class SemanticMatcher:
        MODEL_NAME = "TechWolf/JobBERT-v2"

        def __init__(self, model_name: str = MODEL_NAME):
            self._model          = SentenceTransformer(model_name)
            self._lock           = asyncio.Lock()
            self._cached_keywords: Optional[str]             = None
            self._query_embeddings                           = None
            self._compiled_abbr: List[Tuple[re.Pattern, str]] = []

        def _encode(self, texts: List[str]):
            features = self._model.tokenize(texts)
            features = batch_to_device(features, self._model.device)
            features["text_keys"] = ["anchor"]
            with torch.no_grad():
                out = self._model.forward(features)
            emb = out["sentence_embedding"]
            return torch.nn.functional.normalize(emb, p=2, dim=1)

        def _refresh_if_needed(self, user_keywords: str) -> None:
            if user_keywords == self._cached_keywords:
                return
            abbr_map            = _extract_abbr_from_user_text(user_keywords)
            self._compiled_abbr = _compile_patterns(abbr_map)
            tokens = [t.strip() for t in re.split(r'[\n\r,|/•·]+', user_keywords) if t.strip() and len(t.strip()) > 1]
            if not tokens:
                tokens = [user_keywords.strip()]
            seen, unique_tokens = set(), []
            for t in tokens:
                k = t.lower()
                if k not in seen:
                    seen.add(k)
                    unique_tokens.append(t)
            self._query_embeddings = self._encode([f"Job title: {t}" for t in unique_tokens])
            self._cached_keywords  = user_keywords

        def _clean_title(self, title: str) -> str:
            t = title.lower()
            for pattern, replacement in _UNIVERSAL_ABBR.items():
                t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)
            for compiled_re, replacement in self._compiled_abbr:
                t = compiled_re.sub(replacement, t)
            t = _NOISE_RE.sub(' ', t)
            t = _SEP_RE.sub(' ', t)
            return _WHITESPACE_RE.sub(' ', t).strip()

        def _score(self, user_keywords: str, job_titles: List[str]) -> List[float]:
            self._refresh_if_needed(user_keywords)
            title_embeddings = self._encode([self._clean_title(t) for t in job_titles])
            best_scores      = cos_sim(self._query_embeddings, title_embeddings).max(dim=0).values.cpu().numpy()
            return [round(float(max(0.0, s) * 100), 2) for s in best_scores]

        async def match(self, user_keywords: str, job_titles: List[str]) -> List[float]:
            if not user_keywords or not job_titles:
                return []
            async with self._lock:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, self._score, user_keywords, job_titles)

    _matcher = SemanticMatcher()


async def get_match_percentages(about_me: str, job_titles: List[str]) -> List[float]:
    if not about_me or not about_me.strip():
        return [100.0] * len(job_titles)
    if os.getenv("APP_ENV") == "local":
        return [random.randint(0, 100) for _ in range(len(job_titles))]
    return await _matcher.match(about_me, job_titles)


# ─────────────────────────────────────────────────────────────────────────────
# ScraperConfig
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ScraperConfig:
    # Core
    about_me:             str
    ignore_companies:     List[str]
    ignore_related:       List[str]
    concurrency:          int
    url_queue:            List[str]
    max_jobs:             int
    per_company_jobs:     int
    min_match_percentage: int

    # Search builder settings (used when start_urls is empty)
    search_keywords:     List[str] = field(default_factory=list)
    search_location:     str       = ""
    search_country:      str       = "us"

    # Feature flags
    scrape_company_details:   bool = False  # fetch company profile page
    save_unique_only:         bool = True   # deduplicate by (position, company)
    follow_apply_redirect:    bool = False  # resolve external apply link
    skip_expired_jobs:        bool = False
    skip_ignore_related_jobs: bool = False

    # Google Sheets
    google_sheet_url: str = ""
    sheet_name:       str = "Indeed Jobs"

    # Browser
    headless:      bool           = True
    proxies_path:  Optional[Path] = None
    proxies_state: Optional[Path] = None
    headless:      bool           = True
    proxy_config:  dict           = field(default_factory=dict)
    proxies:       List[str]      = field(default_factory=list)  

    # Auth
    account_cookies: List[dict]         = field(default_factory=list)

    # Runtime state
    processed_uids: Set[str] = field(default_factory=set)

    # Internal tracking
    ignored_companies_seen:    Set[str]   = field(default_factory=set)
    new_processed_company_jobs: List[str] = field(default_factory=list)
    extracted_jobs_counter:    int        = field(default=0, init=False)
    _saved_jobs:               List[dict] = field(default_factory=list, init=False)

    # Unique-job fingerprints: set of "position|||company" strings
    _seen_fingerprints: Set[str] = field(default_factory=set, init=False)

    # Locks
    _lock:            asyncio.Lock = field(default=None, init=False, repr=False)
    _context_lock:    asyncio.Lock = field(default=None, init=False, repr=False)
    _uid_buffer_lock: asyncio.Lock = field(default=None, init=False, repr=False)

    _context_requests: Dict[int, int] = field(default_factory=dict, init=False)
    _uid_buffer:       List[str]      = field(default_factory=list, init=False)
    _saved_account_ids: Set[int]      = field(default_factory=set, init=False)

    @property
    def ai_matching_enabled(self) -> bool:
        return bool(self.about_me and self.about_me.strip())

    @property
    def tracking_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def context_lock(self) -> asyncio.Lock:
        if self._context_lock is None:
            self._context_lock = asyncio.Lock()
        return self._context_lock

    @property
    def uid_lock(self) -> asyncio.Lock:
        if self._uid_buffer_lock is None:
            self._uid_buffer_lock = asyncio.Lock()
        return self._uid_buffer_lock

    async def buffer_uids(self, uids: list) -> None:
        async with self.uid_lock:
            self._uid_buffer.extend(uids)

    async def flush_uid_buffer(self) -> list:
        async with self.uid_lock:
            uids = self._uid_buffer.copy()
            self._uid_buffer.clear()
            return uids

    async def try_add_job(self, uid: str, company_name: str) -> bool:
        async with self.tracking_lock:
            if self.extracted_jobs_counter >= self.max_jobs:
                return False
            if uid in self.processed_uids:
                return False
            if self.new_processed_company_jobs.count(company_name) >= self.per_company_jobs:
                return False
            if company_name in self.ignored_companies_seen:
                self.processed_uids.add(uid)
                return False
            matched_rule = next(
                (ic for ic in self.ignore_companies
                 if re.search(r'\b' + re.escape(ic) + r'\b', company_name, re.IGNORECASE)),
                None,
            )
            if matched_rule:
                self.ignored_companies_seen.add(company_name)
                self.processed_uids.add(uid)
                return False
            self.processed_uids.add(uid)
            self.new_processed_company_jobs.append(company_name)
            return True

    def is_duplicate_fingerprint(self, position: str, company: str) -> bool:
        """
        Returns True if this (position, company) pair has already been saved.
        Only active when save_unique_only=True.
        """
        if not self.save_unique_only:
            return False
        fp = f"{position.strip().lower()}|||{company.strip().lower()}"
        if fp in self._seen_fingerprints:
            return True
        self._seen_fingerprints.add(fp)
        return False

    async def confirm_filtered_jobs(
        self, links: list, percentages: list
    ) -> tuple[list, list]:
        async with self.tracking_lock:
            remaining      = self.max_jobs - self.extracted_jobs_counter
            if remaining <= 0:
                return [], []
            accepted_count = min(len(links), remaining)
            self.extracted_jobs_counter += accepted_count
            return links[:accepted_count], percentages[:accepted_count]

    async def is_limit_reached(self) -> bool:
        async with self.tracking_lock:
            return self.extracted_jobs_counter >= self.max_jobs

    def total_queued(self) -> int:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Config builder
# ─────────────────────────────────────────────────────────────────────────────
def load_scraper_config(
    url_list:               list[str],
    about_me:               str,
    ignore_companies_raw:   str,
    ignore_related_raw:     str,
    max_jobs:               int,
    per_company_jobs:       int,
    min_match_percentage:   int,
    concurrency:            int,
    processed_uids:         set[str],
    account_cookies:        list[dict],
    search_keywords:        list[str],
    search_location:        str,
    search_country:         str,
    scrape_company_details: bool,
    save_unique_only:       bool,
    follow_apply_redirect:  bool,
    skip_expired_jobs:      bool,
    skip_ignore_related_jobs:  bool,
    
    proxies:                list[str] | None = None,
    proxies_path:           "Path | None" = None,
    proxies_state:          "Path | None" = None,
    google_sheet_url:       str  = "",
    sheet_name:             str  = "Indeed Jobs",
    headless:               bool = True,
) -> ScraperConfig:
    ignore_companies = [c.strip().lower() for c in ignore_companies_raw.splitlines() if c.strip()]
    ignore_related   = [kw.strip().lower() for kw in ignore_related_raw.splitlines() if kw.strip()]

    return ScraperConfig(
        about_me=about_me,
        ignore_companies=ignore_companies,
        ignore_related=ignore_related,
        concurrency=concurrency,
        url_queue=url_list,
        max_jobs=max_jobs,
        per_company_jobs=per_company_jobs,
        min_match_percentage=min_match_percentage,
        search_keywords=search_keywords,
        search_location=search_location,
        search_country=search_country,
        scrape_company_details=scrape_company_details,
        save_unique_only=save_unique_only,
        follow_apply_redirect=follow_apply_redirect,
        skip_expired_jobs=skip_expired_jobs,
        skip_ignore_related_jobs=skip_ignore_related_jobs,
        google_sheet_url=google_sheet_url,
        sheet_name=sheet_name,
        headless=headless,
        proxies=proxies or [],
        proxies_path=proxies_path,
        proxies_state=proxies_state,
        processed_uids=processed_uids,
        account_cookies=account_cookies,
    )
# ─────────────────────────────────────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────────────────────────────────────
async def log(level: str, message: str) -> None:
    fn = getattr(Actor.log, level, Actor.log.info)
    fn(message)


# ─────────────────────────────────────────────────────────────────────────────
# UID persistence
# ─────────────────────────────────────────────────────────────────────────────
_KV_UID_KEY = "processed_uids"


async def save_processed_uids(uids: list) -> None:
    if not uids:
        return
    try:
        kv       = await Actor.open_key_value_store()
        existing = await kv.get_value(_KV_UID_KEY) or []
        merged   = list(set(existing) | set(uids))
        await kv.set_value(_KV_UID_KEY, merged)
        Actor.log.info(f"💾 Saved {len(uids)} new UIDs (total: {len(merged)})")
    except Exception as e:
        Actor.log.warning(f"⚠️ Could not save UIDs: {e}")


async def update_processed_uids(uids: list) -> None:
    await save_processed_uids(uids)


# ─────────────────────────────────────────────────────────────────────────────
# Data push
# ─────────────────────────────────────────────────────────────────────────────
async def push_job_data(data: dict, config: ScraperConfig) -> None:
    try:
        await Actor.push_data(data)
        config._saved_jobs.append(data)
    except Exception as e:
        Actor.log.warning(f"⚠️ Failed to push data: {e}")


def get_proxy(proxies: list[str], config: ScraperConfig):
    # First use in-memory proxies
    if proxies:
        return proxies.pop()

    state_file = Path(config.proxies_state)

    proxies_start_number = 0
    if state_file.exists():
        with open(state_file, "r") as f:
            proxies_start_number = json.load(f).get("proxies_start_number", 0)

    # Read proxy file
    with open(config.proxies_path, "r") as f:
        all_proxies = [line.strip() for line in f if line.strip()]

    # No proxies anywhere
    if not all_proxies:
        log.warning("[HP] No proxies available.")
        return []

    index = proxies_start_number % len(all_proxies)
    proxy = all_proxies[index].split(":")

    # Save next index
    new_start = (index + 1) % len(all_proxies)
    with open(state_file, "w") as f:
        json.dump({"proxies_start_number": new_start}, f)

    return proxy

# ─────────────────────────────────────────────────────────────────────────────
# Browser context creation
# ─────────────────────────────────────────────────────────────────────────────

def get_timezone_from_ip(ip: str | None = None) -> str:
    try:
        url = f"http://ip-api.com/json/{ip}" if ip else "http://ip-api.com/json"
        data = requests.get(url, timeout=5).json()
        if data.get("status") == "success":
            tz = data.get("timezone", "UTC")
            print(f"[INFO] Timezone: {tz}")
            return tz
    except Exception as e:
        print(f"[WARN] Could not fetch timezone: {e}")
    return "UTC"

def get_proxy_public_ip(ip: str, port: str, user: str, pwd: str) -> str:
    try:
        r = requests.get(
            "https://api.ipify.org",
            proxies={"https": f"http://{user}:{pwd}@{ip}:{port}"},
            timeout=8,
        )
        addr = r.text.strip()
        print(f"[INFO] Proxy public IP: {addr}")
        return addr
    except Exception as e:
        print(f"[WARN] Could not get proxy public IP: {e}")
    return ip


def _get_builtin_proxy(config: ScraperConfig) -> list[str]:
    """File-based round-robin fallback — your original logic."""
    if not config.proxies_path:
        return []
    proxies_file = Path(config.proxies_path)
    if not proxies_file.exists():
        Actor.log.warning("⚠️ Built-in proxies file not found — running without proxy.")
        return []

    state_file = Path(config.proxies_state) if config.proxies_state else None
    proxies_start_number = 0
    if state_file and state_file.exists():
        with open(state_file, "r") as f:
            proxies_start_number = json.load(f).get("proxies_start_number", 0)

    with open(proxies_file, "r") as f:
        all_lines = [line.strip() for line in f if line.strip()]
    if not all_lines:
        Actor.log.warning("⚠️ Built-in proxies file is empty — running without proxy.")
        return []

    index  = proxies_start_number % len(all_lines)
    parsed = _parse_proxy_entry(all_lines[index]) or []

    new_start = (index + 1) % len(all_lines)
    if state_file:
        with open(state_file, "w") as f:
            json.dump({"proxies_start_number": new_start}, f)

    return parsed


async def get_proxy(proxies: list[str], config: ScraperConfig) -> list[str]:
    """
    Priority: user-supplied proxies (config.proxies) — one popped per context,
    health-checked before use. Dead ones are discarded (not retried).
    Once the user list is empty (or the popped one fails), fall back to
    the built-in proxies.txt round-robin.
    """
    if proxies:
        raw = proxies.pop()
        parsed = _parse_proxy_entry(raw)
        if parsed:
            ip, port, user, pwd = parsed
            alive = await asyncio.to_thread(is_proxy_alive, ip, port, user, pwd)
            if alive:
                Actor.log.info(f"✅ User proxy OK: {ip}:{port}")
                return parsed
            Actor.log.warning(f"⚠️ User proxy {ip}:{port} failed health check — using built-in instead")
        else:
            Actor.log.warning(f"⚠️ Could not parse proxy '{raw}' — using built-in instead")

    return _get_builtin_proxy(config)

async def create_context(browser: Browser, config: ScraperConfig):
    proxy = await get_proxy(config.proxies, config)   # [ip, port, user, pwd] or []

    proxy_ip = port = user = pwd = None
    proxy_public_ip = None

    if proxy and len(proxy) == 4:
        proxy_ip, port, user, pwd = proxy
        proxy_public_ip = await asyncio.to_thread(
            fg_generator.get_proxy_public_ip, proxy_ip, port, user, pwd
        )

    timezone_id = await asyncio.to_thread(fg_generator.get_timezone_from_ip, proxy_ip)
    fingerprint = fg_generator.generate()
    script      = fg_generator.build_js_script(fingerprint)

    context_options: dict = {
        "timezone_id": timezone_id,
        "no_viewport": True,
        "user_agent":  fingerprint["user_agent"],
    }

    if proxy and len(proxy) == 4:
        proxy_dict = {"server": f"http://{proxy_ip}:{port}"}
        if user:
            proxy_dict["username"] = user
            proxy_dict["password"] = pwd
        context_options["proxy"] = proxy_dict

    context = await browser.new_context(**context_options)

    # inject cookies
    account_cookies = config.account_cookies
    if account_cookies:
        await context.add_cookies(account_cookies)
    await context.add_init_script(script)

    if proxy and proxy_public_ip:
        await context.add_init_script(fg_generator._webrtc_ip_spoof_script(proxy_public_ip))

    return context


# ─────────────────────────────────────────────────────────────────────────────
# Human-behaviour simulation
# ─────────────────────────────────────────────────────────────────────────────
async def simulate_human_behavior(page: Page) -> None:
    await asyncio.sleep(random.uniform(0.2, 0.5))
    for _ in range(random.randint(1, 2)):
        await page.mouse.wheel(0, random.randint(100, 200))
        await asyncio.sleep(random.uniform(0.2, 0.5))
    await page.mouse.move(random.randint(0, 800), random.randint(0, 600), steps=random.randint(5, 10))
    await asyncio.sleep(random.uniform(0.2, 0.5))
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(random.uniform(0.5, 1.0))
    await page.mouse.move(random.randint(0, 800), random.randint(0, 600), steps=random.randint(5, 10))
    await asyncio.sleep(random.uniform(0.2, 0.5))


# ─────────────────────────────────────────────────────────────────────────────
# Page navigation helpers
# ─────────────────────────────────────────────────────────────────────────────
async def open_jobs_search_page(page: Page, job_search_url: str, url_queue: asyncio.Queue) -> None:
    for attempt in range(3):
        try:
            await page.goto(job_search_url, wait_until="load")
            # Check if Indeed redirected to the sign-in page (cookies expired)
            page_title = await page.title()
            if "sign in | indeed accounts" in page_title.lower():
                Actor.log.info(
                    f"Indeed redirected to the sign-in page (cookies may have expired). "
                    f"Update cookies: {page.url}"
                )
                clear_queue(url_queue)
                return False
            return
        except PlaywrightTimeoutError:
            if attempt < 2:
                Actor.log.info(f"⚠️ Attempt {attempt + 1} failed, retrying…")
            else:
                Actor.log.warning(f"❌ All attempts failed for {job_search_url}, re-queued")
                await url_queue.put(job_search_url)
                await asyncio.sleep(random.uniform(4, 8))


async def get_total_jobs(page: Page) -> int:
    try:
        content = await page.content()
        match   = re.search(r'"totalJobCount":(\d+)', content)
        return int(match.group(1)) if match else 0
    except Exception as e:
        Actor.log.warning(f"⚠️ Error finding total jobs: {e}")
        return 0


async def build_and_enqueue_jobs_search_urls(
    total_jobs: int,
    base_url:   str,
    url_queue:  asyncio.Queue,
) -> None:
    """
    Enqueue pagination URLs.
    """
    cap = total_jobs
    for start in range(10, cap, 10):
        await url_queue.put(f"{base_url}&start={start}")


# ─────────────────────────────────────────────────────────────────────────────
# Queue helper
# ─────────────────────────────────────────────────────────────────────────────
def clear_queue(q: asyncio.Queue) -> None:
    while not q.empty():
        try:
            q.get_nowait()
            q.task_done()
        except asyncio.QueueEmpty:
            break


# ─────────────────────────────────────────────────────────────────────────────
# Job info extractors
# ─────────────────────────────────────────────────────────────────────────────


async def _get_company(page: Page) -> str | None:
    company = await page.locator('[data-testid="inlineHeader-companyName"] a').text_content()
    return company.strip() if company else None


async def _get_company_indeed_url(page: Page) -> str:
    """Return the href of the company's Indeed profile link if present."""
    try:
        link = page.locator(
            '[data-testid="inlineHeader-companyName"] a, '
            'div[data-company-name="true"] a'
        ).first
        if await link.count() > 0:
            href = await link.get_attribute("href")
            if href:
                # Make absolute
                if href.startswith("/"):
                    return f"https://www.indeed.com{href}"
                return href
    except Exception:
        pass
    return ""


import re

async def _extract_posted_date(
    page: Page,
    date_on_indeed: int | None = None,
) -> tuple[str, str]:
    """
    Returns:
        postedAt            -> "12 days ago"
        postingDateParsed   -> "2026-06-18T15:58:53.616Z"
    """
    posted_at = ""
    posting_date_parsed = ""

    try:
        content = await page.content()

        # Fall back to pulling the epoch-ms timestamp from the embedded JSON
        if not date_on_indeed:
            match = re.search(r'"datePublished":(\d+)', content)
            if match:
                date_on_indeed = int(match.group(1))

        if date_on_indeed:
            posting_date_parsed = (
                datetime.fromtimestamp(date_on_indeed / 1000, tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )

        # Relative-age text, e.g. hiringInsightsModel.age in the embedded JSON
        age_match = re.search(r'"age":"([^"]+)"', content)
        if age_match:
            posted_at = age_match.group(1)

    except Exception as e:
        Actor.log.warning(f"⚠️ Failed to extract posted date: {e}")

    return posted_at, posting_date_parsed

async def extract_salary_job_types(page: Page):
    container = page.locator("#salaryInfoAndJobType")
    salary    = ""
    job_types = []

    if await container.count() > 0:
        spans             = container.locator("span")
        count             = await spans.count()
        delimiter_pattern = re.compile(r'\s*[,\/·]\s*|\s+-\s+')

        for i in range(count):
            text = (await spans.nth(i).inner_text()).strip()
            if not text:
                continue
            if any(sym in text for sym in ("$", "£", "€")) or "year" in text.lower():
                salary = text
            else:
                tokens = [t.strip() for t in delimiter_pattern.split(text) if t.strip()]
                job_types.extend(tokens)

        if count == 1 and not salary:
            text      = await spans.first.inner_text()
            job_types = [t.strip() for t in delimiter_pattern.split(text) if t.strip()]

    return (salary, job_types)


async def extract_rating_and_reviews(page: Page) -> dict:
    review_block = page.locator(".jobsearch-CompanyReview").first
    if await review_block.count() == 0:
        return {"rating": 0.0, "review_count": 0}

    rating     = 0.0
    rating_div = review_block.locator('div[role="img"]').first
    if await rating_div.count() > 0:
        aria_label = await rating_div.get_attribute("aria-label")
        if aria_label:
            m = re.search(r'(\d+\.?\d*)', aria_label)
            if m:
                rating = float(m.group(1))

    review_count = 0
    count_span   = review_block.locator("span.css-1t3rggk").first
    if await count_span.count() > 0:
        m = re.search(r'(\d+)', (await count_span.inner_text()).strip())
        if m:
            review_count = int(m.group(1))

    return {"rating": rating, "review_count": review_count}


async def extract_external_apply_link(page: Page) -> str:
    btn = page.locator('button[aria-label*="Apply on company site"]').first
    if await btn.count() > 0:
        href = await btn.get_attribute("href")
        return href or ""
    return ""


async def resolve_redirect(page: Page, url: str) -> str:
    """
    Follow redirects on an external apply link and return the final URL.
    Uses a lightweight fetch (no rendering) to avoid opening a full page.
    """
    if not url:
        return ""
    try:
        result = await page.evaluate(
            """async (url) => {
                try {
                    const resp = await fetch(url, {method: 'HEAD', redirect: 'follow'});
                    return resp.url;
                } catch(e) {
                    return url;
                }
            }""",
            url,
        )
        return result or url
    except Exception:
        return url


async def scrape_company_details(page: Page, company_name: str) -> dict:
    """
    Visit the Indeed company page and extract size, industry, and description.
    Returns a dict with company_size, company_industry, company_description.
    Falls back to empty strings on any error.
    """
    result = {"company_size": "", "company_industry": "", "company_description": ""}
    try:
        search_slug = re.sub(r'\s+', '-', company_name.strip().lower())
        # Indeed company pages follow /cmp/<slug>
        company_url = f"https://www.indeed.com/cmp/{search_slug}"
        await page.goto(company_url, wait_until="load", timeout=15000)
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # Company size
        size_loc = page.locator('[data-testid="companyInfo-employee-count"], [data-tn-element="company-employee-count"]').first
        if await size_loc.count() > 0:
            result["company_size"] = (await size_loc.inner_text()).strip()

        # Industry
        industry_loc = page.locator('[data-testid="companyInfo-industry"], [data-tn-element="company-industry"]').first
        if await industry_loc.count() > 0:
            result["company_industry"] = (await industry_loc.inner_text()).strip()

        # Description (first paragraph)
        desc_loc = page.locator('[data-testid="aboutSection"] p, .cmp-AboutSection p').first
        if await desc_loc.count() > 0:
            result["company_description"] = (await desc_loc.inner_text()).strip()[:500]

    except Exception as e:
        Actor.log.warning(f"⚠️ Company detail scrape failed for '{company_name}': {e}")

    return result


def is_ignored(company_name: str, ignore_companies: list[str]) -> tuple[bool, str]:
    for ic in ignore_companies:
        if re.search(r'\b' + re.escape(ic) + r'\b', company_name, re.IGNORECASE):
            return True, ic
    return False, ""


def check_remote_status(description: str, location: str = "", remote_badge: str = "") -> str:
    badge = remote_badge.strip().lower()
    if badge == "remote":
        return "Remote"
    if "hybrid" in badge:
        return "Hybrid"
    if badge in ("in-person", "on-site", "on site"):
        return "In-Person"

    text = (description + " " + location).lower()

    non_remote_patterns = [
        r'not a remote position', r'not a remote role', r'this is not a remote',
        r'must work in the office', r'work location:\s*in[- ]person',
        r'work remotely[:\s]*no', r'no remote work', r'not eligible for remote',
        r'fully on[- ]site', r'this is a fully on[- ]site',
        r'works full[- ]time on[- ]site', r'on[- ]site only',
        r'office[- ]based', r'in[- ]office required', r'must be.*on[- ]site',
        r'required to (work|report) (in|to) (the )?office',
    ]
    hybrid_patterns = [
        r'\bhybrid\b', r'hybrid remote', r'hybrid work', r'hybrid schedule',
        r'part[- ]time remote',
        r'\d+\s*days?\s*(per week\s*)?(in|from)\s*(the\s*)?office',
        r'\d+\s*days?\s*(a|per)\s*week.*on[- ]?site',
        r'remote.*after.*training', r'may be available.*remote',
        r'blending.*office day', r'#li-hybrid', r'2[- ]3 days onsite',
        r'split.*between.*office.*home', r'flexible.*in[- ]office',
    ]
    remote_patterns = [
        r'\bfully remote\b', r'\b100% remote\b', r'work from home',
        r'work location:\s*remote', r'\bremote position\b', r'\bremote role\b',
        r'\bremote-first\b', r'work remotely[:\s]*yes', r'this is a remote',
        r'#li-remote', r'remote.*anywhere', r'work from anywhere',
        r'permanently remote', r'telework',
    ]

    if any(re.search(p, text) for p in non_remote_patterns):
        return "In-Person"
    if any(re.search(p, text) for p in hybrid_patterns):
        return "Hybrid"
    if any(re.search(p, text) for p in remote_patterns):
        return "Remote"
    return " "


# ─────────────────────────────────────────────────────────────────────────────
# Shared batch flush
# ─────────────────────────────────────────────────────────────────────────────
async def flush_batch(
    config: ScraperConfig,
    batch_positions: list[str],
    batch_links:     list[str],
    batch_uids:      list[str],
    filter_queue:    asyncio.Queue,
) -> None:
    percentages = await get_match_percentages(config.about_me, batch_positions)
    threshold   = config.min_match_percentage if config.ai_matching_enabled else 0

    passed_links: list[str]   = []
    passed_pcts:  list[float] = []
    for link, pct in zip(batch_links, percentages):
        if pct >= threshold:
            passed_links.append(link)
            passed_pcts.append(pct)

    if passed_links:
        accepted_links, accepted_pcts = await config.confirm_filtered_jobs(passed_links, passed_pcts)
        for link, pct in zip(accepted_links, accepted_pcts):
            await filter_queue.put((link, pct))
        if accepted_links:
            Actor.log.info(
                f"✅ Scorer accepted {len(accepted_links)}/{len(passed_links)} jobs"
                f"  |  total: {config.extracted_jobs_counter}/{config.max_jobs}"
            )

    await config.buffer_uids(batch_uids)
    if len(config._uid_buffer) >= ScraperSettings.UID_FLUSH_SIZE:
        uids_to_save = await config.flush_uid_buffer()
        await update_processed_uids(uids_to_save)


async def _flush_shared_batch(
    config: ScraperConfig,
    batch_positions: list,
    batch_links:     list,
    batch_uids:      list,
    batch_lock:      asyncio.Lock,
    filter_queue:    asyncio.Queue,
) -> None:
    async with batch_lock:
        if not batch_positions:
            return
        snap_positions = batch_positions.copy()
        snap_links     = batch_links.copy()
        snap_uids      = batch_uids.copy()
        batch_positions.clear()
        batch_links.clear()
        batch_uids.clear()
    await flush_batch(config, snap_positions, snap_links, snap_uids, filter_queue)


# ─────────────────────────────────────────────────────────────────────────────
# Status logger
# ─────────────────────────────────────────────────────────────────────────────
async def status_logger(config: ScraperConfig, stop_event: asyncio.Event) -> None:
    BAR_LEN, last_saved, ticks_since_log = 20, -1, 0
    HEARTBEAT_TICKS = 30

    while not stop_event.is_set():
        saved = config.extracted_jobs_counter
        ticks_since_log += 1

        if saved != last_saved or ticks_since_log >= HEARTBEAT_TICKS:
            pct    = saved / config.max_jobs if config.max_jobs else 0
            filled = int(pct * BAR_LEN)
            bar    = "█" * filled + "░" * (BAR_LEN - filled)
            Actor.log.info(
                f"📊 [{bar}] {pct * 100:.1f}%  |  ✅ saved: {saved}/{config.max_jobs}"
            )
            last_saved, ticks_since_log = saved, 0

        await asyncio.sleep(2)


# ─────────────────────────────────────────────────────────────────────────────
# Startup info
# ─────────────────────────────────────────────────────────────────────────────
async def showstartinginfo(config: ScraperConfig) -> None:
    Actor.log.info("=" * 80)
    Actor.log.info(f"🎯 Max jobs:            {config.max_jobs}")
    Actor.log.info(f"🔗 Search URLs:         {len(config.url_queue)}")
    if config.search_keywords:
        Actor.log.info(f"🔑 Keywords:            {config.search_keywords}")
        Actor.log.info(f"📍 Location:            '{config.search_location}' | country={config.search_country}")
    Actor.log.info(f"⚡ Concurrency:         {config.concurrency}")
    Actor.log.info(f"🏢 Per company:         {config.per_company_jobs}")
    if config.ai_matching_enabled:
        Actor.log.info(f"🤖 AI matching:         ON  |  min score: {config.min_match_percentage}%")
    else:
        Actor.log.info("🤖 AI matching:         OFF (all jobs collected)")
    Actor.log.info(f"🏭 Company details:     {'ON' if config.scrape_company_details else 'OFF'}")
    Actor.log.info(f"🔁 Unique jobs only:    {'ON' if config.save_unique_only else 'OFF'}")
    Actor.log.info(f"🔗 Follow apply link:   {'ON' if config.follow_apply_redirect else 'OFF'}")
    Actor.log.info(f"🔗 Skip expired jobs:   {'ON' if config.skip_expired_jobs else 'OFF'}")
    Actor.log.info(f"🔗 Skip ignore related jobs:{'ON' if config.skip_ignore_related_jobs else 'OFF'}")
    Actor.log.info(f"🚫 Ignore companies:    {len(config.ignore_companies)}")
    Actor.log.info(f"🚫 Ignore related:      {config.ignore_related}")
    Actor.log.info(f"📚 Prev processed:      {len(config.processed_uids)} job IDs")
    Actor.log.info(f"🍪 Cookies:             {'provided' if config.account_cookies else 'not provided'}")
    Actor.log.info(f"👻 Headless:            {config.headless}")
    Actor.log.info("=" * 80)
    Actor.log.info("🏃 Starting scraper execution…")