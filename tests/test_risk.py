from decimal import Decimal

from tastytrade_mcp.risk import evaluate_deploy_limit


def test_allows_within_account_limit():
    # capacity = used 200 + available 800 = 1000; 50% cap = 500.
    # consume 100 -> projected 300 <= 500 -> allowed.
    allowed, info = evaluate_deploy_limit(
        Decimal("200"), Decimal("800"), Decimal("100"), 50.0
    )
    assert allowed
    assert info["account_buying_power_capacity"] == "1000"
    assert info["account_deploy_limit"] == "500.0"
    assert info["account_deployed_after"] == "300"


def test_rejects_when_existing_positions_already_near_limit():
    # Already 450 used of a 500 limit; a 100 debit -> 550 > 500 -> rejected.
    # This is the key account-derived case: live positions count even if this
    # process placed none of them.
    allowed, info = evaluate_deploy_limit(
        Decimal("450"), Decimal("550"), Decimal("100"), 50.0
    )
    assert not allowed
    assert info["account_deployed_after"] == "550"


def test_credit_order_reduces_deployed():
    # A closing/credit order frees buying power (negative consume).
    allowed, info = evaluate_deploy_limit(
        Decimal("400"), Decimal("600"), Decimal("-150"), 50.0
    )
    assert allowed
    assert info["account_deployed_after"] == "250"


def test_projected_floored_at_zero():
    allowed, info = evaluate_deploy_limit(
        Decimal("100"), Decimal("900"), Decimal("-500"), 50.0
    )
    assert allowed
    assert info["account_deployed_after"] == "0"
