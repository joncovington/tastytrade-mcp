"""Tests for the watchlist tools (get_watchlists, manage_watchlist)."""

from __future__ import annotations

import pytest

from tastytrade_mcp.server import build_server
from tastytrade_mcp.tools import watchlists

pytestmark = pytest.mark.usefixtures("mock_sdk")


class FakeWatchlist:
    """Stands in for tastytrade.watchlists.PrivateWatchlist."""

    last_created = None
    removed_name = None

    def __init__(self, name=None, watchlist_entries=None):
        self.name = name
        self.entries = list(watchlist_entries or [])
        self.added: list[tuple] = []
        self.removed: list[tuple] = []
        self.uploaded = False
        self.updated = False

    @classmethod
    async def get(cls, session, name=None):
        if name:
            return cls(name=name)
        return [cls(name="watch-a"), cls(name="watch-b")]

    async def upload(self, session):
        self.uploaded = True
        FakeWatchlist.last_created = self

    @classmethod
    async def remove(cls, session, name):
        cls.removed_name = name

    def add_symbol(self, symbol, instrument_type):
        self.added.append((symbol, instrument_type))

    def remove_symbol(self, symbol, instrument_type):
        self.removed.append((symbol, instrument_type))

    async def update(self, session):
        self.updated = True

    def model_dump(self, mode="json"):
        return {"name": self.name, "entries": self.entries}


@pytest.fixture
def fake_watchlist(monkeypatch):
    FakeWatchlist.last_created = None
    FakeWatchlist.removed_name = None
    monkeypatch.setattr(watchlists, "PrivateWatchlist", FakeWatchlist)
    return FakeWatchlist


async def test_get_all_watchlists(make_config, call_tool, fake_watchlist):
    mcp = build_server(make_config())
    res = await call_tool(mcp, "get_watchlists")
    assert res["ok"]
    assert {w["name"] for w in res["watchlists"]} == {"watch-a", "watch-b"}


async def test_get_named_watchlist(make_config, call_tool, fake_watchlist):
    mcp = build_server(make_config())
    res = await call_tool(mcp, "get_watchlists", {"name": "watch-a"})
    assert res["ok"]
    assert res["watchlist"]["name"] == "watch-a"


async def test_manage_watchlist_create(make_config, call_tool, fake_watchlist):
    mcp = build_server(make_config())
    res = await call_tool(mcp, "manage_watchlist", {"action": "create", "name": "mine"})
    assert res["ok"] and res["action"] == "create"
    assert fake_watchlist.last_created.uploaded is True


async def test_manage_watchlist_add_symbol(make_config, call_tool, fake_watchlist):
    mcp = build_server(make_config())
    res = await call_tool(
        mcp,
        "manage_watchlist",
        {"action": "add_symbol", "name": "mine", "symbol": "spy"},
    )
    assert res["ok"]
    assert res["symbol"] == "SPY"


async def test_manage_watchlist_delete(make_config, call_tool, fake_watchlist):
    mcp = build_server(make_config())
    res = await call_tool(mcp, "manage_watchlist", {"action": "delete", "name": "mine"})
    assert res["ok"] and res["action"] == "delete"
    assert fake_watchlist.removed_name == "mine"


async def test_manage_watchlist_requires_symbol(make_config, call_tool, fake_watchlist):
    mcp = build_server(make_config())
    res = await call_tool(mcp, "manage_watchlist", {"action": "add_symbol", "name": "mine"})
    assert res["ok"] is False
    assert "requires a symbol" in res["error"]


async def test_manage_watchlist_unknown_action(make_config, call_tool, fake_watchlist):
    mcp = build_server(make_config())
    res = await call_tool(
        mcp, "manage_watchlist", {"action": "frobnicate", "name": "mine", "symbol": "SPY"}
    )
    assert res["ok"] is False
    assert "Unknown action" in res["error"]


async def test_manage_watchlist_absent_without_live_trading(make_config):
    mcp = build_server(make_config(enable_live_trading=False))
    tools = {t.name for t in await mcp.list_tools()}
    assert "get_watchlists" in tools          # read tool stays
    assert "manage_watchlist" not in tools     # mutating tool gated
