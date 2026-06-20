"""Tests for the hardened HTTP transport: CORS restriction and rate limiting.

Exercises the Starlette middleware in build_http_app via Starlette's TestClient,
without starting uvicorn. The MCP route responses themselves are irrelevant here
— we assert only on the CORS and rate-limit behavior the middleware enforces.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tastytrade_mcp.http_app import build_http_app
from tastytrade_mcp.server import build_server

ALLOWED = "http://localhost:3333"
DISALLOWED = "http://evil.example.com"


@pytest.fixture
def client_factory(make_config):
    def _make(**overrides):
        config = make_config(**overrides)
        app = build_http_app(build_server(config), config)
        return TestClient(app)

    return _make


def test_cors_preflight_allows_configured_origin(client_factory):
    with client_factory() as client:
        resp = client.options(
            "/mcp",
            headers={"Origin": ALLOWED, "Access-Control-Request-Method": "POST"},
        )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ALLOWED


def test_cors_preflight_rejects_other_origin(client_factory):
    with client_factory() as client:
        resp = client.options(
            "/mcp",
            headers={"Origin": DISALLOWED, "Access-Control-Request-Method": "POST"},
        )
    # Starlette's CORSMiddleware returns 400 for a disallowed preflight origin.
    assert resp.status_code == 400
    assert resp.headers.get("access-control-allow-origin") != DISALLOWED


def test_rate_limit_returns_429_after_limit(client_factory):
    with client_factory(rate_limit="3/minute") as client:
        statuses = [client.get("/mcp").status_code for _ in range(6)]
    assert 429 in statuses
    # Once tripped it stays limited.
    assert statuses[-1] == 429
    # The first few (within the limit) were NOT rate-limited.
    assert statuses[0] != 429
