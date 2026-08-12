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
from app.sources import greenhouse, lever, adzuna

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
        }
    except Exception as e:
        logger.warning(f"[ingest] couldn't load fallback companies.json: {e}")
        return {"greenhouse": [], "lever": []}


def fetch_all_raw_jobs(search_query: str = "", search_location: str = "") -> list[dict]:
    """Pulls from every configured source. Each source function is
    independently fault-tolerant (returns [] on failure) so one bad
    source never blocks the others."""
    all_jobs = []
    fallback = _fallback_boards()

    gh_boards = [b for b in os.getenv("GREENHOUSE_BOARDS", "").split(",") if b.strip()]
    if not gh_boards:
        gh_boards = fallback["greenhouse"]
        if gh_boards:
            logger.info(f"[ingest] GREENHOUSE_BOARDS not set, using fallback list: {gh_boards}")
    if gh_boards:
        all_jobs.extend(greenhouse.fetch_multiple(gh_boards))

    lever_boards = [b for b in os.getenv("LEVER_BOARDS", "").split(",") if b.strip()]
    if not lever_boards:
        lever_boards = fallback["lever"]
        if lever_boards:
            logger.info(f"[ingest] LEVER_BOARDS not set, using fallback list: {lever_boards}")
    if lever_boards:
        all_jobs.extend(lever.fetch_multiple(lever_boards))

    if search_query:
        all_jobs.extend(adzuna.fetch_jobs(query=search_query, location=search_location))

    logger.info(f"[ingest] fetched {len(all_jobs)} raw jobs from all sources")
    return all_jobs


def store_jobs(db: Session, raw_jobs: list[dict]) -> dict:
    """Inserts new jobs, skips duplicates (by hash), marks a fetch
    timestamp. Returns counts for observability/logging."""
    new_count = 0
    skipped_count = 0

    for j in raw_jobs:
        title = j.get("title", "").strip()
        company = j.get("company", "").strip()
        location = j.get("location", "").strip()
        if not title or not company:
            skipped_count += 1
            continue

        job_hash = make_job_hash(title, company, location)
        exists = db.query(Job).filter(Job.job_hash == job_hash).first()
        if exists:
            skipped_count += 1
            continue

        db_job = Job(
            job_hash=job_hash,
            title=title,
            company=company,
            location=location or "Unspecified",
            description=j.get("description", ""),
            apply_url=j.get("apply_url", ""),
            source=j.get("source", "unknown"),
            posted_date=dt.datetime.utcnow(),
            fetched_at=dt.datetime.utcnow(),
            is_active=True,
        )
        db.add(db_job)
        new_count += 1

    db.commit()
    logger.info(f"[ingest] stored {new_count} new jobs, skipped {skipped_count} duplicates/invalid")
    return {"new": new_count, "skipped": skipped_count, "total_fetched": len(raw_jobs)}


def run_ingestion_cycle(db: Session, search_query: str = "", search_location: str = "") -> dict:
    """One full fetch-and-store cycle. This is what the scheduler calls
    on a timer (see app/scheduler.py)."""
    raw_jobs = fetch_all_raw_jobs(search_query, search_location)
    return store_jobs(db, raw_jobs)


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
