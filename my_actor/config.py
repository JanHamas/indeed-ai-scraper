"""
my_actor/config.py
Firecrawl accounts and scraper limits.
"""
import os
from dotenv import load_dotenv
load_dotenv()

class ScraperSettings:
    # Firecrawl accounts — add more anytime, concurrency scales automatically
    accounts = {
        "hamasjan82": "fc-fbe422c6b17045469b6f2ec420cc10f7",
        "hamasjan33": "fc-48975577169e406e9ad9d8c2fd6dde85",
        "janharis4455": "fc-9db9a27613c64fa0abf0f83fb0f452b8",
        "jansudais5544": "fc-52c46a829e8a4138bdf700d96f8fa926",
        "janharis41": "fc-aa0fddaa2e854e1c922d18ca2589f7b3",
        "perveen": "fc-1ba3c6f6002a475894d73dc252f5b407",
        "hamasjan833": "fc-c343b0a08ac04a378111701f5f1140bd",
    }

    # ── Debugging ───────────────────────────────────────────────────────
    DEBUG_SAVE_HTML = os.getenv("DEBUG_SAVE_HTML", "").lower() == "false"
    DEBUG_HTML_DIR  = "debug_html"

    # NOTE: concurrency is no longer a fixed number. Use
    # `firecrawl.max_concurrency` (from firecrawl_client.py) wherever you
    # used to read ScraperSettings.MAX_CONCURRENCY — it's 10 x however many
    # accounts still have credit, and updates itself as accounts run out.

    REQUEST_TIMEOUT      = 120      # seconds per HTTP request
    MAX_RETRIES          = 3        # network-error retries
    RETRY_DELAY_MIN      = 2
    RETRY_DELAY_MAX      = 5
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