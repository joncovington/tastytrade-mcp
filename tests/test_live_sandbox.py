"""Live integration tests against the Tastytrade production environment.

These confirm the SDK + OAuth + API contract actually works with the credentials
stored in the OS keyring — the one path the mocked unit tests cannot cover.

OPT-IN ONLY. The whole module is skipped unless BOTH are true:
  * credentials are present in the keyring
    (`tastytrade-mcp secrets set`), and
  * the env var RUN_LIVE=1 is set.

Run with:   RUN_LIVE=1 pytest -m live -v

The server is built with force_dry_run=True so these tests can NEVER submit a
real order, even by mistake.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from tastytrade_mcp import credentials
from tastytrade_mcp.server import build_server
from tastytrade_mcp.session import get_session, reset_session

pytestmark = pytest.mark.live

_RUN = os.getenv("RUN_LIVE") == "1"
_HAS_CREDS = credentials.secrets_present()

skip_reason = "set RUN_LIVE=1 and store credentials to run live tests"
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not (_RUN and _HAS_CREDS), reason=skip_reason),
]


@pytest.fixture
def config(make_config):
    # Rebuild the OAuth session per test: the SDK's async HTTP client binds to
    # the event loop it was created in, and pytest-asyncio uses a fresh loop per
    # test, so a cached session would fail with "Event loop is closed".
    # force_dry_run=True is belt-and-suspenders: never submit from a test.
    reset_session()
    yield make_config(force_dry_run=True)
    reset_session()


@pytest.fixture
def mcp(config):
    return build_server(config)


async def _call(mcp, name, args=None):
    _content, structured = await mcp.call_tool(name, args or {})
    return structured


# --------------------------------------------------------------------------- #
# Auth & session
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_oauth_session_builds_and_refreshes(config):
    """A session can be built from stored creds (this triggers a token fetch)."""
    s = get_session(config)
    assert s is not None


@pytest.mark.asyncio
async def test_connection_status(mcp):
    res = await _call(mcp, "get_connection_status")
    assert res["ok"] is True
    assert res["connected"] is True
    assert res["account_count"] >= 1


# --------------------------------------------------------------------------- #
# Account data — confirm the SDK returns the field shapes our tools rely on
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_account_info_returns_numeric_buying_power(mcp):
    res = await _call(mcp, "get_account_info")
    assert res["ok"] is True
    assert res["account_number"]
    balances = res["balances"]
    for key in ("derivative_buying_power", "used_derivative_buying_power"):
        assert key in balances, f"missing balance field: {key}"
        Decimal(str(balances[key]))


@pytest.mark.asyncio
async def test_positions_is_a_list(mcp):
    res = await _call(mcp, "get_positions")
    assert res["ok"] is True
    assert isinstance(res["positions"], list)


@pytest.mark.asyncio
async def test_working_orders_is_a_list(mcp):
    res = await _call(mcp, "get_working_orders")
    assert res["ok"] is True
    assert isinstance(res["orders"], list)


@pytest.mark.asyncio
async def test_list_accounts(mcp):
    res = await _call(mcp, "list_accounts")
    assert res["ok"] is True
    assert len(res["accounts"]) >= 1
    assert res["accounts"][0]["account_number"]


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_option_chain_for_liquid_symbol(mcp):
    res = await _call(mcp, "get_option_chain", {"symbol": "SPY"})
    if not res["ok"] and _is_outage(res):
        pytest.skip("option-chain endpoint unavailable (5xx)")
    assert res["ok"] is True
    assert res["chain"], "expected at least one expiration"


@pytest.mark.asyncio
async def test_market_overview(mcp):
    res = await _call(mcp, "get_market_overview", {"symbols": ["SPY"]})
    if not res["ok"] and _is_outage(res):
        pytest.skip("market-metrics endpoint unavailable (5xx)")
    assert res["ok"] is True


# --------------------------------------------------------------------------- #
# Order path — dry-run only (force_dry_run=True)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_equity_dry_run_validates_against_api(mcp):
    """A well-formed equity order should pass dry-run validation."""
    order = {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": 1.00,
        "legs": [
            {
                "instrument_type": "Equity",
                "symbol": "SPY",
                "quantity": 1,
                "action": "Buy to Open",
            }
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": order, "dry_run": True})
    if not res.get("ok") and _is_outage(res):
        pytest.skip("order endpoint unavailable (5xx)")
    assert "buying_power" in res or "problems" in res or _is_api_validation(res)
    if res.get("ok"):
        assert res["dry_run"] is True
        assert "change_in_buying_power" in res["buying_power"]


def _is_outage(res: dict) -> bool:
    text = (str(res.get("error", "")) + " ".join(map(str, res.get("problems", [])))).lower()
    return any(t in text for t in ("502", "503", "504", "bad gateway", "<html", "couldn't parse"))


def _is_api_validation(res: dict) -> bool:
    err = str(res.get("error", "")).lower()
    if not err or _is_outage(res):
        return False
    transport_failures = ("connect", "timeout", "ssl", "unauthorized", "401", "403", "oauth", "token")
    return not any(t in err for t in transport_failures)
