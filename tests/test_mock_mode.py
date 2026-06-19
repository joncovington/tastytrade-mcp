"""Mock mode: an agent can exercise every tool with simulated SDK responses.

These build the server with ``mock_mode=True`` (which installs the simulated
backend via ``build_server``) and drive the tools exactly as an agent would — no
credentials, no network. A fixture restores the patched SDK entry points so the
global mock install does not leak into other tests.
"""

from __future__ import annotations

import pytest

from tastytrade.account import Account

from tastytrade_mcp.config import Config
from tastytrade_mcp.server import build_server
from tastytrade_mcp.tools import market, strategy


def _mock_config(**overrides):
    base = dict(
        sandbox=True,
        mock_mode=True,
        mock_fixture=None,
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
    base.update(overrides)
    return Config(**base)


@pytest.fixture(autouse=True)
def _restore_sdk():
    """Snapshot SDK entry points that install_mocks() patches and restore them,
    so mock mode doesn't bleed into other test modules."""
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


@pytest.mark.asyncio
async def test_connection_status_in_mock_mode():
    mcp = build_server(_mock_config())
    res = await _call(mcp, "get_connection_status")
    assert res["ok"] and res["connected"]
    assert res["mock_mode"] is True
    assert res["credentials_present"] is True  # no real creds needed
    assert res["account_count"] == 1


@pytest.mark.asyncio
async def test_account_and_market_reads_in_mock_mode():
    mcp = build_server(_mock_config())

    info = await _call(mcp, "get_account_info")
    assert info["ok"]
    assert info["account_number"] == "5WX99999"

    positions = await _call(mcp, "get_positions")
    assert positions["ok"] and isinstance(positions["positions"], list)

    overview = await _call(mcp, "get_market_overview", {"symbols": ["SPY"]})
    assert overview["ok"]

    chain = await _call(mcp, "get_option_chain", {"symbol": "SPY"})
    assert chain["ok"] and chain["chain"]

    strat = await _call(mcp, "get_strategies", {"symbol": "SPY", "target_dte": 1})
    assert strat["ok"]
    assert set(strat["legs"]) == {"short_put", "long_put", "short_call", "long_call"}


@pytest.mark.asyncio
async def test_order_round_trip_in_mock_mode():
    mcp = build_server(_mock_config())
    order = {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": -1.20,
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "SPY C520", "quantity": 1, "action": "Sell to Open"},
            {"instrument_type": "Equity Option", "symbol": "SPY C525", "quantity": 1, "action": "Buy to Open"},
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": order, "dry_run": True})
    assert res["ok"]
    assert res["dry_run"] is True
    assert "change_in_buying_power" in res["buying_power"]


@pytest.mark.asyncio
async def test_no_credentials_required_in_mock_mode():
    """get_session must not raise even with no stored credentials."""
    from tastytrade_mcp.session import get_session

    session = get_session(_mock_config())
    assert getattr(session, "is_mock", False) is True


# --------------------------------------------------------------------------- #
# Fixture-driven scenarios
# --------------------------------------------------------------------------- #
import json  # noqa: E402


def _write_fixture(tmp_path, data) -> str:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.mark.asyncio
async def test_fixture_overrides_account_and_balances(tmp_path):
    fixture = _write_fixture(
        tmp_path,
        {
            "account_number": "5WX12345",
            "balances": {
                "derivative_buying_power": "30000",
                "used_derivative_buying_power": "20000",
            },
            "positions": [{"symbol": "XSP P540", "quantity": "2"}],
        },
    )
    mcp = build_server(_mock_config(mock_fixture=fixture))

    info = await _call(mcp, "get_account_info")
    assert info["account_number"] == "5WX12345"
    assert info["balances"]["derivative-buying-power"] == "30000"

    positions = await _call(mcp, "get_positions")
    assert len(positions["positions"]) == 1
    assert positions["positions"][0]["symbol"] == "XSP P540"


@pytest.mark.asyncio
async def test_fixture_can_reject_an_order(tmp_path):
    fixture = _write_fixture(
        tmp_path,
        {"order_response": {"errors": ["Insufficient option level"]}},
    )
    mcp = build_server(_mock_config(mock_fixture=fixture))
    order = {
        "order_type": "Limit",
        "price": -1.0,
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "SPY C520", "quantity": 1, "action": "Sell to Open"},
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": order, "dry_run": True})
    assert res["ok"] is False
    assert "Insufficient option level" in res["problems"]


@pytest.mark.asyncio
async def test_fixture_can_simulate_endpoint_outage(tmp_path):
    fixture = _write_fixture(
        tmp_path, {"raise": {"get_positions": "502 Bad Gateway"}}
    )
    mcp = build_server(_mock_config(mock_fixture=fixture))
    res = await _call(mcp, "get_positions")
    assert res["ok"] is False
    assert "502" in res["error"]


@pytest.mark.asyncio
async def test_fixture_defines_option_chain(tmp_path):
    from datetime import date, timedelta

    exp = (date.today() + timedelta(days=2)).isoformat()
    fixture = _write_fixture(
        tmp_path,
        {
            "option_chain": {
                exp: [
                    {"strike": 580, "option_type": "Call", "symbol": f"XSP {exp} C580"},
                    {"strike": 540, "option_type": "Put", "symbol": f"XSP {exp} P540"},
                ]
            }
        },
    )
    mcp = build_server(_mock_config(mock_fixture=fixture))

    chain = await _call(mcp, "get_option_chain", {"symbol": "XSP"})
    assert chain["ok"]
    assert exp in chain["chain"]
    symbols = [o["symbol"] for o in chain["chain"][exp]]
    assert f"XSP {exp} C580" in symbols and f"XSP {exp} P540" in symbols


@pytest.mark.asyncio
async def test_fixture_account_deploy_limit_uses_fixture_balances(tmp_path):
    # used 9000 + available 1000 -> capacity 10000; 50% cap = 5000.
    # Order consumes 1500 (default BPE) -> deployed 9000+1500=10500 > 5000 -> reject.
    fixture = _write_fixture(
        tmp_path,
        {
            "balances": {
                "derivative_buying_power": "1000",
                "used_derivative_buying_power": "9000",
            }
        },
    )
    mcp = build_server(_mock_config(mock_fixture=fixture, account_deploy_limit_pct=50.0))
    order = {
        "order_type": "Limit",
        "price": -1.0,
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "SPY C520", "quantity": 1, "action": "Sell to Open"},
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": order, "dry_run": True})
    assert res["ok"] is False
    assert any("deploy limit" in p.lower() for p in res["problems"])
