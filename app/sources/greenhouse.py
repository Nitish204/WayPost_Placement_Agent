"""
Greenhouse public job boards.
Many companies (Stripe, Airbnb, Figma, Notion, DoorDash, etc.) use Greenhouse
as their ATS and expose a FREE, public, unauthenticated JSON API for their
job listings. No API key needed. This is one of the best "off-campus"
job sources since it's the actual company posting, not an aggregator.

Docs: https://developers.greenhouse.io/job-board.html
Endpoint pattern: https://boards-api.greenhouse.io/v1/boards/{token}/jobs
"""
import requests
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch_jobs(board_token: str, timeout: int = 15) -> list[dict]:
    """Fetch all open jobs for a single company's Greenhouse board.

    Returns a normalized list of dicts: title, company, location,
    description, apply_url, source.
    """
    url = BASE_URL.format(token=board_token)
    try:
        resp = requests.get(url, params={"content": "true"}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[greenhouse] failed for board '{board_token}': {e}")
        return []

    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "title": j.get("title", "").strip(),
            "company": board_token,  # Greenhouse doesn't return a display name here
            "location": (j.get("location") or {}).get("name", "Unspecified"),
            "description": j.get("content", ""),  # HTML content
            "apply_url": j.get("absolute_url", ""),
            "source": "greenhouse",
        })
    return jobs


def fetch_multiple(board_tokens: list[str]) -> list[dict]:
    """Fetch jobs across multiple company boards, skipping failures."""
    all_jobs = []
    for token in board_tokens:
        token = token.strip()
        if not token:
            continue
        all_jobs.extend(fetch_jobs(token))
    return all_jobs
