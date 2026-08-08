"""
my_actor/parse_indeed_rootprops.py

Single-source parser for Indeed job pages.

Source: window._rootProps.preloadedVJData.hostQueryExecutionResult
        .data.jobData.results[0].job
This is the actual GraphQL response the page hydrates from — fully
structured, present across template generations (unlike DOM selectors,
which broke silently on the 2026 RNW template).

Only extracts the fields job_scraper.py maps into extraction_fields:
id, positionName, company, companyIndeedUrl, location, salary, jobType,
isRemote, description, descriptionHTML, postedAt, postingDateParsed,
applyType, externalApplyLink, benefits, rating, reviewsCount, isExpired.
"""
import json
import re
from datetime import datetime, timezone
from typing import Optional


def _find_json_after(text: str, marker: str) -> Optional[dict]:
    idx = text.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    if start >= len(text) or text[start] != "{":
        return None
    try:
        obj, _end = json.JSONDecoder().raw_decode(text, start)
        return obj
    except json.JSONDecodeError:
        return None


def _extract_root_props(html: str) -> Optional[dict]:
    for marker in ("window._rootProps = ", "window._rootProps="):
        obj = _find_json_after(html, marker)
        if obj:
            return obj
    return None


def _html_to_text(html_fragment: Optional[str]) -> str:
    if not html_fragment:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html_fragment, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _get(d: Optional[dict], *path, default=None):
    """Safe nested .get() chain that never raises on missing/None links."""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _first_job_result(root: dict) -> Optional[dict]:
    results = _get(
        root,
        "preloadedVJData", "hostQueryExecutionResult", "data", "jobData", "results",
        default=[],
    )
    if not results:
        return None
    return _get(results[0], "job")


def parse_indeed_job(html: str) -> dict:
    """
    Parse a single Indeed job page into the exact set of fields
    job_scraper.py needs. Each field is defaulted independently so a
    missing piece (e.g. no salaryInfoModel on this posting) doesn't
    blank out the rest.
    """
    data: dict = {
        "job_id": None,
        "title": None,
        "company": None,
        "company_url": None,
        "location": None,
        "salary": None,
        "job_type": None,
        "is_remote": False,
        "remote_type": None,
        "description_html": None,
        "description_text": None,
        "date_posted_iso": None,
        "posted_age_text": None,
        "expired": None,
        "apply_url": None,
        "apply_type": None,
        "benefits": [],
        "rating": 0.0,
        "review_count": 0,
    }

    root = _extract_root_props(html)
    if not root:
        return data

    job = _first_job_result(root)
    vj = _get(root, "preloadedVJData", default={})
    info = _get(vj, "jobInfoWrapperModel", "jobInfoModel", default={})
    header = _get(info, "jobInfoHeaderModel", default={})
    footer = _get(info, "jobMetadataFooterModel", default={})

    if job:
        data["job_id"] = _get(job, "key")
        data["title"] = _get(job, "title")
        data["company"] = _get(job, "sourceEmployerName")
        data["expired"] = _get(job, "expired")
        data["apply_url"] = _get(job, "url")

        desc_html = _get(job, "description", "html")
        if desc_html:
            data["description_html"] = desc_html
            data["description_text"] = _get(job, "description", "text") or _html_to_text(desc_html)

        loc = _get(job, "location", default={})
        data["location"] = _get(loc, "formatted", "long") or _get(loc, "fullAddress")

        job_types = _get(job, "jobTypes", default=[])
        if job_types:
            data["job_type"] = ", ".join(t.get("label", "") for t in job_types if t.get("label"))

        employer = _get(job, "employer", default={})
        data["company_url"] = _get(employer, "relativeCompanyPageUrl")
        data["rating"] = float(_get(employer, "ugcStats", "ratings", "overallRating", "value", default=0.0) or 0.0)
        data["review_count"] = int(_get(employer, "ugcStats", "globalReviewCount", default=0) or 0)

        data["benefits"] = [b.get("label") for b in _get(job, "benefits", default=[]) if b.get("label")]

        ia_url = _get(job, "indeedApply", "applyLink", "url")
        if ia_url:
            data["apply_url"] = ia_url
            data["apply_type"] = "Easy Apply"
        elif data["apply_url"]:
            data["apply_type"] = "CS Apply"

        date_posted_ms = _get(job, "datePublished")
        if date_posted_ms:
            data["date_posted_iso"] = (
                datetime.fromtimestamp(date_posted_ms / 1000, tz=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )

    # remote status: header model has the clean typed version
    remote_type = _get(header, "remoteWorkModel", "type")
    if remote_type:
        data["remote_type"] = remote_type
        data["is_remote"] = remote_type == "REMOTE_ALWAYS"

    # salary: decoded salaryInfoModel, when present
    salary_info = _get(vj, "salaryInfoModel")
    if salary_info:
        data["salary"] = _get(salary_info, "salaryText")

    # UI-model fallbacks
    data["posted_age_text"] = _get(footer, "age")
    if not data["location"]:
        data["location"] = _get(info, "jobLocation") or _get(header, "formattedLocation")
    if not data["title"]:
        data["title"] = _get(info, "jobTitle")
    if not data["company"]:
        data["company"] = _get(header, "companyName")

    return data