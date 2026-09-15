"""
Shared pytest fixtures. Environment variables must be set BEFORE
app.main (and transitively app.db) is imported, since app/db.py creates
its SQLAlchemy engine at module-import time from DATABASE_URL - setting
the env var after import would have no effect on an already-created engine.
"""
import os
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-for-pytest")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault("GEMINI_API_KEY", "dummy-key-not-used-in-these-tests")
os.environ.setdefault("DATABASE_URL", "sqlite:///./_pytest_waypost.db")

from fastapi.testclient import TestClient
from app.main import app
from app.db import Base, engine


@pytest.fixture(autouse=True)
def _disable_rate_limiting():
    """Auth tests hit /auth/login and /auth/register repeatedly across
    many test cases - all from the same test-client 'IP', which would
    otherwise trip the real 10/minute and 5/minute limits and cause
    flaky failures unrelated to what's actually being tested."""
    app.state.limiter.enabled = False
    yield
    app.state.limiter.enabled = True


@pytest.fixture
def client():
    """Fresh tables for every test - genuine isolation, not just hoping
    tests don't interfere with each other."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)
