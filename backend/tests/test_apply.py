"""
Tests for the auto-apply feature (POST /apply/prepare, /apply/{id}/confirm,
/apply/{id}/reject, GET /applications) and the board-health check
(GET /admin/board-health).

Playwright's browser binary isn't assumed to be installed in every test
environment (CI containers, sandboxes without network access to
download Chromium), so these tests deliberately verify the *contract*
around the browser agent - auth, ownership, status transitions,
idempotency - rather than requiring a live browser launch to pass.
apply_agent.prepare_application/confirm_submit already fail closed
(return {"ok": False, "reason": ...}) rather than raising when the
browser can't launch, so a missing Chromium binary shows up here as a
'failed' Application row, not a test crash - see test_apply_prepare_*
below, which assert on that exact behavior.
"""
from unittest.mock import patch

from tests.test_main import register, login, auth_headers


def _seed_and_get_job_id(client, headers):
    client.post("/jobs/seed-sample", headers=headers)
    from app.db import SessionLocal, Job
    db = SessionLocal()
    job = db.query(Job).first()
    job_id = job.id
    db.close()
    return job_id


# ------------------------- /admin/board-health -------------------------

def test_board_health_requires_login(client):
    r = client.get("/admin/board-health")
    assert r.status_code == 401


def test_board_health_returns_results_and_stale_shape(client):
    register(client)
    token = login(client, "test@test.com")
    r = client.get("/admin/board-health", headers=auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert "results" in data and "stale" in data
    assert isinstance(data["stale"], list)


# ------------------------- /apply/prepare -------------------------

def test_apply_prepare_requires_login(client):
    r = client.post("/apply/prepare", data={"job_id": 1})
    assert r.status_code == 401


def test_apply_prepare_404_for_unknown_job(client):
    register(client)
    token = login(client, "test@test.com")
    r = client.post("/apply/prepare", data={"job_id": 999999}, headers=auth_headers(token))
    assert r.status_code == 404


def test_apply_prepare_creates_application_row(client):
    """Whether or not a real browser is available in this environment,
    /apply/prepare must always return a well-formed Application row -
    'pending_approval' with a screenshot if the browser agent
    succeeded, or 'failed' with an error_message if it didn't. Never a
    500, and never nothing."""
    register(client)
    token = login(client, "test@test.com")
    headers = auth_headers(token)
    job_id = _seed_and_get_job_id(client, headers)

    r = client.post("/apply/prepare", data={"job_id": job_id, "phone": "9999999999"}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("pending_approval", "failed")
    if data["status"] == "failed":
        assert data["error_message"]  # must explain why, not fail silently
    else:
        assert data["preview_screenshot_b64"]


def test_apply_prepare_is_idempotent_per_user_and_job(client):
    """Calling prepare twice for the same (user, job) must return the
    SAME application, not spin up a second browser session / create a
    duplicate pending_approval row."""
    register(client)
    token = login(client, "test@test.com")
    headers = auth_headers(token)
    job_id = _seed_and_get_job_id(client, headers)

    first = client.post("/apply/prepare", data={"job_id": job_id}, headers=headers).json()
    second = client.post("/apply/prepare", data={"job_id": job_id}, headers=headers).json()
    assert first["id"] == second["id"]

    listing = client.get("/applications", headers=headers).json()
    assert listing["count"] == 1


def test_apply_prepare_with_mocked_browser_success(client):
    """Mocks apply_agent.prepare_application so this test exercises the
    route's own logic (row creation, field shape, idempotency key) in
    complete isolation from Playwright/network, independent of whether
    a real browser binary is installed."""
    register(client)
    token = login(client, "test@test.com")
    headers = auth_headers(token)
    job_id = _seed_and_get_job_id(client, headers)

    fake_result = {
        "ok": True,
        "filled_fields": [{"field": "email", "label": "Email"}],
        "screenshot_b64": "ZmFrZS1wbmctYnl0ZXM=",
    }
    with patch("app.main.prepare_application", return_value=fake_result):
        r = client.post("/apply/prepare", data={"job_id": job_id}, headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "pending_approval"
    assert data["filled_fields"] == fake_result["filled_fields"]
    assert data["preview_screenshot_b64"] == fake_result["screenshot_b64"]


# ------------------------- /apply/{id}/confirm and /reject -------------------------

def test_confirm_rejects_application_not_owned_by_caller(client):
    register(client, email="a@test.com")
    token_a = login(client, "a@test.com")
    job_id = _seed_and_get_job_id(client, auth_headers(token_a))

    with patch("app.main.prepare_application", return_value={"ok": True, "filled_fields": [], "screenshot_b64": "x"}):
        app_data = client.post("/apply/prepare", data={"job_id": job_id}, headers=auth_headers(token_a)).json()

    register(client, email="b@test.com")
    token_b = login(client, "b@test.com")
    r = client.post(f"/apply/{app_data['id']}/confirm", headers=auth_headers(token_b))
    assert r.status_code == 404  # not found FOR THIS USER, not leaked as 403 (avoids confirming existence)


def test_confirm_only_allowed_from_pending_approval(client):
    register(client)
    token = login(client, "test@test.com")
    headers = auth_headers(token)
    job_id = _seed_and_get_job_id(client, headers)

    # Force a 'failed' row (no mock -> real prepare_application call,
    # which fails closed if no browser binary is present; if a browser
    # IS present in this environment, mock instead so the test is
    # deterministic either way).
    with patch("app.main.prepare_application", return_value={"ok": False, "reason": "simulated failure"}):
        app_data = client.post("/apply/prepare", data={"job_id": job_id}, headers=headers).json()
    assert app_data["status"] == "failed"

    r = client.post(f"/apply/{app_data['id']}/confirm", headers=headers)
    assert r.status_code == 400  # can't confirm something that was never previewed successfully


def test_confirm_success_path_with_mocked_browser(client):
    register(client)
    token = login(client, "test@test.com")
    headers = auth_headers(token)
    job_id = _seed_and_get_job_id(client, headers)

    with patch("app.main.prepare_application", return_value={"ok": True, "filled_fields": [], "screenshot_b64": "x"}):
        app_data = client.post("/apply/prepare", data={"job_id": job_id}, headers=headers).json()
    assert app_data["status"] == "pending_approval"

    with patch("app.main.confirm_submit", return_value={"ok": True, "confirmation_b64": "y"}):
        r = client.post(f"/apply/{app_data['id']}/confirm", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "submitted"
    assert data["confirmation_screenshot_b64"] == "y"


def test_reject_discards_without_submitting(client):
    register(client)
    token = login(client, "test@test.com")
    headers = auth_headers(token)
    job_id = _seed_and_get_job_id(client, headers)

    with patch("app.main.prepare_application", return_value={"ok": True, "filled_fields": [], "screenshot_b64": "x"}):
        app_data = client.post("/apply/prepare", data={"job_id": job_id}, headers=headers).json()

    with patch("app.main.confirm_submit") as mock_confirm:
        r = client.post(f"/apply/{app_data['id']}/reject", headers=headers)
        assert r.status_code == 200
        mock_confirm.assert_not_called()  # the actual point of this test: reject must never touch the submit path

    listing = client.get("/applications", headers=headers).json()
    assert listing["applications"][0]["status"] == "rejected"


# ------------------------- GET /applications -------------------------

def test_applications_listing_is_scoped_to_caller(client):
    register(client, email="a@test.com")
    token_a = login(client, "a@test.com")
    job_id = _seed_and_get_job_id(client, auth_headers(token_a))
    with patch("app.main.prepare_application", return_value={"ok": True, "filled_fields": [], "screenshot_b64": "x"}):
        client.post("/apply/prepare", data={"job_id": job_id}, headers=auth_headers(token_a))

    register(client, email="b@test.com")
    token_b = login(client, "b@test.com")
    r = client.get("/applications", headers=auth_headers(token_b))
    assert r.json()["count"] == 0  # user B must not see user A's applications
