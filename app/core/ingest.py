"""
Ingestion pipeline: pulls jobs from all configured sources, dedups them
against what's already in the DB, and inserts new ones. This is the
function the scheduler calls repeatedly to keep the job pool fresh -
this is what makes the agent "continuously work" rather than only
searching on-demand.
"""
import os
import logging
import datetime as dt
from sqlalchemy.orm import Session

from app.db import Job, make_job_hash
from app.sources import greenhouse, lever, adzuna

logger = logging.getLogger(__name__)


def fetch_all_raw_jobs(search_query: str = "", search_location: str = "") -> list[dict]:
    """Pulls from every configured source. Each source function is
    independently fault-tolerant (returns [] on failure) so one bad
    source never blocks the others."""
    all_jobs = []

    gh_boards = [b for b in os.getenv("GREENHOUSE_BOARDS", "").split(",") if b.strip()]
    if gh_boards:
        all_jobs.extend(greenhouse.fetch_multiple(gh_boards))

    lever_boards = [b for b in os.getenv("LEVER_BOARDS", "").split(",") if b.strip()]
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
