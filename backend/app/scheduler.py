"""
Scheduler: this is what makes the agent "continuously work" instead of
only fetching jobs when a user asks. Runs the ingestion cycle on a
fixed interval in the background, for every distinct (job_title,
location) combination that active users have registered.

For an MVP, APScheduler running inside the same process is enough.
For real production scale (many users, frequent polling), move this to
Celery beat + workers, or a serverless cron (e.g. AWS EventBridge ->
Lambda) so ingestion doesn't compete with API request handling.
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler

from app.db import SessionLocal, UserProfile
from app.core.ingest import run_ingestion_cycle
from app.core.matching_notify import notify_new_matches_for_all_users

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def scheduled_ingestion_job():
    """Runs one ingestion cycle per distinct search combo currently
    requested by registered users, so continuous fetching stays
    relevant to what people actually asked for (rather than blindly
    pulling everything). After ingestion, checks every user's profile
    against the (possibly new) job pool and sends email/Telegram alerts
    for any new high-scoring matches - this is what makes the agent
    proactively reach out instead of only responding when asked."""
    db = SessionLocal()
    try:
        profiles = db.query(UserProfile).all()
        seen_combos = set()
        for p in profiles:
            titles = (p.job_titles or "").split(",")
            locations = (p.locations or "").split(",")
            for title in titles:
                for loc in locations:
                    combo = (title.strip(), loc.strip())
                    if combo in seen_combos or not combo[0]:
                        continue
                    seen_combos.add(combo)
                    logger.info(f"[scheduler] ingesting for query='{combo[0]}' location='{combo[1]}'")
                    run_ingestion_cycle(db, search_query=combo[0], search_location=combo[1])

        # Always also refresh the static Greenhouse/Lever boards even if
        # no user profile matches them directly - keeps the general pool fresh.
        if not profiles:
            run_ingestion_cycle(db)

        notify_new_matches_for_all_users(db)
    finally:
        db.close()


def start_scheduler(interval_minutes: int = 60):
    """Starts the background scheduler. Call once at app startup."""
    scheduler.add_job(
        scheduled_ingestion_job,
        "interval",
        minutes=interval_minutes,
        id="job_ingestion",
        replace_existing=True,
        next_run_time=None,  # will run after the first interval; call once manually at startup if you want immediate results
    )
    scheduler.start()
    logger.info(f"[scheduler] started - polling every {interval_minutes} minutes")


def stop_scheduler():
    scheduler.shutdown(wait=False)
