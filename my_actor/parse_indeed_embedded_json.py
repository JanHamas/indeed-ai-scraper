"""
my_actor/parse_indeed_embedded_json.py

Parses job data from Indeed's embedded JSON blocks in the page HTML:

  1. <script type="application/ld+json">{...JobPosting...}</script>
     Standard schema.org block — stable for Google/SEO rich results.

  2. window._rootProps = {...};
     Indeed's internal hydration state — richer (salary breakdown, age text,
     apply link) but uses internal field names. Treated as bonus layer.

Strategy: parse ld+json first, then enrich with _rootProps per-field.
"""
import json
import re
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


def _extract_ld_json_jobposting(html: str) -> Optional[dict]:
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    ):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
            return obj
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    return item
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


def parse_indeed_job_from_embedded_json(html: str) -> dict:
    data: dict = {
        "title": None,
        "company": None,
        "company_url": None,
        "location": None,
        "salary": None,
        "salary_min": None,
        "salary_max": None,
        "job_type": None,
        "is_remote": None,
        "description_html": None,
        "description_text": None,
        "date_posted": None,
        "valid_through": None,
        "posted_age_text": None,
        "apply_url": None,
        "job_id": None,
        "source": [],
    }

    ld = _extract_ld_json_jobposting(html)
    if ld:
        data["source"].append("ld+json")
        data["title"] = ld.get("title")
        org = ld.get("hiringOrganization") or {}
        data["company"] = org.get("name")
        data["company_url"] = org.get("sameAs")

        loc = (ld.get("jobLocation") or {}).get("address") or {}
        loc_parts = [loc.get("addressLocality"), loc.get("addressRegion")]
        loc_parts = [p for p in loc_parts if p]
        if loc_parts:
            data["location"] = ", ".join(loc_parts)

        salary = ld.get("baseSalary") or {}
        value = salary.get("value") or {}
        if value:
            data["salary_min"] = value.get("minValue")
            data["salary_max"] = value.get("maxValue")
            unit = value.get("unitText", "").lower()
            if data["salary_min"] and data["salary_max"]:
                data["salary"] = (
                    f"${data['salary_min']:,.0f} - ${data['salary_max']:,.0f} "
                    f"a {unit.rstrip('ly') or 'year'}"
                ).replace("a yearly", "a year")

        emp_types = ld.get("employmentType") or []
        if emp_types:
            data["job_type"] = ", ".join(
                t.replace("_", "-").title() for t in emp_types
            )

        data["is_remote"] = ld.get("jobLocationType") == "TELECOMMUTE"
        data["description_html"] = ld.get("description")
        data["date_posted"] = ld.get("datePosted")
        data["valid_through"] = ld.get("validThrough")

    root = _extract_root_props(html)
    if root:
        data["source"].append("_rootProps")
        data["job_id"] = data["job_id"] or root.get("jobKey")
        data["title"] = data["title"] or root.get("jobTitle")
        data["location"] = data["location"] or root.get("jobLocation")

        try:
            header = (
                root["jobInfoWrapperModel"]["jobInfoModel"]["jobInfoHeaderModel"]
            )
            data["company"] = data["company"] or header.get("companyName")
            data["location"] = data["location"] or header.get("formattedLocation")
            remote_model = header.get("remoteWorkModel") or {}
            if remote_model.get("type") == "REMOTE_ALWAYS":
                data["is_remote"] = True
        except (KeyError, TypeError):
            pass

        try:
            job_type = (
                root["jobInfoWrapperModel"]["jobInfoModel"]
                ["jobMetadataHeaderModel"]["jobType"]
            )
            data["job_type"] = data["job_type"] or job_type
        except (KeyError, TypeError):
            pass

        salary_model = root.get("salaryInfoModel") or {}
        if salary_model.get("salaryText"):
            data["salary"] = salary_model["salaryText"]
        data["salary_min"] = data["salary_min"] or salary_model.get("salaryMin")
        data["salary_max"] = data["salary_max"] or salary_model.get("salaryMax")

        try:
            data["posted_age_text"] = root["jobMetadataFooterModel"]["age"]
        except (KeyError, TypeError):
            pass

        try:
            data["apply_url"] = (
                root["viewJobButtonLinkContainerModel"]
                ["viewJobButtonLinkModel"]["href"]
            )
        except (KeyError, TypeError):
            pass

        try:
            root_desc = root["sanitizedJobDescription"]["content"]
            if root_desc:
                data["description_html"] = root_desc
        except (KeyError, TypeError):
            pass

    data["description_text"] = _html_to_text(data["description_html"])
    return data
