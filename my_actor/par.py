#!/usr/bin/env python3
"""
Scrape a list of Indeed job URLs through Decodo's Scraper API (v2/scrape),
spread across multiple Decodo accounts.

Each account has its own rate limit (default 10 req/s). Total concurrency
scales automatically with the number of accounts: N accounts x 10/s each
= N*10 total concurrent workers. Just add more entries to ACCOUNTS below
and everything (worker pool, rate limiting, URL distribution) scales with it.

- Reads URLs from urls.txt (one per line)
- Round-robins URLs across accounts, each account capped at its own rate limit
- Retries a failed request up to 3 times (same account)
- Saves each response's HTML into responses/
- Prints per-request time, average time, and total time taken

Usage:
    python3 scrape_indeed_multi.py
"""

import os
import re
import json
import time
import threading
import concurrent.futures

import requests

# ---------------------------------------------------------------------------
# Accounts config
# ---------------------------------------------------------------------------
# Add as many accounts as you have. "rate_limit" = max requests/sec for that
# account. Total concurrency = sum of all rate_limits.

ACCOUNTS = [
    {
        "name": "account_1",
        "authorization": "Basic VTAwMDA0NTA4MjM6UFdfMWQ0MjFlYmE2YjljNGEwNjYyZTZjMzg0YTdhMDA0OTNm",
        "rate_limit": 10,
    },
    {
        "name": "account_2",
        "authorization": "Basic VTAwMDA0Nzc4Mzc6UFdfMWU3ZDYxMjJlYzdmNTM2OThmYWJmOGZkODhmYjg0ODM0",
        "rate_limit": 10,
    },
    # {
    #     "name": "account_3",
    #     "authorization": "Basic ...",
    #     "rate_limit": 10,
    # },
]

API_URL = "https://scraper-api.decodo.com/v2/scrape"

URLS_FILE = "urls.txt"
OUTPUT_DIR = "responses"
MAX_RETRIES = 3
REQUEST_TIMEOUT = 150  # seconds, per attempt

print_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Per-account token-bucket rate limiter (keeps each account under its req/s cap)
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, rate_per_sec: float):
        self.rate = rate_per_sec
        self.capacity = max(1, int(rate_per_sec))
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                refill = elapsed * self.rate
                if refill > 0:
                    self.tokens = min(self.capacity, self.tokens + refill)
                    self.last_refill = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait_time = (1 - self.tokens) / self.rate
            time.sleep(wait_time)


class Account:
    def __init__(self, cfg: dict):
        self.name = cfg["name"]
        self.headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": cfg["authorization"],
        }
        self.rate_limit = cfg["rate_limit"]
        self.limiter = RateLimiter(cfg["rate_limit"])


ACCOUNT_OBJS = [Account(cfg) for cfg in ACCOUNTS if cfg.get("authorization")]

if not ACCOUNT_OBJS:
    raise SystemExit("No accounts configured with an authorization header. "
                      "Fill in ACCOUNTS at the top of the script.")

TOTAL_WORKERS = sum(a.rate_limit for a in ACCOUNT_OBJS)


def safe_filename_from_url(url: str, idx: int) -> str:
    match = re.search(r"[?&]jk=([a-zA-Z0-9]+)", url)
    if match:
        return f"{idx:04d}_{match.group(1)}.html"
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", url).strip("_")
    return f"{idx:04d}_{cleaned[:80]}.html"


def extract_html(resp_json: dict) -> str:
    """Pull the rendered HTML out of Decodo's JSON response."""
    try:
        results = resp_json.get("results")
        if results and isinstance(results, list):
            content = results[0].get("content")
            if content:
                return content
    except (AttributeError, IndexError, KeyError):
        pass
    return json.dumps(resp_json, ensure_ascii=False, indent=2)


def fetch_url(idx: int, url: str, account: Account):
    """Fetch a single URL through one Decodo account, with retries."""
    filename = safe_filename_from_url(url, idx)
    filepath = os.path.join(OUTPUT_DIR, filename)

    payload = {
        "url": url,
        "proxy_pool": "premium",
        "headless": "html",
    }

    start = time.perf_counter()
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        account.limiter.acquire()  # respect this account's req/s cap
        try:
            resp = requests.post(
                API_URL,
                json=payload,
                headers=account.headers,
                timeout=REQUEST_TIMEOUT,
            )
            elapsed = time.perf_counter() - start

            if resp.status_code == 200 and resp.text:
                try:
                    resp_json = resp.json()
                    html = extract_html(resp_json)
                except ValueError:
                    html = resp.text

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(html)

                with print_lock:
                    print(
                        f"[OK]   #{idx:04d} [{account.name}] status={resp.status_code} "
                        f"time={elapsed:.2f}s attempt={attempt} -> {filename}"
                    )
                return {"idx": idx, "url": url, "success": True,
                         "time": elapsed, "attempts": attempt,
                         "status": resp.status_code, "account": account.name}
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                with print_lock:
                    print(
                        f"[WARN] #{idx:04d} [{account.name}] status={resp.status_code} "
                        f"attempt={attempt}/{MAX_RETRIES} - retrying..."
                    )
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            with print_lock:
                print(
                    f"[ERR]  #{idx:04d} [{account.name}] attempt={attempt}/{MAX_RETRIES} "
                    f"error={last_error}"
                )

    elapsed = time.perf_counter() - start
    with print_lock:
        print(
            f"[FAIL] #{idx:04d} [{account.name}] time={elapsed:.2f}s after "
            f"{MAX_RETRIES} attempts - {url} - last_error={last_error}"
        )
    return {"idx": idx, "url": url, "success": False,
             "time": elapsed, "attempts": MAX_RETRIES, "status": None,
             "error": last_error, "account": account.name}


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not os.path.exists(URLS_FILE):
        print(f"URLs file not found: {URLS_FILE}")
        return

    with open(URLS_FILE, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    total_urls = len(urls)
    if total_urls == 0:
        print("No URLs found in urls.txt")
        return

    print(f"Loaded {total_urls} URLs.")
    print(f"Accounts: {len(ACCOUNT_OBJS)} -> "
          f"{', '.join(f'{a.name}({a.rate_limit}/s)' for a in ACCOUNT_OBJS)}")
    print(f"Total concurrency: {TOTAL_WORKERS} workers, "
          f"max {MAX_RETRIES} retries per URL.\n")

    overall_start = time.perf_counter()
    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=TOTAL_WORKERS) as executor:
        futures = {}
        # Round-robin: URL i goes to account (i % num_accounts)
        for idx, url in enumerate(urls, start=1):
            account = ACCOUNT_OBJS[idx % len(ACCOUNT_OBJS)]
            future = executor.submit(fetch_url, idx, url, account)
            futures[future] = (idx, url)

        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    overall_elapsed = time.perf_counter() - overall_start

    # ---------------- Summary ----------------
    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]
    times = [r["time"] for r in results]
    avg_time = sum(times) / len(times) if times else 0

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Accounts used:       {len(ACCOUNT_OBJS)}")
    print(f"Total concurrency:   {TOTAL_WORKERS}")
    print(f"Total URLs:          {total_urls}")
    print(f"Successful:          {len(successes)}")
    print(f"Failed:              {len(failures)}")
    print(f"Average time/req:    {avg_time:.2f}s")
    print(f"Total time taken:    {overall_elapsed:.2f}s "
          f"({overall_elapsed/60:.2f} min)")
    print("=" * 60)

    # Per-account breakdown
    print("\nPer-account breakdown:")
    for a in ACCOUNT_OBJS:
        acc_results = [r for r in results if r["account"] == a.name]
        acc_success = [r for r in acc_results if r["success"]]
        print(f"  {a.name}: {len(acc_results)} handled, "
              f"{len(acc_success)} succeeded, "
              f"{len(acc_results) - len(acc_success)} failed")

    if failures:
        print("\nFailed URLs:")
        for r in sorted(failures, key=lambda x: x["idx"]):
            print(f"  #{r['idx']:04d} [{r['account']}] {r['url']} - {r.get('error')}")

    with open(os.path.join(OUTPUT_DIR, "_summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"Accounts used: {len(ACCOUNT_OBJS)}\n")
        f.write(f"Total concurrency: {TOTAL_WORKERS}\n")
        f.write(f"Total URLs: {total_urls}\n")
        f.write(f"Successful: {len(successes)}\n")
        f.write(f"Failed: {len(failures)}\n")
        f.write(f"Average time/req: {avg_time:.2f}s\n")
        f.write(f"Total time taken: {overall_elapsed:.2f}s\n")
        if failures:
            f.write("\nFailed URLs:\n")
            for r in sorted(failures, key=lambda x: x["idx"]):
                f.write(f"  #{r['idx']:04d} [{r['account']}] {r['url']} - {r.get('error')}\n")


if __name__ == "__main__":
    main()