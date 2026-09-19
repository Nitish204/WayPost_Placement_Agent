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
import datetime as dt
from apscheduler.schedulers.background import BackgroundScheduler

from app.db import SessionLocal, UserProfile
from app.core.ingest import fetch_board_jobs, fetch_adzuna_jobs, store_jobs
from app.core.matching_notify import notify_new_matches_for_all_users
from app.core.board_validator import check_and_alert_boards

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def scheduled_ingestion_job():
    """Runs one ingestion cycle per distinct search combo currently
    requested by registered users, so continuous fetching stays
    relevant to what people actually asked for (rather than blindly
    pulling everything). After ingestion, checks every user's profile
    against the (possibly new) job pool and sends email/Telegram alerts
    for any new high-scoring matches - this is what makes the agent
    proactively reach out instead of only responding when asked.

    Board sources (Greenhouse/Lever/Ashby) are fetched exactly ONCE per
    cycle - not once per combo - since they aren't parameterized by
    query/location at all; refetching them per combo was pure
    redundant load with zero additional data (observed in production:
    the same ~1500-job fetch repeated 3 times in one cycle for 3
    locations of the same title). Only Adzuna, which genuinely is
    parameterized by query/location, gets fetched per distinct combo.
    """
    db = SessionLocal()
    try:
        board_jobs = fetch_board_jobs()
        board_result = store_jobs(db, board_jobs)
        logger.info(f"[scheduler] board refresh (greenhouse/lever/ashby): {board_result}")

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
                    logger.info(f"[scheduler] adzuna search for query='{combo[0]}' location='{combo[1]}'")
                    adzuna_jobs = fetch_adzuna_jobs(combo[0], combo[1])
                    store_jobs(db, adzuna_jobs)

        notify_new_matches_for_all_users(db)
    finally:
        db.close()


def scheduled_board_health_job():
    """Periodic, alerting version of GET /admin/board-health. Runs far
    less often than ingestion (default every 6 hours, see
    start_scheduler) since it's a diagnostic check, not something that
    needs to keep pace with the job pool refresh - and unlike
    ingestion, checking 15-ish tokens with an 8s timeout each has real
    latency (worst case ~2 minutes) that shouldn't be tacked onto every
    hourly ingestion run."""
    db = SessionLocal()
    try:
        result = check_and_alert_boards(db)
        if result["newly_stale"]:
            logger.error(f"[scheduler] board health check found new stale tokens: {result['newly_stale']}")
        else:
            logger.info("[scheduler] board health check - no new stale tokens")
    finally:
        db.close()


def start_scheduler(interval_minutes: int = 60, board_health_interval_minutes: int = 360):
    """Starts the background scheduler. Call once at app startup.

    Runs two independent interval jobs:
      - job_ingestion: fetches new postings (default hourly)
      - board_health_check: checks board tokens and alerts on newly
        stale ones (default every 6 hours - see scheduled_board_health_job
        for why this runs on its own, slower cadence)

    Both get next_run_time=now so they fire immediately in the
    background at startup rather than waiting a full interval before
    doing anything - this was the direct cause of a fresh install/deploy
    showing an empty pool for up to an hour, which is exactly the gap
    the "Load sample jobs" button was being used to paper over.

    Both run in APScheduler's own background thread, so neither blocks
    app startup or the first incoming request.
    """
    scheduler.add_job(
        scheduled_ingestion_job,
        "interval",
        minutes=interval_minutes,
        id="job_ingestion",
        replace_existing=True,
        next_run_time=dt.datetime.now(),
    )
    scheduler.add_job(
        scheduled_board_health_job,
        "interval",
        minutes=board_health_interval_minutes,
        id="board_health_check",
        replace_existing=True,
        next_run_time=dt.datetime.now(),
    )
    scheduler.start()
    logger.info(
        f"[scheduler] started - immediate ingestion + board health check queued, "
        f"then polling every {interval_minutes}m (ingestion) / {board_health_interval_minutes}m (board health)"
    )


def stop_scheduler():
    """Guarded: calling shutdown() on a scheduler that was never
    started (DISABLE_SCHEDULER=1, e.g. during tests) raises
    SchedulerNotRunningError - `.running` check avoids that instead of
    letting every test's teardown log a spurious exception."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
