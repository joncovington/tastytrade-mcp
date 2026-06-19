"""Tests for the shipped MEIC fixture variants — each reproduces a specific
agent decision/limit branch.

    mock_fixture_stop_filled.json   -> Step 4a post-stop evaluation
    mock_fixture_stale_pending.json -> Step 4b stale-pending cancellation
    mock_fixture_bp_rejection.json  -> Step 6 buying-power rejection on entry
    mock_fixture_mcp_outage.json    -> Hard limit 7 connection failure / halt
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tastytrade.account import Account

from tastytrade_mcp.config import Config
from tastytrade_mcp.server import build_server
from tastytrade_mcp.tools import market, strategy

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _config(fixture_name: str):
    return Config(
        sandbox=True,
        mock_mode=True,
        mock_fixture=str(EXAMPLES / fixture_name),
        enable_live_trading=True,
        force_dry_run=False,
        buying_power_buffer_pct=0.0,
        account_deploy_limit_pct=0.0,
        log_level="INFO",
        cors_origin="http://localhost:3333",
        rate_limit="120/minute",
        http_host="127.0.0.1",
        http_port=7698,
    )


@pytest.fixture(autouse=True)
def _restore_sdk():
    saved = (
        Account.get,
        market.get_market_metrics,
        market.get_option_chain,
        strategy.get_option_chain,
    )
    yield
    Account.get, market.get_market_metrics, market.get_option_chain, strategy.get_option_chain = saved


async def _call(mcp, name, args=None):
    _content, structured = await mcp.call_tool(name, args or {})
    return structured


_ENTRY_ORDER = {
    "order": {
        "order_type": "Limit",
        "price": -1.15,
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "XSP C588", "quantity": 1, "action": "Sell to Open"},
            {"instrument_type": "Equity Option", "symbol": "XSP C593", "quantity": 1, "action": "Buy to Open"},
            {"instrument_type": "Equity Option", "symbol": "XSP P576", "quantity": 1, "action": "Sell to Open"},
            {"instrument_type": "Equity Option", "symbol": "XSP P571", "quantity": 1, "action": "Buy to Open"},
        ],
    },
    "dry_run": True,
}


@pytest.mark.asyncio
async def test_stop_filled_has_a_condor_without_a_working_stop():
    """Step 4a: a stop filled, so working orders (2) are fewer than condors (3)."""
    mcp = build_server(_config("mock_fixture_stop_filled.json"))
    positions = await _call(mcp, "get_positions")
    working = await _call(mcp, "get_working_orders")
    assert len(positions["positions"]) == 12       # 3 condors still on book
    assert len(working["orders"]) == 2             # only 2 stops remain
    ids = {o["id"] for o in working["orders"]}
    assert 910001 not in ids                        # IC #1 stop is gone (filled)


@pytest.mark.asyncio
async def test_stale_pending_order_is_detectable():
    """Step 4b: a pending (unfilled) entry with an old received_at is present."""
    mcp = build_server(_config("mock_fixture_stale_pending.json"))
    working = await _call(mcp, "get_working_orders")
    pending = [o for o in working["orders"] if o.get("status") != "Live"]
    assert len(pending) == 1
    stale = pending[0]
    assert stale["status"] == "Received"
    assert stale["received-at"].startswith("2020-")  # far in the past -> > 10 min


@pytest.mark.asyncio
async def test_bp_rejection_blocks_entry():
    """Step 6: dry-run entry is rejected for insufficient buying power."""
    mcp = build_server(_config("mock_fixture_bp_rejection.json"))
    res = await _call(mcp, "execute_trade", _ENTRY_ORDER)
    assert res["ok"] is False
    assert any("buying power" in p.lower() for p in res["problems"])


@pytest.mark.asyncio
async def test_mcp_outage_connection_status_fails():
    """Hard limit 7: connection check fails so the agent can halt."""
    mcp = build_server(_config("mock_fixture_mcp_outage.json"))
    res = await _call(mcp, "get_connection_status")
    assert res["ok"] is False
    assert res["connected"] is False
    assert "503" in res["error"]
