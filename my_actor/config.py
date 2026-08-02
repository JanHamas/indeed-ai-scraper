"""
my_actor/config.py
Scrape.do Web Scraping API accounts and scraper limits.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO ADD MORE ACCOUNTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Copy the block below and append it to SCRAPEDO_ACCOUNTS.
The scraper auto-detects all accounts and rotates through them.
When one fails or hits its rate limit, the next one is tried automatically.

`token` is the API token shown on your Scrape.do dashboard.
Strongly recommend loading these from environment variables instead of
hardcoding them here (e.g. os.environ["SCRAPEDO_TOKEN_1"]).
"""

API_URL = "https://api.scrape.do/"

# ── Scrape.do accounts ─────────────────────────────────────────────────────
# Add more dicts here when you buy additional subscriptions/tokens.
# ────────────────────────────────────────────────────────────────────────────
SCRAPEDO_ACCOUNTS = [
    # harisjan4455gamil.com Usage: 99.10%   renew: 02/09/2026 12:48
    {"name": "account_1", "token": "073c0a5abcc349929759d0bb11f77390bf4e9bcb020", "rate_limit": 5},
    
    # p98632838@gmail.com Usage: 99.70% renew: 03/09/2026 02:37
    {"name": "account_2", "token": "571a8bcb0e564063a5258ca3b59b64d3c5a9c7017a7", "rate_limit": 5},

    # hamsajan33@gmail.com Usage: 0.10% renew: 03/09/2026 04:39
    {"name": "account_3", "token": "d0f9b10dbc474b808e81d2ed93e3ba745e982b14513", "rate_limit": 5},
    
    # hamsajan33@gmail.com Usage: 0.10% renew: 03/09/2026 04:51
    {"name": "account_4", "token": "43cdbc953894435fa402c3e6f3a9baafff4f7c857cd", "rate_limit": 5},
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