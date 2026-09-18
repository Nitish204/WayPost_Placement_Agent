"""
Regression test for a production incident: a single ingestion batch
containing two raw jobs that normalize to the same job_hash (observed
with Greenhouse returning the same Stripe posting twice under two
different gh_jid values) crashed the entire batch with a
sqlalchemy.exc.IntegrityError on the jobs.job_hash unique constraint,
because SessionLocal is configured with autoflush=False (see db.py) -
the DB-side duplicate check inside store_jobs() can't see a row added
earlier in the same loop that hasn't been flushed yet.

store_jobs() now tracks seen_hashes locally within the batch, in
addition to the DB-side check, specifically to catch this case.
"""
from app.db import SessionLocal, Job
from app.core.ingest import store_jobs


def test_store_jobs_handles_intra_batch_duplicate_hash(client):
    """Two raw jobs with identical (title, company, location) - hence
    identical job_hash - arriving in the SAME store_jobs() call must
    not raise, and only one of them should be stored."""
    db = SessionLocal()
    try:
        raw_jobs = [
            {"title": "Abuse Investigator", "company": "stripe", "location": "Dublin",
             "description": "posting A", "apply_url": "https://stripe.com/jobs/search?gh_jid=8172487",
             "source": "greenhouse"},
            {"title": "Abuse Investigator", "company": "stripe", "location": "Dublin",
             "description": "posting B - same role, different gh_jid, real-world duplicate",
             "apply_url": "https://stripe.com/jobs/search?gh_jid=8172508", "source": "greenhouse"},
        ]

        result = store_jobs(db, raw_jobs)  # must not raise IntegrityError

        assert result["new"] == 1
        assert result["skipped"] == 1
        assert db.query(Job).filter(Job.company == "stripe", Job.title == "Abuse Investigator").count() == 1
    finally:
        db.close()


def test_store_jobs_handles_larger_batch_with_multiple_duplicate_pairs(client):
    """Same failure mode, but closer to production scale/shape: several
    distinct duplicate pairs mixed into one larger batch, to confirm
    the fix isn't order-dependent or limited to a single pair."""
    db = SessionLocal()
    try:
        raw_jobs = []
        for i in range(5):
            raw_jobs.append({"title": f"Engineer {i}", "company": "acme", "location": "Remote",
                              "description": "x", "apply_url": f"https://acme.com/{i}a", "source": "lever"})
            raw_jobs.append({"title": f"Engineer {i}", "company": "acme", "location": "Remote",
                              "description": "duplicate of above under a different posting id",
                              "apply_url": f"https://acme.com/{i}b", "source": "lever"})

        result = store_jobs(db, raw_jobs)  # must not raise

        assert result["new"] == 5
        assert result["skipped"] == 5
        assert db.query(Job).filter(Job.company == "acme").count() == 5
    finally:
        db.close()
