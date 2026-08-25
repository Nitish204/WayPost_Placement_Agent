"""
Ashby public job board API.
Many newer startups (Linear, Vercel, Ramp, Retool, Mercury, Replit, etc.)
use Ashby as their ATS and expose a FREE, public, unauthenticated JSON
API for job listings. No API key needed.

Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
Endpoint pattern: https://api.ashbyhq.com/posting-api/job-board/{token}
"""
import requests
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def fetch_jobs(board_token: str, timeout: int = 15) -> list[dict]:
    """Fetch all open jobs for a single company's Ashby board.

    Returns a normalized list of dicts: title, company, location,
    description, apply_url, source.
    """
    url = BASE_URL.format(token=board_token)
    try:
        resp = requests.get(url, params={"includeCompensation": "true"}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"[ashby] failed for board '{board_token}': {e}")
        return []

    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        location = j.get("location") or j.get("locationName") or "Unspecified"
        jobs.append({
            "title": j.get("title", "").strip(),
            "company": board_token,  # Ashby doesn't return a display name here either
            "location": location,
            "description": j.get("descriptionPlain", j.get("description", "")),
            "apply_url": j.get("applyUrl", j.get("jobUrl", "")),
            "source": "ashby",
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
