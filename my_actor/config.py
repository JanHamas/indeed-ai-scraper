"""
my_actor/config.py
Bright Data Web Unlocker accounts (zones) and scraper limits.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO ADD MORE ACCOUNTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Copy the block below and append it to BRIGHTDATA_ACCOUNTS.
The scraper auto-detects all accounts and rotates through them.
When one fails or hits its rate limit, the next one is tried automatically.

This uses Bright Data's **native/proxy access** to Web Unlocker (the
"Username" / "Password" / "Host" / "Port" panel in your zone's Overview
tab), not the Direct REST API. Each "account" here is one zone:
  - `username` is the full string shown as "Username" on the zone's
    Overview tab, e.g. "brd-customer-hl_dbeb3119-zone-web_unlocker1"
    (it already encodes your customer ID and zone name — use it as-is).
  - `password` is the zone password shown as "Password".
  - `host`/`port` are almost always "brd.superproxy.io" / 44445 for all
    zones on your account, but are kept per-account here in case a zone
    ever uses a different port.

Strongly recommend loading these from environment variables instead of
hardcoding them here (e.g. os.environ["BRIGHTDATA_PASSWORD_1"]).
"""

# ── Bright Data Web Unlocker accounts (zones) ──────────────────────────────
# Add more dicts here when you buy additional zones/subscriptions.
# ────────────────────────────────────────────────────────────────────────────
BRIGHTDATA_ACCOUNTS = [
    # # example@gmail.com Usage: xx% renew: dd/mm/yyyy
    # {
    #     "name": "account_1",
    #     "username": "brd-customer-YOUR_CUSTOMER_ID-zone-YOUR_ZONE_NAME",
    #     "password": "YOUR_ZONE_PASSWORD",
    #     "host": "brd.superproxy.io",
    #     "port": 44445,
    #     "rate_limit": 5,
    # },

    {
        "name": "account_1",
        "username": "brd-customer-hl_dbeb3119-zone-web_unlocker1",
        "password": "rbw9khccsoqe",
        "host": "brd.superproxy.io",
        "port": 44445,
        "rate_limit": 50,
    },
]


class ScraperSettings:
    MAX_CONCURRENCY     = 50
    REQUEST_TIMEOUT     = 120       # seconds per HTTP request
    MAX_RETRIES         = 3         # attempts per URL before giving up
    RETRY_DELAY_MIN     = 2
    RETRY_DELAY_MAX     = 5
    UID_FLUSH_SIZE       = 200
    PAGES_PER_QUERY      = 100

    indeed_country_domains: dict[str, str] = {
        "us": "https://www.indeed.com",
        "uk": "https://uk.indeed.com",
        "ca": "https://ca.indeed.com",
        "au": "https://au.indeed.com",
        "de": "https://de.indeed.com",
        "fr": "https://fr.indeed.com",
        "in": "https://www.indeed.co.in",
        "sg": "https://www.indeed.com.sg",
        "nz": "https://nz.indeed.com",
        "ie": "https://ie.indeed.com",
        "za": "https://za.indeed.com",
    }

    extraction_fields: list[str] = [
        "id", "url", "positionName", "company", "companyIndeedUrl",
        "location", "salary", "jobType", "isRemote", "description",
        "descriptionHTML", "postedAt", "postingDateParsed", "applyType",
        "externalApplyLink", "benefits", "rating", "reviewsCount",
        "isExpired", "jobMatch", "scrapedAt",
        "searchInput/country", "searchInput/location", "searchInput/position",
        "searchInput",
    ]