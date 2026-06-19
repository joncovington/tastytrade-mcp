"""Simulate the MEICAgent (https://github.com/joncovington/MEICAgent) request
handling against the MCP server.

MEICAgent runs as a Claude Code /loop that, each ~5-minute iteration, calls the
tastytrade-mcp tools in this sequence:

    get_connection_status -> get_account_info -> get_market_overview ->
    get_option_chain -> get_positions -> get_working_orders ->
    execute_trade (iron condor) -> execute_trade (stop-limit) ->
    adjust_order / close_position

These tests exercise the real tool handlers end-to-end through ``mcp.call_tool``
(the same entry point the agent uses), with the tastytrade SDK mocked so no live
brokerage or OAuth is required. They assert on the structured responses and that
the agent's order specs (4-leg IC, stop-limit with trigger) translate correctly
into the SDK ``NewOrder``.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from tastytrade.account import Account

from tastytrade_mcp import credentials, session
from tastytrade_mcp.config import Config
from tastytrade_mcp.server import build_server
from tastytrade_mcp.tools import market, strategy


# --------------------------------------------------------------------------- #
# Fakes standing in for the tastytrade SDK
# --------------------------------------------------------------------------- #
class FakeBuyingPowerEffect:
    def __init__(self, current="10000", new="9000", change="-1000"):
        self.current_buying_power = Decimal(current)
        self.new_buying_power = Decimal(new)
        self.change_in_buying_power = Decimal(change)
        self.impact = "1000"
        self.effect = "Debit"
        self.is_spread = True


class FakeOrderResponse:
    def __init__(self, bpe: FakeBuyingPowerEffect | None = None):
        self.buying_power_effect = bpe or FakeBuyingPowerEffect()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.order = SimpleNamespace(id=4242, status="Received")


class FakeBalances:
    def __init__(self, used="0", available="10000"):
        self.used_derivative_buying_power = Decimal(used)
        self.derivative_buying_power = Decimal(available)
        self.cash_balance = Decimal("25000")
        self.equity_buying_power = Decimal("10000")


class FakeAccount:
    """A single shared account whose last submitted order is inspectable."""

    account_number = "5WX01234"
    nickname = "Test"
    account_type_name = "Margin"

    def __init__(self):
        self.last_order = None
        self.deleted_order_id = None
        self.balances = FakeBalances()
        self.order_response = FakeOrderResponse()

    async def get_balances(self, _session):
        return self.balances

    async def get_positions(self, _session):
        return [SimpleNamespace(symbol="XSP 0DTE", quantity=Decimal("1"))]

    async def get_live_orders(self, _session):
        return [SimpleNamespace(id=4242, status="Live")]

    async def place_order(self, _session, order, dry_run=True):
        self.last_order = order
        return self.order_response

    async def replace_order(self, _session, order_id, order, dry_run=True):
        self.last_order = order
        return self.order_response

    async def delete_order(self, _session, order_id):
        self.deleted_order_id = order_id


class FakeOption:
    def __init__(self, strike, option_type, symbol):
        self.strike_price = Decimal(str(strike))
        self.option_type = option_type  # "Call" / "Put"
        self.symbol = symbol


def _fake_chain():
    """Two expirations of XSP options spanning a range of strikes."""
    from datetime import date, timedelta

    near = date.today() + timedelta(days=1)
    far = date.today() + timedelta(days=45)
    chain = {}
    for exp in (near, far):
        opts = []
        for strike in range(440, 561, 5):
            opts.append(
                FakeOption(strike, "Call", f"XSP {exp} C{strike}")
            )
            opts.append(
                FakeOption(strike, "Put", f"XSP {exp} P{strike}")
            )
        chain[exp] = opts
    return chain


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _config(**overrides):
    base = dict(
        sandbox=True,
        mock_mode=False,
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


@pytest.fixture
def account():
    return FakeAccount()


@pytest.fixture(autouse=True)
def _patched_sdk(monkeypatch, account):
    # Credentials present so get_session does not raise.
    credentials.set_secret(credentials.CLIENT_SECRET, "cs", sandbox=True)
    credentials.set_secret(credentials.REFRESH_TOKEN, "rt", sandbox=True)

    # Avoid real OAuth: get_session builds a sentinel session object.
    session.reset_session()
    monkeypatch.setattr(session, "Session", lambda *a, **k: SimpleNamespace())

    # Account.get -> our shared fake account (list when no number given).
    async def fake_get(_session, number=None):
        return account if number else [account]

    monkeypatch.setattr(Account, "get", fake_get)

    # Market-data module-level functions (imported by reference in the tools).
    chain = _fake_chain()

    async def fake_metrics(_session, symbols):
        return [SimpleNamespace(symbol=s, implied_volatility_rank="0.45") for s in symbols]

    async def fake_chain_fn(_session, symbol):
        return chain

    monkeypatch.setattr(market, "get_market_metrics", fake_metrics)
    monkeypatch.setattr(market, "get_option_chain", fake_chain_fn)
    monkeypatch.setattr(strategy, "get_option_chain", fake_chain_fn)

    yield
    session.reset_session()


async def _call(mcp, name, args=None):
    _content, structured = await mcp.call_tool(name, args or {})
    return structured


# --------------------------------------------------------------------------- #
# The agent loop
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_full_meic_loop_iteration():
    """Walk the agent's per-iteration sequence and assert each step succeeds."""
    mcp = build_server(_config())

    status = await _call(mcp, "get_connection_status")
    assert status["ok"] and status["connected"]
    assert status["environment"] == "sandbox"
    assert status["account_count"] == 1

    info = await _call(mcp, "get_account_info")
    assert info["ok"]
    assert info["account_number"] == "5WX01234"

    overview = await _call(mcp, "get_market_overview", {"symbols": ["XSP"]})
    assert overview["ok"]

    chain = await _call(mcp, "get_option_chain", {"symbol": "XSP"})
    assert chain["ok"] and chain["chain"]

    positions = await _call(mcp, "get_positions")
    assert positions["ok"]

    working = await _call(mcp, "get_working_orders")
    assert working["ok"]


@pytest.mark.asyncio
async def test_strategies_builds_iron_condor():
    mcp = build_server(_config())
    res = await _call(mcp, "get_strategies", {"symbol": "XSP", "target_dte": 1})
    assert res["ok"]
    assert res["strategy"] == "iron_condor"
    assert set(res["legs"]) == {"short_put", "long_put", "short_call", "long_call"}
    assert 0 <= res["estimated_pop"] <= 1


@pytest.mark.asyncio
async def test_execute_iron_condor_dry_run(account):
    """Agent submits a 4-leg iron condor as a dry-run first."""
    mcp = build_server(_config())
    ic_order = {
        "time_in_force": "Day",
        "order_type": "Limit",
        "price": -1.20,  # net credit
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "XSP C520", "quantity": 1, "action": "Sell to Open"},
            {"instrument_type": "Equity Option", "symbol": "XSP C525", "quantity": 1, "action": "Buy to Open"},
            {"instrument_type": "Equity Option", "symbol": "XSP P480", "quantity": 1, "action": "Sell to Open"},
            {"instrument_type": "Equity Option", "symbol": "XSP P475", "quantity": 1, "action": "Buy to Open"},
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": ic_order, "dry_run": True})
    assert res["ok"]
    assert res["dry_run"] is True
    assert res["buying_power"]["change_in_buying_power"] == "-1000"
    # The order reached the SDK with all four legs and a credit price.
    assert len(account.last_order.legs) == 4
    assert account.last_order.price == Decimal("-1.20")


@pytest.mark.asyncio
async def test_execute_stop_limit_break_even(account):
    """Agent attaches a DAY stop-limit for break-even protection."""
    mcp = build_server(_config())
    stop_order = {
        "time_in_force": "Day",
        "order_type": "Stop Limit",
        "stop_trigger": 1.20,
        "price": 1.25,
        "price_effect": "Debit",
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "XSP C520", "quantity": 1, "action": "Buy to Close"},
            {"instrument_type": "Equity Option", "symbol": "XSP P480", "quantity": 1, "action": "Buy to Close"},
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": stop_order, "dry_run": False})
    assert res["ok"]
    assert res["dry_run"] is False
    assert account.last_order.order_type.value == "Stop Limit"
    assert account.last_order.stop_trigger == Decimal("1.20")
    assert account.last_order.price == Decimal("1.25")  # Debit -> positive


@pytest.mark.asyncio
async def test_adjust_and_close_management(account):
    """Mid-session: tighten a working stop, then cancel it at EOD."""
    mcp = build_server(_config())
    new_stop = {
        "time_in_force": "Day",
        "order_type": "Stop Limit",
        "stop_trigger": 0.90,
        "price": 0.95,
        "price_effect": "Debit",
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "XSP C520", "quantity": 1, "action": "Buy to Close"},
        ],
    }
    adjusted = await _call(
        mcp, "adjust_order", {"order_id": 4242, "order": new_stop, "dry_run": True}
    )
    assert adjusted["ok"]
    assert account.last_order.stop_trigger == Decimal("0.90")

    closed = await _call(mcp, "close_position", {"order_id": 4242})
    assert closed["ok"]
    assert closed["cancelled_order_id"] == 4242
    assert account.deleted_order_id == 4242


# --------------------------------------------------------------------------- #
# Risk controls the agent must respect
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_order_rejected_by_buying_power_buffer():
    # Reserve 95% of BP; the order leaves only 9000/10000 (90%) -> rejected.
    mcp = build_server(_config(buying_power_buffer_pct=95.0))
    order = {
        "order_type": "Limit",
        "price": -1.0,
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "XSP C520", "quantity": 1, "action": "Sell to Open"},
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": order, "dry_run": False})
    assert res["ok"] is False
    assert any("buying power" in p.lower() for p in res["problems"])


@pytest.mark.asyncio
async def test_order_rejected_by_account_deploy_limit():
    # Capacity = used 0 + available 10000; 5% cap = 500. Order consumes 1000.
    mcp = build_server(_config(account_deploy_limit_pct=5.0))
    order = {
        "order_type": "Limit",
        "price": -1.0,
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "XSP C520", "quantity": 1, "action": "Sell to Open"},
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": order, "dry_run": False})
    assert res["ok"] is False
    assert any("deploy limit" in p.lower() for p in res["problems"])


@pytest.mark.asyncio
async def test_force_dry_run_blocks_live_submission(account):
    """With FORCE_DRY_RUN, even dry_run=false is downgraded to a dry-run."""
    mcp = build_server(_config(force_dry_run=True))
    order = {
        "order_type": "Limit",
        "price": -1.0,
        "legs": [
            {"instrument_type": "Equity Option", "symbol": "XSP C520", "quantity": 1, "action": "Sell to Open"},
        ],
    }
    res = await _call(mcp, "execute_trade", {"order": order, "dry_run": False})
    assert res["ok"]
    assert res["dry_run"] is True
    assert res["forced_dry_run"] is True


@pytest.mark.asyncio
async def test_order_tools_absent_without_live_trading():
    """When live trading is off the agent cannot even see the order tools."""
    mcp = build_server(_config(enable_live_trading=False))
    tools = {t.name for t in await mcp.list_tools()}
    assert "execute_trade" not in tools
    assert "get_positions" in tools  # read tools still available
