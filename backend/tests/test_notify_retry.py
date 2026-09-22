"""
Regression test for a real production bug: a user who links Telegram
AFTER their first match-scoring cycle never got notified for matches
that were scored before the link, because the dedup check treated
"a MatchResult row exists" as equivalent to "already notified" -
those are different things. notify_new_matches_for_all_users() must
retry a match whose MatchResult exists but has notified=False, not
skip it forever.
"""
from unittest.mock import patch

from app.db import SessionLocal, Job, UserProfile, MatchResult, make_job_hash
from app.core.matching_notify import notify_new_matches_for_all_users


def _make_job(db, title="Python Developer", company="Acme", location="Remote"):
    job = Job(
        job_hash=make_job_hash(title, company, location),
        title=title, company=company, location=location,
        description="", apply_url="https://example.com/apply",
        source="greenhouse", is_active=True,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _make_user(db, email="t@test.com", job_titles="Python Developer", locations="Remote",
                telegram_chat_id=None, match_score_threshold=0.0):
    user = UserProfile(
        name="T", email=email, hashed_password="x",
        job_titles=job_titles, locations=locations,
        telegram_chat_id=telegram_chat_id, notify_telegram=True,
        match_score_threshold=match_score_threshold,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_match_scored_before_telegram_linked_is_retried_after_linking(client):
    """The exact bug scenario: MatchResult created with notified=False
    while telegram_chat_id was still None, THEN the user links
    Telegram, THEN the next notify cycle runs - it must send this time,
    not skip it as 'already seen'."""
    db = SessionLocal()
    try:
        job = _make_job(db)
        user = _make_user(db, telegram_chat_id=None)  # not linked yet

        # Simulate the first cycle running before Telegram was linked -
        # this creates the MatchResult with notified=False and nothing
        # to send to, exactly as the real scheduler would.
        with patch("app.core.matching_notify.send_telegram") as mock_send_1:
            result_1 = notify_new_matches_for_all_users(db)
        mock_send_1.assert_not_called()
        assert result_1["users_notified"] == 0

        mr = db.query(MatchResult).filter(MatchResult.user_id == user.id, MatchResult.job_id == job.id).first()
        assert mr is not None
        assert mr.notified is False

        # Now the user links Telegram
        user.telegram_chat_id = "123456"
        db.commit()

        # Next cycle must retry this match, not skip it
        with patch("app.core.matching_notify.send_telegram", return_value=True) as mock_send_2:
            result_2 = notify_new_matches_for_all_users(db)
        mock_send_2.assert_called_once()
        assert result_2["users_notified"] == 1
        assert result_2["matches_sent"] == 1

        db.refresh(mr)
        assert mr.notified is True
    finally:
        db.close()


def test_genuinely_already_notified_match_is_never_resent(client):
    """The other half of the fix: a match that WAS successfully
    notified must never be resent on a later cycle, even though the
    retry logic now looks at more than just 'does a row exist'."""
    db = SessionLocal()
    try:
        job = _make_job(db)
        user = _make_user(db, telegram_chat_id="123456")

        with patch("app.core.matching_notify.send_telegram", return_value=True) as mock_send_1:
            notify_new_matches_for_all_users(db)
        mock_send_1.assert_called_once()

        with patch("app.core.matching_notify.send_telegram") as mock_send_2:
            result_2 = notify_new_matches_for_all_users(db)
        mock_send_2.assert_not_called()
        assert result_2["users_notified"] == 0
    finally:
        db.close()


def test_new_match_with_telegram_already_linked_still_works(client):
    """Baseline regression: the simple, common case (Telegram already
    linked before any matching happens) must still work exactly as
    before this fix."""
    db = SessionLocal()
    try:
        job = _make_job(db)
        user = _make_user(db, telegram_chat_id="123456")

        with patch("app.core.matching_notify.send_telegram", return_value=True) as mock_send:
            result = notify_new_matches_for_all_users(db)

        mock_send.assert_called_once()
        assert result["users_notified"] == 1
        assert result["matches_sent"] == 1
    finally:
        db.close()
