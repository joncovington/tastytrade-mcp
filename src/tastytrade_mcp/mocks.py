"""Simulated SDK backend for mock mode.

When the server runs with ``TASTYTRADE_MOCK=true`` it installs these fakes in
place of the live tastytrade SDK calls, so an agent can exercise every tool —
connection checks, account/market queries, and (dry-run) order placement —
without any credentials or network access. Responses are deterministic.

``install_mocks`` patches the SDK entry points the tools use. Pass a custom
``setter`` (e.g. ``monkeypatch.setattr``) to make the patches auto-reversible in
tests; the default uses ``setattr`` for a long-lived server process.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

# Sentinel returned by get_session in mock mode (never used to make real calls).
MOCK_SESSION = SimpleNamespace(is_mock=True)


class _BuyingPowerEffect:
    def __init__(self, current="100000", new="98500", change="-1500"):
        self.current_buying_power = Decimal(current)
        self.new_buying_power = Decimal(new)
        self.change_in_buying_power = Decimal(change)
        self.impact = "1500"
        self.effect = "Debit"
        self.is_spread = True

    def model_dump(self, mode="json"):
        return {
            "current-buying-power": str(self.current_buying_power),
            "new-buying-power": str(self.new_buying_power),
            "change-in-buying-power": str(self.change_in_buying_power),
            "effect": self.effect,
        }


class _OrderResponse:
    def __init__(self):
        self.buying_power_effect = _BuyingPowerEffect()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.order = SimpleNamespace(id=900001, status="Received")

    def model_dump(self, mode="json"):
        return {
            "order": {"id": 900001, "status": "Received"},
            "buying-power-effect": self.buying_power_effect.model_dump(),
            "warnings": [],
        }


class _Balances:
    def model_dump(self, mode="json"):
        return {
            "cash-balance": "100000.00",
            "equity-buying-power": "100000.00",
            "derivative-buying-power": "100000.00",
            "used-derivative-buying-power": "0.00",
            "net-liquidating-value": "100000.00",
        }
    # Attribute access used by the account-deploy-limit check.
    used_derivative_buying_power = Decimal("0")
    derivative_buying_power = Decimal("100000")


class _Position:
    def __init__(self, symbol, qty, pnl):
        self.symbol, self.quantity, self.realized_day_gain = symbol, qty, pnl

    def model_dump(self, mode="json"):
        return {
            "symbol": self.symbol,
            "quantity": str(self.quantity),
            "realized-day-gain": str(self.realized_day_gain),
        }


class MockAccount:
    account_number = "5WX99999"
    nickname = "Mock Account"
    account_type_name = "Margin"

    def __init__(self):
        self.last_order = None
        self.deleted_order_id = None

    async def get_balances(self, _session):
        return _Balances()

    async def get_positions(self, _session):
        return [_Position("SPY 250101C00500000", Decimal("1"), Decimal("0"))]

    async def get_live_orders(self, _session):
        return [SimpleNamespace(
            id=900000, status="Live",
            model_dump=lambda mode="json": {"id": 900000, "status": "Live"},
        )]

    async def place_order(self, _session, order, dry_run=True):
        self.last_order = order
        return _OrderResponse()

    async def replace_order(self, _session, order_id, order, dry_run=True):
        self.last_order = order
        return _OrderResponse()

    async def delete_order(self, _session, order_id):
        self.deleted_order_id = order_id


def _mock_metric(symbol):
    return SimpleNamespace(
        symbol=symbol,
        model_dump=lambda mode="json": {
            "symbol": symbol,
            "implied-volatility-rank": "0.42",
            "implied-volatility-percentile": "0.55",
            "beta": "1.0",
        },
    )


class _MockOption:
    def __init__(self, strike, option_type, symbol):
        self.strike_price = Decimal(str(strike))
        self.option_type = option_type
        self.symbol = symbol

    def model_dump(self, mode="json"):
        return {
            "symbol": self.symbol,
            "strike-price": str(self.strike_price),
            "option-type": self.option_type,
        }


def mock_option_chain(symbol):
    near = date.today() + timedelta(days=1)
    far = date.today() + timedelta(days=45)
    chain = {}
    for exp in (near, far):
        opts = []
        for strike in range(400, 601, 5):
            opts.append(_MockOption(strike, "Call", f"{symbol} {exp} C{strike}"))
            opts.append(_MockOption(strike, "Put", f"{symbol} {exp} P{strike}"))
        chain[exp] = opts
    return chain


def install_mocks(setter=setattr) -> MockAccount:
    """Patch SDK entry points with mock implementations. Returns the shared
    MockAccount so callers/tests can inspect the last order placed."""
    from tastytrade.account import Account

    from .tools import market, strategy

    account = MockAccount()

    async def fake_get(_session, number=None):
        return account if number else [account]

    async def fake_metrics(_session, symbols):
        return [_mock_metric(s.upper()) for s in symbols]

    async def fake_chain(_session, symbol):
        return mock_option_chain(symbol.upper())

    setter(Account, "get", fake_get)
    setter(market, "get_market_metrics", fake_metrics)
    setter(market, "get_option_chain", fake_chain)
    setter(strategy, "get_option_chain", fake_chain)
    return account
