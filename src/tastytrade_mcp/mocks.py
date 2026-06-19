"""Simulated SDK backend for mock mode.

When the server runs with ``TASTYTRADE_MOCK=true`` it installs these fakes in
place of the live tastytrade SDK calls, so an agent can exercise every tool —
connection checks, account/market queries, and (dry-run) order placement —
without any credentials or network access.

By default the responses are deterministic placeholders. Set
``TASTYTRADE_MOCK_FIXTURE=/path/to/file.json`` to override them and simulate
specific scenarios (custom balances/positions, order rejections, endpoint
outages). See ``examples/mock_fixture.json`` for the schema.

``install_mocks`` patches the SDK entry points the tools use. Pass a custom
``setter`` (e.g. ``monkeypatch.setattr``) to make the patches auto-reversible in
tests; the default uses ``setattr`` for a long-lived server process.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel returned by get_session in mock mode (never used to make real calls).
MOCK_SESSION = SimpleNamespace(is_mock=True)

# --------------------------------------------------------------------------- #
# Defaults (used when the fixture omits a section)
# --------------------------------------------------------------------------- #
_DEFAULT_BALANCES = {
    "cash_balance": "100000.00",
    "equity_buying_power": "100000.00",
    "derivative_buying_power": "100000.00",
    "used_derivative_buying_power": "0.00",
    "net_liquidating_value": "100000.00",
}
_DEFAULT_POSITIONS = [
    {"symbol": "SPY 250101C00500000", "quantity": "1", "realized_day_gain": "0"},
]
_DEFAULT_WORKING_ORDERS = [{"id": 900000, "status": "Live"}]
_DEFAULT_BPE = {
    "current_buying_power": "100000",
    "new_buying_power": "98500",
    "change_in_buying_power": "-1500",
    "effect": "Debit",
}


def _hyphenate(data: dict[str, Any]) -> dict[str, Any]:
    return {k.replace("_", "-"): v for k, v in data.items()}


class _Record:
    """Generic object that serializes its dict via model_dump (hyphenated keys)
    and exposes the values as attributes."""

    def __init__(self, data: dict[str, Any]):
        self._data = data
        for key, value in data.items():
            setattr(self, key, value)

    def model_dump(self, mode="json"):  # noqa: ARG002
        return _hyphenate({k: (str(v) if isinstance(v, Decimal) else v) for k, v in self._data.items()})


class _Balances(_Record):
    def __init__(self, data: dict[str, Any]):
        super().__init__(data)
        # The account-deploy-limit check reads these as Decimals.
        self.used_derivative_buying_power = Decimal(
            str(data.get("used_derivative_buying_power", "0"))
        )
        self.derivative_buying_power = Decimal(
            str(data.get("derivative_buying_power", "100000"))
        )


class _BuyingPowerEffect(_Record):
    def __init__(self, data: dict[str, Any]):
        super().__init__(data)
        self.current_buying_power = Decimal(str(data.get("current_buying_power", "100000")))
        self.new_buying_power = Decimal(str(data.get("new_buying_power", "98500")))
        self.change_in_buying_power = Decimal(str(data.get("change_in_buying_power", "-1500")))
        self.impact = str(abs(self.change_in_buying_power))
        self.effect = data.get("effect", "Debit")
        self.is_spread = True


class _OrderResponse:
    def __init__(self, fixture: dict[str, Any]):
        spec = fixture.get("order_response", {})
        self.buying_power_effect = _BuyingPowerEffect(spec.get("buying_power_effect", _DEFAULT_BPE))
        self.errors = list(spec.get("errors", []))
        self.warnings = list(spec.get("warnings", []))
        self.order = SimpleNamespace(id=900001, status="Received")

    def model_dump(self, mode="json"):  # noqa: ARG002
        return {
            "order": {"id": 900001, "status": "Received"},
            "buying-power-effect": self.buying_power_effect.model_dump(),
            "errors": self.errors,
            "warnings": self.warnings,
        }


class MockAccount:
    def __init__(self, fixture: dict[str, Any]):
        self._fixture = fixture
        self.account_number = fixture.get("account_number", "5WX99999")
        self.nickname = fixture.get("nickname", "Mock Account")
        self.account_type_name = fixture.get("account_type_name", "Margin")
        self.last_order = None
        self.deleted_order_id = None

    def _maybe_raise(self, method: str) -> None:
        message = self._fixture.get("raise", {}).get(method)
        if message:
            raise RuntimeError(str(message))

    async def get_balances(self, _session):
        self._maybe_raise("get_balances")
        return _Balances(self._fixture.get("balances", _DEFAULT_BALANCES))

    async def get_positions(self, _session):
        self._maybe_raise("get_positions")
        return [_Record(p) for p in self._fixture.get("positions", _DEFAULT_POSITIONS)]

    async def get_live_orders(self, _session):
        self._maybe_raise("get_live_orders")
        return [_Record(o) for o in self._fixture.get("working_orders", _DEFAULT_WORKING_ORDERS)]

    async def place_order(self, _session, order, dry_run=True):
        self.last_order = order
        self._maybe_raise("place_order")
        return _OrderResponse(self._fixture)

    async def replace_order(self, _session, order_id, order, dry_run=True):
        self.last_order = order
        self._maybe_raise("replace_order")
        return _OrderResponse(self._fixture)

    async def delete_order(self, _session, order_id):
        self._maybe_raise("delete_order")
        self.deleted_order_id = order_id


class _MockOption(_Record):
    def __init__(self, strike, option_type, symbol):
        super().__init__(
            {"symbol": symbol, "strike_price": str(strike), "option_type": option_type}
        )
        self.strike_price = Decimal(str(strike))
        self.option_type = option_type
        self.symbol = symbol


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


def _load_fixture(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            fixture = json.load(f)
        logger.info("Loaded mock fixture from %s", path)
        return fixture
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load mock fixture %s: %s; using defaults", path, exc)
        return {}


def install_mocks(config=None, setter=setattr) -> MockAccount:
    """Patch SDK entry points with mock implementations driven by an optional
    JSON fixture. Returns the shared MockAccount so callers/tests can inspect the
    last order placed."""
    from tastytrade.account import Account

    from .tools import market, strategy

    fixture = _load_fixture(getattr(config, "mock_fixture", None))
    account = MockAccount(fixture)

    async def fake_get(_session, number=None):
        return account if number else [account]

    metrics_override = fixture.get("metrics", {})

    async def fake_metrics(_session, symbols):
        out = []
        for s in symbols:
            data = metrics_override.get(s.upper(), {
                "implied_volatility_rank": "0.42",
                "implied_volatility_percentile": "0.55",
                "beta": "1.0",
            })
            out.append(_Record({"symbol": s.upper(), **data}))
        return out

    async def fake_chain(_session, symbol):
        return mock_option_chain(symbol.upper())

    setter(Account, "get", fake_get)
    setter(market, "get_market_metrics", fake_metrics)
    setter(market, "get_option_chain", fake_chain)
    setter(strategy, "get_option_chain", fake_chain)
    return account
