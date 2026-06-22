"""Shared test fixtures: an in-memory keyring backend."""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        try:
            del self._store[(service, username)]
        except KeyError as exc:
            from keyring.errors import PasswordDeleteError

            raise PasswordDeleteError("not found") from exc


@pytest.fixture(autouse=True)
def memory_keyring(request):
    # Live integration tests must use the real OS keyring (stored credentials),
    # so skip the in-memory swap for anything marked @pytest.mark.live.
    if request.node.get_closest_marker("live"):
        yield None
        return
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


# --------------------------------------------------------------------------- #
# Shared Config factory — one place to add fields as Config evolves.
# --------------------------------------------------------------------------- #
@pytest.fixture
def make_config():
    from tastytrade_mcp.config import Config

    def _make(**overrides):
        base = dict(
            sandbox=True,
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

    return _make


@pytest.fixture
def call_tool():
    """Invoke an MCP tool and return its structured (dict) result."""

    async def _call(mcp, name, args=None):
        _content, structured = await mcp.call_tool(name, args or {})
        return structured

    return _call


# --------------------------------------------------------------------------- #
# Fake tastytrade SDK — shared by tool-level tests.
# --------------------------------------------------------------------------- #
from decimal import Decimal  # noqa: E402
from types import SimpleNamespace  # noqa: E402


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
        self.streamer_symbol = f".{symbol.replace(' ', '')}"


def build_fake_chain():
    """Two expirations of XSP options spanning a range of strikes."""
    from datetime import date, timedelta

    chain = {}
    for exp in (date.today() + timedelta(days=1), date.today() + timedelta(days=45)):
        opts = []
        for strike in range(440, 561, 5):
            opts.append(FakeOption(strike, "Call", f"XSP {exp} C{strike}"))
            opts.append(FakeOption(strike, "Put", f"XSP {exp} P{strike}"))
        chain[exp] = opts
    return chain


@pytest.fixture
def fake_account():
    return FakeAccount()


@pytest.fixture
def mock_sdk(monkeypatch, fake_account):
    """Patch the tastytrade SDK entry points the tools use with deterministic
    fakes, and store credentials so get_session does not raise. Yields the shared
    FakeAccount so tests can inspect the last order placed."""
    from tastytrade.account import Account

    from tastytrade_mcp import credentials, session
    from tastytrade_mcp.tools import market, strategy

    credentials.set_secret(credentials.CLIENT_SECRET, "cs", sandbox=True)
    credentials.set_secret(credentials.REFRESH_TOKEN, "rt", sandbox=True)

    session.reset_session()
    monkeypatch.setattr(session, "Session", lambda *a, **k: SimpleNamespace())

    async def fake_get(_session, number=None):
        return fake_account if number else [fake_account]

    chain = build_fake_chain()

    async def fake_metrics(_session, symbols):
        return [
            SimpleNamespace(symbol=s, implied_volatility_rank="0.45") for s in symbols
        ]

    async def fake_chain_fn(_session, symbol):
        return chain

    monkeypatch.setattr(Account, "get", fake_get)
    monkeypatch.setattr(market, "get_market_metrics", fake_metrics)
    monkeypatch.setattr(market, "get_option_chain", fake_chain_fn)
    monkeypatch.setattr(strategy, "get_option_chain", fake_chain_fn)

    yield fake_account
    session.reset_session()
