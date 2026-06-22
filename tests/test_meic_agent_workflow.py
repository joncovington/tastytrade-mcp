"""Simulate the MEICAgent (https://github.com/joncovington/MEICAgent) request
handling against the MCP server.

MEICAgent runs as a Claude Code /loop that, each ~5-minute iteration, calls the
tastytrade-mcp tools in this sequence:

    get_connection_status -> get_account_info -> get_market_overview ->
    get_option_chain -> get_positions -> get_working_orders ->
    execute_trade (iron condor) -> execute_trade (stop-limit) ->
    adjust_order / close_position

These exercise the real tool handlers end-to-end through ``mcp.call_tool`` (the
same entry point the agent uses), with the SDK mocked via the shared ``mock_sdk``
fixture (see conftest.py). They assert on the structured responses and that the
agent's order specs translate correctly into the SDK ``NewOrder``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tastytrade_mcp.server import build_server

# Activate the fake SDK for every test in this module.
pytestmark = pytest.mark.usefixtures("mock_sdk")


def _legs(*specs):
    return [
        {"instrument_type": "Equity Option", "symbol": sym, "quantity": 1, "action": act}
        for sym, act in specs
    ]


# --------------------------------------------------------------------------- #
# The agent loop
# --------------------------------------------------------------------------- #
async def test_full_meic_loop_iteration(make_config, call_tool):
    """Walk the agent's per-iteration sequence and assert each step succeeds."""
    mcp = build_server(make_config())

    status = await call_tool(mcp, "get_connection_status")
    assert status["ok"] and status["connected"]
    assert status["account_count"] == 1

    info = await call_tool(mcp, "get_account_info")
    assert info["ok"] and info["account_number"] == "5WX01234"

    assert (await call_tool(mcp, "get_market_overview", {"symbols": ["XSP"]}))["ok"]
    chain = await call_tool(mcp, "get_option_chain", {"symbol": "XSP"})
    assert chain["ok"] and chain["chain"]
    assert (await call_tool(mcp, "get_positions"))["ok"]
    assert (await call_tool(mcp, "get_working_orders"))["ok"]


async def test_strategies_builds_iron_condor(make_config, call_tool):
    mcp = build_server(make_config())
    res = await call_tool(mcp, "get_strategies", {"symbol": "XSP", "target_dte": 1})
    assert res["ok"]
    assert res["strategy"] == "iron_condor"
    assert set(res["legs"]) == {"short_put", "long_put", "short_call", "long_call"}
    assert 0 <= res["estimated_pop"] <= 1
    # net_credit is None when quotes aren't available (mock doesn't stream quotes).
    assert "net_credit" in res
    assert "quotes_complete" in res


async def test_strategies_net_credit_from_quotes(make_config, call_tool, monkeypatch):
    """When quotes are available, net_credit is derived from leg midpoints."""
    from decimal import Decimal
    from types import SimpleNamespace
    from tastytrade_mcp.tools import strategy

    async def fake_quotes(_session, symbols, _timeout):
        # short legs: mid=1.20, long legs: mid=0.30 => net credit = (1.20+1.20)-(0.30+0.30) = 1.80
        return {
            s: SimpleNamespace(bid_price=Decimal("1.10"), ask_price=Decimal("1.30"))
            if i % 2 == 0
            else SimpleNamespace(bid_price=Decimal("0.25"), ask_price=Decimal("0.35"))
            for i, s in enumerate(symbols)
        }

    monkeypatch.setattr(strategy, "_collect_quotes", fake_quotes)
    mcp = build_server(make_config())
    res = await call_tool(mcp, "get_strategies", {"symbol": "XSP", "target_dte": 1})
    assert res["ok"]
    assert res["quotes_complete"] is True
    assert res["net_credit"] is not None
    assert res["net_credit_per_contract"] == round(res["net_credit"] * 100, 2)
    # Each leg's mid is present.
    for leg in res["legs"].values():
        assert leg["mid"] is not None


async def test_execute_iron_condor_dry_run(make_config, call_tool, fake_account):
    """Agent submits a 4-leg iron condor as a dry-run first."""
    mcp = build_server(make_config())
    ic_order = {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": -1.20,  # net credit
        "legs": _legs(
            ("XSP C520", "Sell to Open"),
            ("XSP C525", "Buy to Open"),
            ("XSP P480", "Sell to Open"),
            ("XSP P475", "Buy to Open"),
        ),
    }
    res = await call_tool(mcp, "execute_trade", {"order": ic_order, "dry_run": True})
    assert res["ok"] and res["dry_run"] is True
    assert res["buying_power"]["change_in_buying_power"] == "-1000"
    # The order reached the SDK with all four legs and a credit price.
    assert len(fake_account.last_order.legs) == 4
    assert fake_account.last_order.price == Decimal("-1.20")


async def test_execute_stop_limit_break_even(make_config, call_tool, fake_account):
    """Agent attaches a DAY stop-limit for break-even protection."""
    mcp = build_server(make_config())
    stop_order = {
        "time_in_force": "Day",
        "order_type": "Stop Limit",
        "stop_trigger": 1.20,
        "price": 1.25,
        "price_effect": "Debit",
        "legs": _legs(("XSP C520", "Buy to Close"), ("XSP P480", "Buy to Close")),
    }
    res = await call_tool(mcp, "execute_trade", {"order": stop_order, "dry_run": False})
    assert res["ok"] and res["dry_run"] is False
    assert fake_account.last_order.order_type.value == "Stop Limit"
    assert fake_account.last_order.stop_trigger == Decimal("1.20")
    assert fake_account.last_order.price == Decimal("1.25")  # Debit -> positive


async def test_adjust_and_close_management(make_config, call_tool, fake_account):
    """Mid-session: tighten a working stop, then cancel it at EOD."""
    mcp = build_server(make_config())
    new_stop = {
        "time_in_force": "Day",
        "order_type": "Stop Limit",
        "stop_trigger": 0.90,
        "price": 0.95,
        "price_effect": "Debit",
        "legs": _legs(("XSP C520", "Buy to Close")),
    }
    adjusted = await call_tool(
        mcp, "adjust_order", {"order_id": 4242, "order": new_stop, "dry_run": True}
    )
    assert adjusted["ok"]
    assert fake_account.last_order.stop_trigger == Decimal("0.90")

    closed = await call_tool(mcp, "close_position", {"order_id": 4242})
    assert closed["ok"] and closed["cancelled_order_id"] == 4242
    assert fake_account.deleted_order_id == 4242


# --------------------------------------------------------------------------- #
# Risk controls the agent must respect (integration, through the tool stack)
# --------------------------------------------------------------------------- #
_SINGLE_LEG_ORDER = {
    "order_type": "Limit",
    "price": -1.0,
    "legs": _legs(("XSP C520", "Sell to Open")),
}


async def test_order_rejected_by_buying_power_buffer(make_config, call_tool):
    # Reserve 95% of BP; the order leaves only 9000/10000 (90%) -> rejected.
    mcp = build_server(make_config(buying_power_buffer_pct=95.0))
    res = await call_tool(mcp, "execute_trade", {"order": _SINGLE_LEG_ORDER, "dry_run": False})
    assert res["ok"] is False
    assert any("buying power" in p.lower() for p in res["problems"])


async def test_order_rejected_by_account_deploy_limit(make_config, call_tool):
    # Capacity = used 0 + available 10000; 5% cap = 500. Order consumes 1000.
    mcp = build_server(make_config(account_deploy_limit_pct=5.0))
    res = await call_tool(mcp, "execute_trade", {"order": _SINGLE_LEG_ORDER, "dry_run": False})
    assert res["ok"] is False
    assert any("deploy limit" in p.lower() for p in res["problems"])


async def test_force_dry_run_blocks_live_submission(make_config, call_tool):
    """With FORCE_DRY_RUN, even dry_run=false is downgraded to a dry-run."""
    mcp = build_server(make_config(force_dry_run=True))
    res = await call_tool(mcp, "execute_trade", {"order": _SINGLE_LEG_ORDER, "dry_run": False})
    assert res["ok"] and res["dry_run"] is True
    assert res["forced_dry_run"] is True


async def test_order_tools_absent_without_live_trading(make_config):
    """When live trading is off the agent cannot even see the order tools."""
    mcp = build_server(make_config(enable_live_trading=False))
    tools = {t.name for t in await mcp.list_tools()}
    assert "execute_trade" not in tools
    assert "get_positions" in tools  # read tools still available
