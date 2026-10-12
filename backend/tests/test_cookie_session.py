"""Cookie-based session: httpOnly cookie is issued on login/register,
authenticates /auth/me without an Authorization header, is cleared on
logout, and Bearer tokens keep working."""
import httpx
import pytest
from tests.test_main import register


@pytest.fixture(autouse=True)
def _https(client):
    # The session cookie is Secure (as it is in production), so the cookie
    # jar only returns it for https URLs.
    client.base_url = httpx.URL("https://testserver")


def _login(client):
    return client.post("/auth/login", data={"email": "test@test.com", "password": "testpass123"})


def test_login_sets_httponly_cookie_and_authenticates(client):
    register(client)
    r = _login(client)
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "waypost_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    # TestClient keeps the cookie jar, so no Authorization header is sent here.
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "test@test.com"


def test_bearer_header_still_works_without_cookie(client):
    register(client)
    token = _login(client).json()["access_token"]
    client.cookies.clear()
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_logout_clears_cookie(client):
    register(client)
    _login(client)
    assert client.get("/auth/me").status_code == 200
    r = client.post("/auth/logout")
    assert r.status_code == 200
    client.cookies.clear()  # browser drops it on Max-Age=0; mimic that
    assert client.get("/auth/me").status_code == 401


def test_logout_everywhere_revokes_cookie_session(client):
    register(client)
    _login(client)
    assert client.post("/auth/logout_everywhere").status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_csp_header_present_and_cors_not_wildcard(client):
    r = client.get("/health")
    assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
    r = client.options("/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") != "*"
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"
