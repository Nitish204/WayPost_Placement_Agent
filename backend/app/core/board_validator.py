"""
Board token validator.

data/companies.json itself warns that a Greenhouse/Lever/Ashby board
token can go stale if a company migrates ATS providers or renames its
board - and when that happens, the source adapter just logs a warning
and returns [] (by design, so one dead board doesn't break ingestion
for the others). That's the right failure mode for a single fetch, but
it also means a stale token can sit unnoticed indefinitely with no
signal beyond a log line buried in normal request logs.

This module actively checks each currently-configured board token
against its real endpoint and reports which ones are dead, so staleness
becomes something you can check on demand (or see at startup) instead
of something you'd only discover by noticing a company's jobs quietly
stopped appearing.
"""
import logging
import requests

from app.sources import greenhouse, lever, ashby

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 8


def _check_greenhouse(token: str) -> bool:
    url = greenhouse.BASE_URL.format(token=token)
    try:
        resp = requests.get(url, params={"content": "false"}, timeout=_TIMEOUT_SECONDS)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _check_lever(token: str) -> bool:
    url = lever.BASE_URL.format(company=token)
    try:
        resp = requests.get(url, params={"mode": "json"}, timeout=_TIMEOUT_SECONDS)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _check_ashby(token: str) -> bool:
    url = ashby.BASE_URL.format(token=token)
    try:
        resp = requests.get(url, timeout=_TIMEOUT_SECONDS)
        return resp.status_code == 200
    except requests.RequestException:
        return False


_CHECKERS = {
    "greenhouse": _check_greenhouse,
    "lever": _check_lever,
    "ashby": _check_ashby,
}


def validate_boards(boards: dict) -> dict:
    """boards: {"greenhouse": ["stripe", ...], "lever": [...], "ashby": [...]}
    (the same shape ingest.resolve_boards() returns - env-configured if
    set, else the data/companies.json fallback list).

    Returns:
        {
          "results": {"greenhouse": {"stripe": True, "deadco": False}, ...},
          "stale": ["greenhouse:deadco", ...],   # flat list, easy to alert on
        }

    Each token is checked with a short-timeout GET against its real
    endpoint - the same endpoint the adapter itself calls - so a "live"
    result here is a real guarantee that fetch_multiple() will succeed
    for that token, not just a guess.
    """
    results = {}
    stale = []
    for source, tokens in boards.items():
        checker = _CHECKERS.get(source)
        if not checker:
            continue
        results[source] = {}
        for raw_token in tokens:
            token = raw_token.strip()
            if not token:
                continue
            is_live = checker(token)
            results[source][token] = is_live
            if not is_live:
                stale.append(f"{source}:{token}")
                logger.warning(f"[board_validator] STALE token - {source}:{token} did not resolve")
    return {"results": results, "stale": stale}
