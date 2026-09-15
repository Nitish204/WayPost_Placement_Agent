"""
Test suite for the Waypost backend. Covers real bugs found and fixed
during this session:

  - test_logout_everywhere_*        -> tokens that couldn't be revoked
  - test_password_reset_revokes_*   -> reset didn't kill old sessions
  - test_upload_resume_rejects_fake_* -> extension-only file validation
  - test_agent_chat_schema (see note) -> the Gemini "default" field crash
    isn't re-tested here since it requires a real GEMINI_API_KEY and
    live API call - covered instead by the unit-level schema test in
    test_llm_schema.py.

Run with: pytest tests/ -v
"""


def register(client, email="test@test.com", password="testpass123", **overrides):
    data = {
        "name": "Test User", "email": email, "password": password,
        "security_question": "first pet", "security_answer": "fluffy",
        "job_titles": "Engineer", "locations": "Remote",
    }
    data.update(overrides)
    return client.post("/auth/register", data=data)


def login(client, email, password="testpass123"):
    res = client.post("/auth/login", data={"email": email, "password": password})
    return res.json()["access_token"] if res.status_code == 200 else None


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------- Auth basics -------------------------

def test_register_then_login(client):
    reg = register(client)
    assert reg.status_code == 200
    assert "access_token" in reg.json()

    token = login(client, "test@test.com")
    assert token is not None


def test_login_wrong_password_rejected(client):
    register(client)
    res = client.post("/auth/login", data={"email": "test@test.com", "password": "wrongpass"})
    assert res.status_code == 401


def test_protected_route_rejects_no_token(client):
    res = client.get("/auth/me")
    assert res.status_code == 401


def test_me_returns_correct_profile(client):
    register(client)
    token = login(client, "test@test.com")
    res = client.get("/auth/me", headers=auth_headers(token))
    assert res.status_code == 200
    assert res.json()["email"] == "test@test.com"


# ------------------------- JWT revocation (the fix for issue #9) -------------------------

def test_logout_everywhere_invalidates_the_token_used_to_call_it(client):
    register(client)
    token = login(client, "test@test.com")

    assert client.get("/auth/me", headers=auth_headers(token)).status_code == 200

    revoke_res = client.post("/auth/logout_everywhere", headers=auth_headers(token))
    assert revoke_res.status_code == 200

    # The exact same token must now be rejected
    assert client.get("/auth/me", headers=auth_headers(token)).status_code == 401


def test_fresh_login_works_after_logout_everywhere(client):
    register(client)
    old_token = login(client, "test@test.com")
    client.post("/auth/logout_everywhere", headers=auth_headers(old_token))

    new_token = login(client, "test@test.com")
    assert client.get("/auth/me", headers=auth_headers(new_token)).status_code == 200


def test_password_reset_revokes_old_sessions(client):
    """Regression test: a password reset used to leave any existing
    tokens for that account still valid - meaning resetting your
    password because a token leaked wouldn't actually stop the leaked
    token from working."""
    register(client)
    old_token = login(client, "test@test.com")
    assert client.get("/auth/me", headers=auth_headers(old_token)).status_code == 200

    reset_res = client.post("/auth/reset-with-security-answer", data={
        "email": "test@test.com", "security_answer": "fluffy", "new_password": "newpassword456",
    })
    assert reset_res.status_code == 200

    # The token from before the reset must now be dead
    assert client.get("/auth/me", headers=auth_headers(old_token)).status_code == 401

    # Logging in with the NEW password must work
    new_token = login(client, "test@test.com", password="newpassword456")
    assert new_token is not None
    assert client.get("/auth/me", headers=auth_headers(new_token)).status_code == 200


# ------------------------- File upload validation (the fix for issue #6) -------------------------

def test_upload_resume_rejects_fake_pdf(client):
    """Regression test: a plain-text file renamed to .pdf used to pass
    the old extension-only check."""
    register(client)
    token = login(client, "test@test.com")

    fake_pdf = ("resume.pdf", b"This is not really a PDF", "application/pdf")
    res = client.post("/resume/upload", headers=auth_headers(token), files={"file": fake_pdf})
    assert res.status_code == 400


def test_upload_resume_malformed_pdf_returns_clean_error_not_crash(client):
    """A file can have the genuine %PDF- header (passes the signature
    check) while still being structurally broken - this used to be an
    unhandled PDFSyntaxError -> 500. Found by writing this exact test."""
    register(client)
    token = login(client, "test@test.com")

    malformed_pdf = ("resume.pdf", b"%PDF-1.4\nnot a real, complete PDF structure", "application/pdf")
    res = client.post("/resume/upload", headers=auth_headers(token), files={"file": malformed_pdf})
    assert res.status_code == 400  # clean error, not a 500 crash
    assert "detail" in res.json()


def test_upload_resume_rejects_unsupported_extension(client):
    register(client)
    token = login(client, "test@test.com")

    bad_file = ("resume.exe", b"MZ fake exe header", "application/octet-stream")
    res = client.post("/resume/upload", headers=auth_headers(token), files={"file": bad_file})
    assert res.status_code == 400
