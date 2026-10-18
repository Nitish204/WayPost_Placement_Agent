"""
The "Load sample jobs" feature was removed permanently. These tests make
sure it stays gone and that databases seeded by the old button get cleaned.
"""
from tests.test_main import register, login, auth_headers


def test_seed_sample_endpoint_is_gone(client):
    register(client)
    token = login(client, "test@test.com")
    r = client.post("/jobs/seed-sample", headers=auth_headers(token))
    assert r.status_code in (404, 405)


def test_purge_removes_leftover_sample_rows_and_dependents(client):
    from app.db import SessionLocal, Job, MatchResult, Application
    from app.core.ingest import purge_sample_jobs

    db = SessionLocal()
    sample = Job(job_hash="s1", title="Intern", company="Sample Corp", location="Remote",
                 description="x", apply_url="https://example.com/s", source="sample_data")
    real = Job(job_hash="r1", title="Engineer", company="Acme", location="Remote",
               description="x", apply_url="https://example.com/r", source="greenhouse")
    db.add_all([sample, real])
    db.commit()
    db.add(MatchResult(user_id=1, job_id=sample.id, score=80))
    db.add(Application(user_id=1, job_id=sample.id, idempotency_key="k1"))
    db.commit()
    real_id = real.id

    assert purge_sample_jobs(db) == 1
    assert db.query(Job).filter(Job.source == "sample_data").count() == 0
    assert db.query(MatchResult).count() == 0
    assert db.query(Application).count() == 0
    assert db.query(Job).filter(Job.id == real_id).count() == 1  # real jobs untouched
    assert purge_sample_jobs(db) == 0  # idempotent
    db.close()
