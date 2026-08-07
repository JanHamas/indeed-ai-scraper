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
        # credits being refreshed on Sep 6, 2026
        "janabubakar4455": "fc-3adec90e4a6549b5b5f95c76444466cc", # 
        "ahaidbutt7@gmail.com": "fc-7529f5ede73a47a8a3b46c9bf5c77a23",
        "ahsanraza555.dev@gmail.com": "fc-b0e54a3decbe4b40bb72666d616d9ba6",
        "babarrehman1970@gmail.com": "fc-ca5bdae691f54a5d9a1721a1406b392c",
        "hamasjan82": "fc-9db9a27613c64fa0abf0f83fb0f452b8",
        "hamasjan833": "fc-c343b0a08ac04a378111701f5f1140bd",
        "hamasjan33": "fc-4201546df046413db01894d8765b2a85",
        "hamasjan4455": "fc-52c46a829e8a4138bdf700d96f8fa926",
        "jansudais5544": "fc-fbe422c6b17045469b6f2ec420cc10f7",
        "shumailaakhan5544@gmail.com": "fc-1784f1814d0f4c62924d61212fd68baf",
        "p98632838@gmail.com": "fc-1ba3c6f6002a475894d73dc252f5b407",
        "m.naqqashthr@gmail.com": "fc-00723e0617c14a51bafb99d351ff1c22",
        "khurshidsherani.dev@gmail.com": "fc-879d14a1d2c346ab860002d2749a7ac8",
        "janharis41": "fc-aa0fddaa2e854e1c922d18ca2589f7b3",
        "janharis5544": "fc-48975577169e406e9ad9d8c2fd6dde85",
        "hamasjan.dev": "fc-94392866e4bc4b88b322262d38dbc663",
        "hamasjan859": "fc-edd9b5385a414dd1881e207f5b592c22",
        "hamasjan281": "fc-30624786ec6f4b1e8807ee0daa8a4e33",
        "hamasjan75": "fc-c28924c3dbd143e8b1ae45fbeaed5ec6",
        "mnaqqashtahir": "fc-6eddf1fcff0444d4acd03f8b090cedfb",
        "jawadqayyum.dev": "fc-9905f7f56ac74b1d98f2e55d7510afaa",
        "hamasjan160@gmail.com": "fc-f5a0e70bd331465ba02fd96b22c7e66a",
        "hamasjan74@gmail.com": "fc-885e5a645e9d4819b1e74349d9e9bcba",
        "hamasjan822": "fc-6e047438346e422f8802cbd63a17eaab",
        "hamasjan154": "fc-556807af182d405ba6775a6c18c0b564",
    }

    # ── Debugging ───────────────────────────────────────────────────────
    DEBUG_SAVE_HTML = os.getenv("DEBUG_SAVE_HTML", "").lower() == "false"
    DEBUG_HTML_DIR  = "debug_html"

    # NOTE: concurrency is no longer a fixed number. Use
    # `firecrawl.max_concurrency` (from firecrawl_client.py) wherever you
    # used to read ScraperSettings.MAX_CONCURRENCY — it's 10 x however many
    # accounts still have credit, and updates itself as accounts run out.

    # ── Point 1 & 4: how many runs (users) may scrape at the same time ────
    # This is the single source of truth for the cap. run_registry.py makes
    # a run WAIT (poll) until fewer than this many runs are active before it
    # is allowed to join. Once it joins, main.py splits Firecrawl's total
    # capacity evenly across every currently-active run (1 run = 100%,
    # 2 runs = ~50% each, ...), and a 3rd run queues behind the first two —
    # that's the "chain" behavior.
    CONCURRENT_MAX_USERS = 2
    USER_QUEUE_POLL_SECONDS = 10   # how often a queued run rechecks for a free slot

    # ── Point 3: hard cap on listing workers; processing workers are NOT
    # capped by a fixed number — they scale with whatever Firecrawl capacity
    # (`concurrency`, this run's fair share) is actually available. ────────
    MAX_LISTING_WORKERS = 10

    # ── Point 6: retry a job page this many times if no company name could
    # be parsed off it, before giving up on it for good. ──────────────────
    COMPANY_RETRY_LIMIT = 2

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