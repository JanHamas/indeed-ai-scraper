"""
my_actor/config.py
BrightData Web Unlocker accounts and scraper limits.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO ADD MORE ACCOUNTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Copy the block below and append it to BRIGHTDATA_ACCOUNTS.
The scraper auto-detects all accounts and rotates through them.
When one fails or hits its rate limit, the next one is tried automatically.
"""

# ── BrightData Web Unlocker accounts ──────────────────────────────────────────
# Add more dicts here when you buy additional subscriptions.
# ─────────────────────────────────────────────────────────────────────────────
BRIGHTDATA_ACCOUNTS = [
    # Account 1 — hamasjan5544@gmail.com
    {
        "username": "brd-customer-hl_dbeb3119-zone-web_unlocker1",
        "password": "rbw9khccsoqe",
        "host":     "brd.superproxy.io",
        "port":     44445,
    },
    # Account 2 — hamasjan82@gmail.com github
    {
        "username": "brd-customer-hl_4cf2bac1-zone-web_unlocker1",
        "password": "tk5gg3kjgyhg",
        "host":     "brd.superproxy.io",
        "port":     44445,
    },
    # ← ADD MORE ACCOUNTS HERE:
    # {
    #     "username": "brd-customer-hl_XXXXXXXX-zone-web_unlocker1",
    #     "password": "YOUR_PASSWORD",
    #     "host":     "brd.superproxy.io",
    #     "port":     44445,
    # },
]


class ScraperSettings:
    MAX_CONCURRENCY     = 50
    REQUEST_TIMEOUT     = 120       # seconds per HTTP request
    MAX_RETRIES         = 3        # attempts per URL before giving up
    RETRY_DELAY_MIN     = 2        # seconds min between retries
    RETRY_DELAY_MAX     = 5        # seconds max between retries
    UID_FLUSH_SIZE      = 200      # flush processed UIDs to KV store after this many
    PAGES_PER_QUERY     = 50       # pagination pages pre-built per seed URL
    CONTEXT_ROTATE_LIMIT = 50      # requests per account before rotating (unused in HTTP mode)
    HEADLESS            = True

    # Indeed country → domain
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
