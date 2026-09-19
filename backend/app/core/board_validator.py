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
import os
import logging
import datetime as dt
import requests

from app.sources import greenhouse, lever, ashby
from app.core.notifier import send_telegram

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


def check_and_alert_boards(db) -> dict:
    """The periodic, state-aware version of validate_boards(). Called
    on a schedule (see scheduler.py) rather than only on-demand via
    GET /admin/board-health, so a token going stale gets surfaced
    automatically instead of depending on someone remembering to check.

    Persists each token's live/dead state in BoardTokenStatus so an
    alert only fires on the LIVE -> DEAD transition, not on every check
    while it stays dead - a token that's been broken for a week
    shouldn't re-page anyone every cycle. Recovery (DEAD -> LIVE) is
    logged quietly, not alerted - good news doesn't need to interrupt
    anyone.

    Alerting itself goes to ADMIN_TELEGRAM_CHAT_ID via the existing
    Telegram notifier (see notifier.py) if that env var is set; either
    way, every newly-stale token is always logged at ERROR level so
    it's visible in Render's logs even with no Telegram configured -
    the alert is a convenience on top of that, not a replacement for it.
    """
    from app.db import BoardTokenStatus
    from app.core.ingest import resolve_boards

    boards = resolve_boards()
    result = validate_boards(boards)

    newly_stale = []
    newly_recovered = []
    now = dt.datetime.utcnow()

    for source, token_results in result["results"].items():
        for token, is_live in token_results.items():
            key = f"{source}:{token}"
            row = db.query(BoardTokenStatus).filter(BoardTokenStatus.source_token_key == key).first()

            was_live = row.is_live if row else True  # first-ever check: no prior alert to suppress

            if row is None:
                row = BoardTokenStatus(source=source, token=token, source_token_key=key)
                db.add(row)

            row.is_live = is_live
            row.last_checked_at = now

            if was_live and not is_live:
                newly_stale.append(key)
                row.last_alerted_at = now
            elif not was_live and is_live:
                newly_recovered.append(key)

    db.commit()

    if newly_stale:
        message = (
            f"⚠️ {len(newly_stale)} job board token(s) went stale:\n"
            + "\n".join(f"- {k}" for k in newly_stale)
            + "\nThey likely need removing from data/companies.json (or GREENHOUSE_BOARDS/"
              "LEVER_BOARDS/ASHBY_BOARDS) if the company migrated ATS providers."
        )
        logger.error(f"[board_validator] {message}")
        admin_chat_id = os.getenv("ADMIN_TELEGRAM_CHAT_ID", "")
        if admin_chat_id:
            send_telegram(admin_chat_id, message)

    if newly_recovered:
        logger.info(f"[board_validator] recovered: {newly_recovered}")

    return {"newly_stale": newly_stale, "newly_recovered": newly_recovered, "checked": result}
