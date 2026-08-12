"""
Lever public job postings API.
Similar to Greenhouse - many companies (Netflix, Palantir, etc.) use Lever
and expose a free public JSON feed of open roles.

Endpoint pattern: https://api.lever.co/v0/postings/{company}?mode=json
"""
import requests
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.lever.co/v0/postings/{company}"


def fetch_jobs(company_slug: str, timeout: int = 15) -> list[dict]:
    url = BASE_URL.format(company=company_slug)
    try:
        resp = requests.get(url, params={"mode": "json"}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[lever] failed for '{company_slug}': {e}")
        return []

    postings = resp.json()
    jobs = []
    for p in postings:
        categories = p.get("categories", {})
        jobs.append({
            "title": p.get("text", "").strip(),
            "company": company_slug,
            "location": categories.get("location", "Unspecified"),
            "description": p.get("descriptionPlain", p.get("description", "")),
            "apply_url": p.get("hostedUrl", p.get("applyUrl", "")),
            "source": "lever",
        })
    return jobs


def fetch_multiple(company_slugs: list[str]) -> list[dict]:
    all_jobs = []
    for slug in company_slugs:
        slug = slug.strip()
        if not slug:
            continue
        all_jobs.extend(fetch_jobs(slug))
    return all_jobs
