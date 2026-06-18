from types import SimpleNamespace

from tastytrade_mcp.tools.orders import _evaluate_preflight


def _response(new_bp, *, current_bp="1000", errors=None, warnings=None):
    bpe = SimpleNamespace(
        current_buying_power=current_bp,
        new_buying_power=new_bp,
        change_in_buying_power="-500",
        impact="0.5",
        effect="Debit",
    )
    return SimpleNamespace(
        buying_power_effect=bpe,
        errors=errors or [],
        warnings=warnings or [],
    )


def test_preflight_passes_with_positive_buying_power():
    problems, summary = _evaluate_preflight(_response("500"))
    assert problems == []
    assert summary["new_buying_power"] == "500"


def test_preflight_rejects_negative_buying_power():
    problems, _ = _evaluate_preflight(_response("-250"))
    assert problems
    assert "Insufficient buying power" in problems[0]


def test_preflight_surfaces_api_errors():
    problems, _ = _evaluate_preflight(_response("500", errors=["bad symbol"]))
    assert "bad symbol" in problems


def test_buffer_rejects_when_below_reserve():
    # current 1000, 20% reserve = 200; new 150 < 200 -> rejected
    problems, summary = _evaluate_preflight(
        _response("150", current_bp="1000"), buffer_pct=20.0
    )
    assert problems
    assert "reserve" in problems[0]
    assert summary["required_reserve"] == "200.0"


def test_buffer_allows_when_above_reserve():
    # current 1000, 20% reserve = 200; new 250 >= 200 -> allowed
    problems, _ = _evaluate_preflight(
        _response("250", current_bp="1000"), buffer_pct=20.0
    )
    assert problems == []
