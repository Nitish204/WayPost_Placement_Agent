"""
Bridges matching + notifications: after every ingestion cycle, this
scores each user's profile against the current active job pool, records
new scores in MatchResult (dedup key: user_id + job_id), and sends a
single batched email/Telegram message per user for any match that:
  1. is new (no MatchResult row yet), AND
  2. scores at or above that user's match_score_threshold, AND
  3. hasn't already been notified (MatchResult.notified == False)

This turns the previously pull-only matcher into a push notifier
without duplicating the ranking logic in matcher.py.
"""
import logging
from sqlalchemy.orm import Session

from app.db import Job, UserProfile, MatchResult
from app.core.matcher import find_matches
from app.core.notifier import (
    send_email, send_telegram, format_match_email, format_match_telegram,
)

logger = logging.getLogger(__name__)


def notify_new_matches_for_all_users(db: Session) -> dict:
    users = db.query(UserProfile).all()
    active_jobs = db.query(Job).filter(Job.is_active == True).all()  # noqa: E712
    job_by_id = {j.id: j for j in active_jobs}

    job_dicts = [
        {"id": j.id, "title": j.title, "company": j.company, "location": j.location,
         "description": j.description, "apply_url": j.apply_url, "source": j.source}
        for j in active_jobs
    ]

    total_notified_users = 0
    total_matches_sent = 0

    for user in users:
        titles = [t.strip() for t in (user.job_titles or "").split(",") if t.strip()]
        locations = [l.strip() for l in (user.locations or "").split(",") if l.strip()]
        if not titles:
            continue

        ranked = find_matches(job_dicts, titles, locations, user.resume_text or "", top_k=50)

        new_matches = []
        for job in ranked:
            job_id = job["id"]
            score = job["match_score"]
            if score < user.match_score_threshold:
                continue

            existing = db.query(MatchResult).filter(
                MatchResult.user_id == user.id, MatchResult.job_id == job_id
            ).first()
            if existing:
                continue  # already scored/seen before, don't re-notify

            mr = MatchResult(user_id=user.id, job_id=job_id, score=score, notified=False)
            db.add(mr)
            new_matches.append({**job, "score": score})

        db.commit()

        if not new_matches:
            continue

        sent_any = False
        if user.notify_email and user.email:
            subject, html = format_match_email(user.name or "there", new_matches)
            email_sent, _ = send_email(user.email, subject, html)
            sent_any = email_sent or sent_any
        if user.notify_telegram and user.telegram_chat_id:
            text = format_match_telegram(new_matches)
            sent_any = send_telegram(user.telegram_chat_id, text) or sent_any

        if sent_any:
            db.query(MatchResult).filter(
                MatchResult.user_id == user.id,
                MatchResult.job_id.in_([j["id"] for j in new_matches]),
            ).update({"notified": True}, synchronize_session=False)
            db.commit()
            total_notified_users += 1
            total_matches_sent += len(new_matches)
            logger.info(f"[matching_notify] notified user={user.id} of {len(new_matches)} new matches")

    return {"users_notified": total_notified_users, "matches_sent": total_matches_sent}
