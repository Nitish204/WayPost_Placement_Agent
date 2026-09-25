"""
Ingestion pipeline: pulls jobs from all configured sources, dedups them
against what's already in the DB, and inserts new ones. This is the
function the scheduler calls repeatedly to keep the job pool fresh -
this is what makes the agent "continuously work" rather than only
searching on-demand.
"""
import os
import json
import logging
import datetime as dt
from pathlib import Path
from sqlalchemy.orm import Session

from app.db import Job, make_job_hash
from app.sources import greenhouse, lever, adzuna, ashby

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _fallback_boards() -> dict:
    """Loads the curated company list from app/data/companies.json, used
    when GREENHOUSE_BOARDS/LEVER_BOARDS aren't set in .env - so the app
    still has something real to fetch on a fresh install instead of an
    empty job pool."""
    try:
        with open(DATA_DIR / "companies.json") as f:
            data = json.load(f)
        return {
            "greenhouse": data.get("default_fallback_greenhouse", []),
            "lever": data.get("default_fallback_lever", []),
            "ashby": data.get("default_fallback_ashby", []),
        }
    except Exception as e:
        logger.warning(f"[ingest] couldn't load fallback companies.json: {e}")
        return {"greenhouse": [], "lever": [], "ashby": []}


def resolve_boards() -> dict:
    """Resolves the actual board-token list per source: env var if set
    (GREENHOUSE_BOARDS/LEVER_BOARDS/ASHBY_BOARDS), else the curated
    fallback from data/companies.json. Pulled out of fetch_all_raw_jobs
    so board_validator.py can check the exact same tokens ingestion
    will actually use - validating a list that isn't what's really
    configured would be worse than not validating at all."""
    fallback = _fallback_boards()
    resolved = {}

    gh_boards = [b for b in os.getenv("GREENHOUSE_BOARDS", "").split(",") if b.strip()]
    if not gh_boards:
        gh_boards = fallback["greenhouse"]
        if gh_boards:
            logger.info(f"[ingest] GREENHOUSE_BOARDS not set, using fallback list: {gh_boards}")
    resolved["greenhouse"] = gh_boards

    lever_boards = [b for b in os.getenv("LEVER_BOARDS", "").split(",") if b.strip()]
    if not lever_boards:
        lever_boards = fallback["lever"]
        if lever_boards:
            logger.info(f"[ingest] LEVER_BOARDS not set, using fallback list: {lever_boards}")
    resolved["lever"] = lever_boards

    ashby_boards = [b for b in os.getenv("ASHBY_BOARDS", "").split(",") if b.strip()]
    if not ashby_boards:
        ashby_boards = fallback["ashby"]
        if ashby_boards:
            logger.info(f"[ingest] ASHBY_BOARDS not set, using fallback list: {ashby_boards}")
    resolved["ashby"] = ashby_boards

    return resolved


def fetch_board_jobs() -> list[dict]:
    """Pulls from Greenhouse/Lever/Ashby only. These sources aren't
    filtered by search query or location at the API level - a board
    token always returns that company's ENTIRE board, identical every
    time regardless of what a user searched for. Split out from
    fetch_all_raw_jobs so the scheduler can call this exactly once per
    cycle, instead of once per (title, location) combo - production
    logs showed the same ~1500-job Greenhouse/Lever/Ashby fetch
    repeated 3 times in a single cycle (once per combo), which is pure
    redundant load on those APIs and their rate limits for zero
    additional data."""
    all_jobs = []
    boards = resolve_boards()

    if boards["greenhouse"]:
        all_jobs.extend(greenhouse.fetch_multiple(boards["greenhouse"]))
    if boards["lever"]:
        all_jobs.extend(lever.fetch_multiple(boards["lever"]))
    if boards["ashby"]:
        all_jobs.extend(ashby.fetch_multiple(boards["ashby"]))

    logger.info(f"[ingest] fetched {len(all_jobs)} raw jobs from board sources (greenhouse/lever/ashby)")
    return all_jobs


def fetch_adzuna_jobs(search_query: str, search_location: str = "") -> list[dict]:
    """Pulls from Adzuna only - the one source that IS genuinely
    parameterized by query/location, so unlike the board sources it
    legitimately needs a separate fetch per distinct combo."""
    if not search_query:
        return []
    jobs = adzuna.fetch_jobs(query=search_query, location=search_location)
    logger.info(f"[ingest] fetched {len(jobs)} raw jobs from adzuna (query='{search_query}' location='{search_location}')")
    return jobs


def fetch_all_raw_jobs(search_query: str = "", search_location: str = "") -> list[dict]:
    """Pulls from every configured source in one combined call: board
    sources (not query-specific) plus Adzuna (which is). Used where a
    single self-contained fetch is exactly what's wanted - the manual
    /jobs/ingest trigger, /cron/ingest, and seed paths. The scheduler
    itself calls fetch_board_jobs()/fetch_adzuna_jobs() separately
    instead (see scheduler.py's scheduled_ingestion_job), specifically
    to avoid the per-combo board refetch described above."""
    all_jobs = fetch_board_jobs()
    all_jobs.extend(fetch_adzuna_jobs(search_query, search_location))
    logger.info(f"[ingest] fetched {len(all_jobs)} raw jobs from all sources")
    return all_jobs


def store_jobs(db: Session, raw_jobs: list[dict]) -> dict:
    """Inserts new jobs, skips duplicates (by hash), marks a fetch
    timestamp. Returns counts for observability/logging.

    For a job that already exists, this now also "touches" it -
    bumping fetched_at and flipping is_active back to True. That touch
    is what deactivate_missing_board_jobs() below relies on to tell
    "still posted, we saw it again this cycle" apart from "gone from
    the board, nobody touched it this cycle" - without it, expiry has
    no way to distinguish a job that's still open from one that's been
    pulled, since both would otherwise just sit there with is_active=True
    forever.

    Tracks `seen_hashes` for THIS batch, in addition to the DB-side
    exists check. That in-batch tracking is required, not optional:
    the session is configured with autoflush=False (see db.py), so a
    query issued mid-loop cannot see rows added earlier in the same
    loop that haven't been flushed/committed yet. A source can list the
    same posting twice under different IDs within one fetch (observed
    in production: Greenhouse returning the same Stripe role twice
    under two different gh_jid values, same title/company/location so
    the same hash) - without this local set, both duplicates pass the
    DB-side check (neither is committed yet), both get staged, and the
    final bulk INSERT fails on the unique constraint - which silently
    drops the ENTIRE batch (all ~1500 jobs that run), not just the
    duplicate pair, since it's one multi-row INSERT statement.
    """
    new_count = 0
    skipped_count = 0
    seen_hashes: set[str] = set()

    for j in raw_jobs:
        title = j.get("title", "").strip()
        company = j.get("company", "").strip()
        location = j.get("location", "").strip()
        if not title or not company:
            skipped_count += 1
            continue

        source = j.get("source", "unknown")
        external_id = j.get("external_id")
        job_hash = make_job_hash(title, company, location, source=source, external_id=external_id)
        if job_hash in seen_hashes:
            skipped_count += 1
            continue

        exists = db.query(Job).filter(Job.job_hash == job_hash).first()
        if exists:
            # Still posted as of this fetch - touch it so expiry (below)
            # knows not to deactivate it, and revive it if it had
            # previously been marked inactive and has now reappeared.
            exists.fetched_at = dt.datetime.utcnow()
            exists.is_active = True
            seen_hashes.add(job_hash)
            skipped_count += 1
            continue

        db_job = Job(
            job_hash=job_hash,
            external_id=external_id,
            title=title,
            company=company,
            location=location or "Unspecified",
            description=j.get("description", ""),
            apply_url=j.get("apply_url", ""),
            source=source,
            posted_date=dt.datetime.utcnow(),
            fetched_at=dt.datetime.utcnow(),
            is_active=True,
        )
        db.add(db_job)
        seen_hashes.add(job_hash)
        new_count += 1

    db.commit()
    logger.info(f"[ingest] stored {new_count} new jobs, skipped {skipped_count} duplicates/invalid")
    return {"new": new_count, "skipped": skipped_count, "total_fetched": len(raw_jobs)}


def deactivate_missing_board_jobs(db: Session, cutoff: dt.datetime) -> int:
    """Marks a board-sourced job inactive once it stops showing up in
    fetches. A Greenhouse/Lever/Ashby board token always returns that
    company's ENTIRE current board (see fetch_board_jobs), so if an
    active job from one of those sources has a fetched_at older than
    `cutoff` - i.e. store_jobs() didn't touch it during this cycle - it
    is no longer on the board and should stop being served.

    Deliberately excludes 'adzuna': that source is fetched per
    (query, location) combo rather than as one full snapshot, so
    "wasn't in this particular fetch" doesn't mean "no longer posted" -
    applying the same expiry there would incorrectly deactivate jobs
    that just weren't matched by the current search terms.
    """
    updated = (
        db.query(Job)
        .filter(Job.source.in_(["greenhouse", "lever", "ashby"]))
        .filter(Job.is_active == True)  # noqa: E712
        .filter(Job.fetched_at < cutoff)
        .update({"is_active": False}, synchronize_session=False)
    )
    db.commit()
    if updated:
        logger.info(f"[ingest] deactivated {updated} board jobs no longer present on their source board")
    return updated


def run_ingestion_cycle(db: Session, search_query: str = "", search_location: str = "") -> dict:
    """One full fetch-and-store cycle. This is what the scheduler calls
    on a timer (see app/scheduler.py).

    cutoff is captured BEFORE fetching, not after: store_jobs() bumps
    fetched_at on every job still present, so any board job whose
    fetched_at is still older than this pre-fetch cutoff once the cycle
    finishes genuinely wasn't seen this time around."""
    cutoff = dt.datetime.utcnow()
    raw_jobs = fetch_all_raw_jobs(search_query, search_location)
    result = store_jobs(db, raw_jobs)
    result["deactivated"] = deactivate_missing_board_jobs(db, cutoff)
    return result


def seed_sample_jobs(db: Session) -> dict:
    """Loads app/data/sample_jobs.json into the DB. Useful for a fresh
    install/demo before any real API keys (Adzuna) or board lists are
    configured - lets you test search/matching/notifications end to end
    with realistic-looking data and zero external calls."""
    try:
        with open(DATA_DIR / "sample_jobs.json") as f:
            sample_jobs = json.load(f)
    except Exception as e:
        logger.warning(f"[ingest] couldn't load sample_jobs.json: {e}")
        return {"new": 0, "skipped": 0, "total_fetched": 0}
    return store_jobs(db, sample_jobs)
