"""
Regression test for a production inefficiency: scheduled_ingestion_job()
used to call the full board fetch (Greenhouse/Lever/Ashby - identical
every time, not parameterized by query/location) once per distinct
(title, location) combo across all registered users. Production logs
showed the same ~1500-job fetch repeated 3 times in a single cycle for
3 locations of one title.

fetch_board_jobs() must now be called exactly ONCE per
scheduled_ingestion_job() run, regardless of how many combos exist;
only fetch_adzuna_jobs() (the source that's actually parameterized)
should scale with the number of combos.
"""
from unittest.mock import patch

from app.db import SessionLocal, UserProfile
from app.scheduler import scheduled_ingestion_job


def _make_profile(db, email, job_titles, locations):
    p = UserProfile(name="T", email=email, hashed_password="x", job_titles=job_titles, locations=locations)
    db.add(p)
    db.commit()
    return p


def test_board_jobs_fetched_once_regardless_of_combo_count(client):
    """3 users x multiple locations each = many combos, but the board
    fetch must still happen exactly once."""
    db = SessionLocal()
    try:
        _make_profile(db, "a@test.com", "python developer", "Hyderabad,Bangalore,Pune")
        _make_profile(db, "b@test.com", "python developer", "Delhi,Mumbai")
        _make_profile(db, "c@test.com", "java developer", "Hyderabad")

        with patch("app.scheduler.fetch_board_jobs", return_value=[]) as mock_board, \
             patch("app.scheduler.fetch_adzuna_jobs", return_value=[]) as mock_adzuna, \
             patch("app.scheduler.notify_new_matches_for_all_users"):
            scheduled_ingestion_job()

        assert mock_board.call_count == 1, f"board fetch should run exactly once per cycle, ran {mock_board.call_count} times"
        # 3 + 2 + 1 = 6 distinct (title, location) combos across the 3 profiles
        assert mock_adzuna.call_count == 6
    finally:
        db.close()


def test_board_jobs_fetched_even_with_zero_user_profiles(client):
    """Board refresh must not depend on any profiles existing - a
    fresh install with zero registered users should still populate the
    general pool from the board sources."""
    with patch("app.scheduler.fetch_board_jobs", return_value=[]) as mock_board, \
         patch("app.scheduler.fetch_adzuna_jobs", return_value=[]) as mock_adzuna, \
         patch("app.scheduler.notify_new_matches_for_all_users"):
        scheduled_ingestion_job()

    assert mock_board.call_count == 1
    assert mock_adzuna.call_count == 0  # no profiles -> no combos -> no adzuna calls
