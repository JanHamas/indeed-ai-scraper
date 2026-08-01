"""
my_actor/config.py
Decodo Web Scraping API accounts and scraper limits.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO ADD MORE ACCOUNTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Copy the block below and append it to DECODO_ACCOUNTS.
The scraper auto-detects all accounts and rotates through them.
When one fails or hits its rate limit, the next one is tried automatically.

`authorization` is the full "Basic <base64(user:pass)>" string shown on
your Decodo dashboard (Scraping APIs → Web Scraping API → Authentication).
"""

API_URL = "https://scraper-api.decodo.com/v2/scrape"

# ── Decodo Web Scraping API accounts ──────────────────────────────────────────
# Add more dicts here when you buy additional subscriptions/tokens.
# ─────────────────────────────────────────────────────────────────────────────
DECODO_ACCOUNTS = [
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
    # ← ADD MORE ACCOUNTS HERE:
    {
        "name": "account_3", "hamasjan5544"
        "authorization": "Basic VTAwMDA0NTA4MjM6UFdfMWQ0MjFlYmE2YjljNGEwNjYyZTZjMzg0YTdhMDA0OTNm"
        "rate_limit": 10,
    },
]

class ScraperSettings:
    MAX_CONCURRENCY     = 50
    REQUEST_TIMEOUT     = 120       # seconds per HTTP request
    MAX_RETRIES         = 3        # attempts per URL before giving up
    RETRY_DELAY_MIN     = 2        # seconds min between retries
    RETRY_DELAY_MAX     = 5        # seconds max between retries
    UID_FLUSH_SIZE      = 200      # flush processed UIDs to KV store after this many
    PAGES_PER_QUERY     = 50       # pagination pages pre-built per seed URL

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