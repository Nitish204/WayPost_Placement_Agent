"""
Adzuna job search API - a legitimate, free-tier (with signup) job
aggregator covering many countries. Good complement to Greenhouse/Lever
because it covers small/local companies too, not just startups on
modern ATS platforms.

Sign up for free keys at: https://developer.adzuna.com/
Docs: https://developer.adzuna.com/docs/search

Country codes: in (India), us, gb, ca, au, de, fr, etc.
"""
import os
import requests
import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


def fetch_jobs(
    query: str,
    location: str = "",
    country: str = "in",
    app_id: str | None = None,
    app_key: str | None = None,
    results_per_page: int = 20,
    max_pages: int = 2,
    timeout: int = 15,
) -> list[dict]:
    """Search Adzuna for jobs matching a query + location.

    Requires ADZUNA_APP_ID / ADZUNA_APP_KEY (free signup). If not
    configured, returns an empty list rather than raising, so the rest
    of the pipeline (Greenhouse/Lever) still works.
    """
    app_id = app_id or os.getenv("ADZUNA_APP_ID")
    app_key = app_key or os.getenv("ADZUNA_APP_KEY")

    if not app_id or not app_key:
        logger.info("[adzuna] skipped - no API credentials configured")
        return []

    all_jobs = []
    for page in range(1, max_pages + 1):
        url = BASE_URL.format(country=country, page=page)
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": results_per_page,
            "what": query,
            "where": location,
            "content-type": "application/json",
        }
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"[adzuna] request failed: {e}")
            break

        data = resp.json()
        results = data.get("results", [])
        if not results:
            break

        for r in results:
            all_jobs.append({
                "title": r.get("title", "").strip(),
                "company": (r.get("company") or {}).get("display_name", "Unknown"),
                "location": (r.get("location") or {}).get("display_name", "Unspecified"),
                "description": r.get("description", ""),
                "apply_url": r.get("redirect_url", ""),
                "source": "adzuna",
            })
    return all_jobs
