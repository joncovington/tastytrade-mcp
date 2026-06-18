from decimal import Decimal

from tastytrade_mcp.tools.orders import _build_order

_LEG = {
    "instrument_type": "Equity Option",
    "symbol": "SPY   260116C00500000",
    "quantity": 1,
    "action": "Sell to Open",
}


def test_stop_limit_sets_trigger():
    order = _build_order(
        {
            "time_in_force": "GTC",
            "order_type": "Stop Limit",
            "stop_trigger": 0.72,
            "price": 0.76,
            "price_effect": "Debit",
            "legs": [_LEG],
        }
    )
    assert order.order_type.value == "Stop Limit"
    assert order.stop_trigger == Decimal("0.72")
    assert order.price == Decimal("0.76")  # Debit -> positive


def test_price_effect_credit_makes_price_negative():
    order = _build_order(
        {"order_type": "Limit", "price": 1.50, "price_effect": "Credit", "legs": []}
    )
    assert order.price == Decimal("-1.50")


def test_raw_price_sign_preserved_without_effect():
    order = _build_order({"order_type": "Limit", "price": -1.50, "legs": []})
    assert order.price == Decimal("-1.50")


def test_no_stop_trigger_when_absent():
    order = _build_order({"order_type": "Limit", "price": 1.0, "legs": []})
    assert order.stop_trigger is None
