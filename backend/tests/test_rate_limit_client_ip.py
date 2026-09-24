"""
Regression test for a real production bug: rate limiting was keyed by
slowapi's default get_remote_address, which reads request.client.host
directly. Behind Render's reverse proxy (or any PaaS/reverse-proxy
deployment), request.client.host is the PROXY's internal address for
every single request, not the real visitor - meaning every user's
login/register attempts across the whole app shared ONE global
rate-limit bucket. A burst of unrelated traffic could 429 everyone,
with no relation to any individual visitor's actual request rate.

get_client_ip must read X-Forwarded-For (which Render, and virtually
every reverse proxy, sets correctly) and key the limiter per-visitor
instead.
"""
from app.main import get_client_ip


class FakeRequest:
    def __init__(self, headers=None, client_host=None):
        self.headers = headers or {}
        self.client = _FakeClient(client_host) if client_host else None


class _FakeClient:
    def __init__(self, host):
        self.host = host


def test_uses_x_forwarded_for_when_present():
    """This is the actual fix: behind a reverse proxy, X-Forwarded-For
    carries the real visitor IP even though request.client.host is the
    proxy's own address."""
    req = FakeRequest(
        headers={"x-forwarded-for": "203.0.113.42"},
        client_host="10.0.0.1",  # Render's internal proxy address
    )
    assert get_client_ip(req) == "203.0.113.42"


def test_uses_first_ip_when_x_forwarded_for_has_a_chain():
    """X-Forwarded-For can be a comma-separated chain if multiple
    proxies are involved (client, then each hop) - the FIRST entry is
    the original client, which is what must be used for rate limiting,
    not an intermediate proxy."""
    req = FakeRequest(headers={"x-forwarded-for": "203.0.113.42, 10.0.0.5, 10.0.0.1"})
    assert get_client_ip(req) == "203.0.113.42"


def test_falls_back_to_client_host_when_no_forwarded_header():
    """Local dev / direct connections (no reverse proxy in front) won't
    have X-Forwarded-For at all - must still work via the direct
    connection's address in that case."""
    req = FakeRequest(headers={}, client_host="127.0.0.1")
    assert get_client_ip(req) == "127.0.0.1"


def test_returns_unknown_when_neither_is_available():
    """Must never raise - a malformed/edge-case request should degrade
    to a safe default rather than crashing the rate limiter itself."""
    req = FakeRequest(headers={}, client_host=None)
    assert get_client_ip(req) == "unknown"


def test_different_visitors_get_separate_rate_limit_buckets(client):
    """Real end-to-end proof of the fix, not just the unit-level one
    above: exhaust one visitor's login rate limit, then confirm a
    DIFFERENT visitor (different X-Forwarded-For) is completely
    unaffected. Before the fix, both would share one global bucket
    and this second request would incorrectly also be 429'd."""
    from app.main import app
    app.state.limiter.enabled = True
    try:
        # /auth/login is limited to 10/minute per client key
        for _ in range(10):
            client.post(
                "/auth/login", data={"email": "nobody@test.com", "password": "wrong"},
                headers={"X-Forwarded-For": "1.1.1.1"},
            )
        exhausted = client.post(
            "/auth/login", data={"email": "nobody@test.com", "password": "wrong"},
            headers={"X-Forwarded-For": "1.1.1.1"},
        )
        assert exhausted.status_code == 429

        different_visitor = client.post(
            "/auth/login", data={"email": "nobody@test.com", "password": "wrong"},
            headers={"X-Forwarded-For": "2.2.2.2"},
        )
        assert different_visitor.status_code == 401  # bad creds, NOT rate-limited
    finally:
        app.state.limiter.enabled = False
