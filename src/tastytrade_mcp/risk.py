"""Account-derived deployment limit.

Enforces a ceiling on how much of the account's buying power may be deployed at
once, measured from **live account state** rather than an in-memory counter. This
makes the limit correct across server restarts and multiple server instances,
and keeps it honest with reality — it reflects the buying power actually consumed
by current positions (``used_derivative_buying_power``), not just orders this
process happens to have placed.

Total capacity = currently used buying power + currently available buying power.
The limit is ``limit_pct`` percent of that capacity. An order is allowed only if
the resulting deployed buying power stays at or below the limit.
"""

from __future__ import annotations

from decimal import Decimal


def evaluate_deploy_limit(
    used_bp: Decimal,
    available_bp: Decimal,
    consume: Decimal,
    limit_pct: float,
) -> tuple[bool, dict[str, object]]:
    """Check whether deploying ``consume`` more buying power stays within the cap.

    Args:
        used_bp: Buying power already deployed by live positions.
        available_bp: Buying power currently available.
        consume: Buying power this order would consume (positive for a debit,
            negative for a credit/closing order).
        limit_pct: Maximum percent of total capacity that may be deployed.

    Returns ``(allowed, info)``.
    """
    capacity = used_bp + available_bp
    limit = capacity * Decimal(str(limit_pct)) / Decimal(100)
    projected = used_bp + consume
    if projected < 0:
        projected = Decimal(0)
    allowed = projected <= limit
    info: dict[str, object] = {
        "account_buying_power_capacity": str(capacity),
        "account_deployed_current": str(used_bp),
        "account_deployed_after": str(projected),
        "account_deploy_limit": str(limit),
        "account_deploy_limit_pct": limit_pct,
    }
    return allowed, info
