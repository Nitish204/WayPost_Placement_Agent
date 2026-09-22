"""
Regression test: /jobs/search's underlying query had no ORDER BY,
which doesn't guarantee consistent row order between calls in SQL -
this was reported as "the same jobs, just shuffled/reversed" on
repeated searches even when the job pool hadn't actually changed.
Newest-first (by fetched_at) is now explicit, not incidental.
"""
import datetime as dt

from app.db import SessionLocal, Job, make_job_hash
from tests.test_main import register, login, auth_headers


def _make_job(db, title, fetched_at):
    job = Job(
        job_hash=make_job_hash(title, "Acme", "Remote"),
        title=title, company="Acme", location="Remote",
        description="", apply_url="https://example.com/apply",
        source="greenhouse", is_active=True, fetched_at=fetched_at,
    )
    db.add(job)
    return job


def test_search_results_are_ordered_newest_first_regardless_of_insertion_order(client):
    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        # Insert deliberately OUT of chronological order, so a test
        # that passed only because SQLite happened to preserve
        # insertion order wouldn't actually prove anything.
        _make_job(db, "Oldest Role", fetched_at=now - dt.timedelta(days=2))
        _make_job(db, "Newest Role", fetched_at=now)
        _make_job(db, "Middle Role", fetched_at=now - dt.timedelta(days=1))
        db.commit()
    finally:
        db.close()

    register(client)
    token = login(client, "test@test.com")
    r = client.post("/jobs/search", data={"job_titles": "Role", "locations": "Remote", "top_k": 10}, headers=auth_headers(token))
    titles_in_order = [j["title"] for j in r.json()["jobs"]]

    # match_score ranking can reorder ties, but with identical/near-identical
    # description text (all empty here) TF-IDF similarity ties out, so the
    # pre-ranking DB order (newest fetched_at first) should be preserved
    # through the stable sort - this is what actually catches a regression
    # to the old undefined-order query.
    assert titles_in_order.index("Newest Role") < titles_in_order.index("Middle Role") < titles_in_order.index("Oldest Role")


def test_search_results_are_stable_across_repeated_calls(client):
    """The direct complaint: calling search twice with no data change
    must return the same order both times, not appear shuffled."""
    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        for i in range(5):
            _make_job(db, f"Role {i}", fetched_at=now - dt.timedelta(minutes=i))
        db.commit()
    finally:
        db.close()

    register(client)
    token = login(client, "test@test.com")
    headers = auth_headers(token)

    r1 = client.post("/jobs/search", data={"job_titles": "Role", "locations": "Remote", "top_k": 10}, headers=headers)
    r2 = client.post("/jobs/search", data={"job_titles": "Role", "locations": "Remote", "top_k": 10}, headers=headers)

    order_1 = [j["title"] for j in r1.json()["jobs"]]
    order_2 = [j["title"] for j in r2.json()["jobs"]]
    assert order_1 == order_2
