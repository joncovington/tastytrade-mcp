"""Tests for per-strike greeks in get_option_chain.

The DXLink streaming step (_collect_greeks) is mocked — these verify the
expiration filtering, the merge of delta/gamma/theta/iv into each strike, and
graceful behavior, not the live feed itself.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tastytrade_mcp.server import build_server
from tastytrade_mcp.tools import market

pytestmark = pytest.mark.usefixtures("mock_sdk")

NEAR = date.today() + timedelta(days=0)
FAR = date.today() + timedelta(days=7)


class GOption:
    def __init__(self, strike, option_type, streamer):
        self.strike_price = Decimal(str(strike))
        self.option_type = option_type
        self.symbol = f"XSP {streamer}"
        self.streamer_symbol = streamer

    def model_dump(self, mode="json"):
        return {
            "strike_price": str(self.strike_price),
            "option_type": self.option_type,
            "symbol": self.symbol,
            "streamer_symbol": self.streamer_symbol,
        }


def _chain():
    return {
        NEAR: [
            GOption(580, "Put", ".XSP-P580"),
            GOption(585, "Call", ".XSP-C585"),
        ],
        FAR: [GOption(580, "Put", ".XSP-P580-FAR")],
    }


def _wide_chain():
    """A single expiration with strikes 560..600 (calls and puts)."""
    opts = []
    for strike in range(560, 605, 5):
        opts.append(GOption(strike, "Call", f".C{strike}"))
        opts.append(GOption(strike, "Put", f".P{strike}"))
    return {NEAR: opts}


def _greek(sym):
    return SimpleNamespace(
        event_symbol=sym,
        delta=Decimal("-0.18"),
        gamma=Decimal("0.042"),
        theta=Decimal("-0.95"),
        volatility=Decimal("0.187"),
    )


@pytest.fixture
def patched_chain(monkeypatch):
    chain = _chain()

    async def fake_chain(_session, _symbol):
        return chain

    monkeypatch.setattr(market, "get_option_chain", fake_chain)
    return chain


async def test_chain_without_greeks_is_unchanged(make_config, call_tool, patched_chain, monkeypatch):
    async def boom(*a, **k):  # _collect_greeks must not be called
        raise AssertionError("greeks should not be fetched")

    monkeypatch.setattr(market, "_collect_greeks", boom)
    mcp = build_server(make_config())
    res = await call_tool(mcp, "get_option_chain", {"symbol": "XSP"})
    assert res["ok"]
    assert "greeks_included" not in res
    entry = res["chain"][str(NEAR)][0]
    assert "delta" not in entry


async def test_greeks_merged_per_strike(make_config, call_tool, patched_chain, monkeypatch):
    async def fake_collect(_session, symbols, _timeout):
        return {s: _greek(s) for s in symbols}

    monkeypatch.setattr(market, "_collect_greeks", fake_collect)
    mcp = build_server(make_config())
    res = await call_tool(
        mcp, "get_option_chain", {"symbol": "XSP", "include_greeks": True}
    )
    assert res["ok"] and res["greeks_included"] is True
    assert res["greeks_complete"] is True
    # include_greeks with no expiration defaults to nearest only.
    assert set(res["chain"]) == {str(NEAR)}
    entry = res["chain"][str(NEAR)][0]
    assert entry["delta"] == -0.18
    assert entry["gamma"] == 0.042
    assert entry["theta"] == -0.95
    assert entry["iv"] == 0.187


async def test_partial_greeks_reported_incomplete(make_config, call_tool, patched_chain, monkeypatch):
    async def fake_collect(_session, symbols, _timeout):
        # Only the first symbol gets greeks.
        return {symbols[0]: _greek(symbols[0])}

    monkeypatch.setattr(market, "_collect_greeks", fake_collect)
    mcp = build_server(make_config())
    res = await call_tool(
        mcp, "get_option_chain", {"symbol": "XSP", "include_greeks": True}
    )
    assert res["greeks_complete"] is False
    assert res["greeks_received"] == 1


async def test_expiration_filter(make_config, call_tool, patched_chain):
    mcp = build_server(make_config())
    res = await call_tool(
        mcp, "get_option_chain", {"symbol": "XSP", "expiration": str(FAR)}
    )
    assert res["ok"]
    assert set(res["chain"]) == {str(FAR)}


async def test_unknown_expiration_lists_available(make_config, call_tool, patched_chain):
    mcp = build_server(make_config())
    res = await call_tool(
        mcp, "get_option_chain", {"symbol": "XSP", "expiration": "2099-01-01"}
    )
    assert res["ok"] is False
    assert str(NEAR) in res["available_expirations"]


async def test_atm_window_limits_strikes(make_config, call_tool, monkeypatch):
    chain = _wide_chain()

    async def fake_chain(_session, _symbol):
        return chain

    monkeypatch.setattr(market, "get_option_chain", fake_chain)
    mcp = build_server(make_config())
    # Center on 580 with 2 strikes each side -> strikes 570,575,580,585,590.
    res = await call_tool(
        mcp,
        "get_option_chain",
        {"symbol": "XSP", "strike_count": 2, "around_price": 580},
    )
    assert res["ok"]
    strikes = {float(e["strike_price"]) for e in res["chain"][str(NEAR)]}
    assert strikes == {570.0, 575.0, 580.0, 585.0, 590.0}


async def test_atm_window_bounds_greeks_subscription(make_config, call_tool, monkeypatch):
    chain = _wide_chain()
    captured = {}

    async def fake_chain(_session, _symbol):
        return chain

    async def fake_collect(_session, symbols, _timeout):
        captured["symbols"] = symbols
        return {s: _greek(s) for s in symbols}

    monkeypatch.setattr(market, "get_option_chain", fake_chain)
    monkeypatch.setattr(market, "_collect_greeks", fake_collect)
    mcp = build_server(make_config())
    res = await call_tool(
        mcp,
        "get_option_chain",
        {
            "symbol": "XSP",
            "include_greeks": True,
            "strike_count": 1,
            "around_price": 580,
        },
    )
    assert res["ok"] and res["greeks_complete"] is True
    # 3 strikes (575/580/585) x 2 types = 6 streamer symbols subscribed.
    assert len(captured["symbols"]) == 6
