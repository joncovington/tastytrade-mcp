"""Order tools.

`get_working_orders` is always available (read-only). The order-placing tools
(`execute_trade`, `close_position`, `adjust_order`) are registered ONLY when
``config.enable_live_trading`` is true, so an autonomous agent cannot place real
orders without an explicit opt-in.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from tastytrade.order import (
    NewOrder,
    OrderAction,
    OrderTimeInForce,
    OrderType,
)

from ..config import Config
from ..risk import evaluate_deploy_limit
from ..session import get_session
from ._helpers import error_payload, get_account, serialize

logger = logging.getLogger(__name__)


def register(mcp, config: Config) -> None:
    @mcp.tool()
    async def get_working_orders(account_number: str | None = None) -> dict[str, Any]:
        """List live (working / unfilled) orders for an account.

        Args:
            account_number: Specific account to query. Defaults to the stored
                default account, or the first account on the session.
        """
        try:
            account = await get_account(config, account_number)
            session = get_session(config)
            orders = await account.get_live_orders(session)
            return {
                "ok": True,
                "account_number": account.account_number,
                "orders": serialize(orders),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_working_orders failed: %s", exc)
            return error_payload(exc)

    if not config.enable_live_trading:
        logger.info(
            "Live trading disabled; order-placing tools are not registered. "
            "Set ENABLE_LIVE_TRADING=true to enable them."
        )
        return

    # ---- Gated, order-placing tools below ----

    @mcp.tool()
    async def execute_trade(
        order: dict[str, Any],
        account_number: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Place an options/equity order (single or multi-leg).

        Args:
            order: Order spec with keys: ``time_in_force`` ("Day"/"GTC"),
                ``order_type`` ("Limit"/"Market"/"Stop"/"Stop Limit"), ``price``
                (signed limit; negative for a net credit), optional
                ``price_effect`` ("Debit"/"Credit", applied to the sign of
                ``price``), optional ``stop_trigger`` (trigger price for stop /
                stop-limit orders), and ``legs`` — a list of
                {instrument_type, symbol, quantity, action}. ``action`` is one of
                "Buy to Open", "Sell to Open", "Buy to Close", "Sell to Close".
            account_number: Account to trade in. Defaults to the stored default.
            dry_run: When true (default), validate the order without submitting
                it and return the projected buying-power effect and fees. Ignored
                (forced true) when the server runs with FORCE_DRY_RUN=true.
        """
        try:
            account = await get_account(config, account_number)
            session = get_session(config)
            new_order = _build_order(order)

            # Pre-flight: always run a dry-run first to validate buying power.
            preflight = await account.place_order(session, new_order, dry_run=True)
            problems, bp_summary = _evaluate_preflight(
                preflight, config.buying_power_buffer_pct
            )
            cap_problems, cap_info = await _account_cap_check(
                config, account, session, bp_summary
            )
            problems += cap_problems
            bp_summary.update(cap_info)

            effective_dry_run = dry_run or config.force_dry_run

            if problems:
                logger.info("execute_trade rejected by pre-flight: %s", problems)
                return {
                    "ok": False,
                    "error": "pre-flight validation failed",
                    "problems": problems,
                    "buying_power": bp_summary,
                }

            if effective_dry_run:
                return {
                    "ok": True,
                    "dry_run": True,
                    "forced_dry_run": config.force_dry_run and not dry_run,
                    "account_number": account.account_number,
                    "buying_power": bp_summary,
                    "response": serialize(preflight),
                }

            # Pre-flight passed and live submission requested.
            response = await account.place_order(session, new_order, dry_run=False)
            return {
                "ok": True,
                "dry_run": False,
                "account_number": account.account_number,
                "buying_power": bp_summary,
                "response": serialize(response),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("execute_trade failed: %s", exc)
            return error_payload(exc)

    @mcp.tool()
    async def adjust_order(
        order_id: int,
        order: dict[str, Any],
        account_number: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Replace a working order with new parameters (e.g. a new limit price).

        Args:
            order_id: The id of the existing live order to replace.
            order: A full order spec (same shape as ``execute_trade``).
            account_number: Account holding the order.
            dry_run: Validate without submitting when true (default). Ignored
                (forced true) when the server runs with FORCE_DRY_RUN=true.
        """
        try:
            account = await get_account(config, account_number)
            session = get_session(config)
            new_order = _build_order(order)

            # Pre-flight: validate buying power via a dry-run replacement.
            preflight = await account.replace_order(
                session, order_id, new_order, dry_run=True
            )
            problems, bp_summary = _evaluate_preflight(
                preflight, config.buying_power_buffer_pct
            )
            cap_problems, cap_info = await _account_cap_check(
                config, account, session, bp_summary
            )
            problems += cap_problems
            bp_summary.update(cap_info)

            effective_dry_run = dry_run or config.force_dry_run

            if problems:
                logger.info("adjust_order rejected by pre-flight: %s", problems)
                return {
                    "ok": False,
                    "error": "pre-flight validation failed",
                    "problems": problems,
                    "buying_power": bp_summary,
                }

            if effective_dry_run:
                return {
                    "ok": True,
                    "dry_run": True,
                    "forced_dry_run": config.force_dry_run and not dry_run,
                    "order_id": order_id,
                    "buying_power": bp_summary,
                    "response": serialize(preflight),
                }

            response = await account.replace_order(
                session, order_id, new_order, dry_run=False
            )
            return {
                "ok": True,
                "dry_run": False,
                "order_id": order_id,
                "buying_power": bp_summary,
                "response": serialize(response),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("adjust_order failed: %s", exc)
            return error_payload(exc)

    @mcp.tool()
    async def close_position(
        order_id: int,
        account_number: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a working order by id.

        Note: to flatten an open position, place an offsetting order via
        ``execute_trade`` with closing actions. This tool cancels a pending
        order.

        Args:
            order_id: The id of the live order to cancel.
            account_number: Account holding the order.
        """
        try:
            account = await get_account(config, account_number)
            session = get_session(config)
            await account.delete_order(session, order_id)
            return {"ok": True, "cancelled_order_id": order_id}
        except Exception as exc:  # noqa: BLE001
            logger.warning("close_position failed: %s", exc)
            return error_payload(exc)


def _evaluate_preflight(
    response: Any, buffer_pct: float = 0.0
) -> tuple[list[str], dict[str, Any]]:
    """Inspect a dry-run order response for blocking problems.

    Returns ``(problems, summary)``. ``problems`` is empty when the order is
    safe to submit. The buying-power check rejects orders that would leave the
    account's projected buying power below the required reserve, where the
    reserve is ``buffer_pct`` percent of the *current* buying power (so at least
    that fraction is always kept free). A ``buffer_pct`` of 0 only rejects orders
    that would drive buying power negative.
    """
    problems: list[str] = [str(e) for e in (getattr(response, "errors", None) or [])]
    bpe = getattr(response, "buying_power_effect", None)

    summary: dict[str, Any] = {
        "warnings": [str(w) for w in (getattr(response, "warnings", None) or [])],
    }
    if bpe is not None:
        current = getattr(bpe, "current_buying_power", None)
        new = getattr(bpe, "new_buying_power", None)
        change = getattr(bpe, "change_in_buying_power", None)
        summary.update(
            {
                "current_buying_power": str(current) if current is not None else None,
                "new_buying_power": str(new) if new is not None else None,
                "change_in_buying_power": str(change) if change is not None else None,
                "impact": getattr(bpe, "impact", None),
                "effect": getattr(bpe, "effect", None),
            }
        )
        if new is not None:
            try:
                new_bp = Decimal(str(new))
                reserve = Decimal("0")
                if buffer_pct > 0 and current is not None:
                    reserve = (
                        Decimal(str(current)) * Decimal(str(buffer_pct)) / Decimal(100)
                    )
                    summary["required_reserve"] = str(reserve)
                    summary["buffer_pct"] = buffer_pct
                if new_bp < reserve:
                    if reserve > 0:
                        problems.append(
                            "Insufficient buying power: order would leave buying "
                            f"power at {new}, below the required {buffer_pct}% "
                            f"reserve of {reserve} (current {current})."
                        )
                    else:
                        problems.append(
                            "Insufficient buying power: order would leave buying "
                            f"power at {new} (current {current})."
                        )
            except (ArithmeticError, ValueError):  # pragma: no cover - defensive
                pass

    return problems, summary


def _decimal(value: Any) -> Decimal:
    """Parse a possibly-None / wrapped numeric value into a Decimal."""
    if value is None:
        return Decimal(0)
    return Decimal(str(value))


async def _account_cap_check(
    config: Config, account: Any, session: Any, bp_summary: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    """Check a candidate order against the account-derived deployment cap.

    Reads live balances (``used_derivative_buying_power`` /
    ``derivative_buying_power``) so the limit reflects buying power actually
    consumed by current positions. Returns ``(problems, info)``; disabled (no
    problems) when ``account_deploy_limit_pct`` is 0 or the change is unknown.
    """
    if config.account_deploy_limit_pct <= 0:
        return [], {}
    change = bp_summary.get("change_in_buying_power")
    if change is None:
        return [], {}

    balances = await account.get_balances(session)
    used = _decimal(getattr(balances, "used_derivative_buying_power", None))
    available = _decimal(getattr(balances, "derivative_buying_power", None))
    consume = -_decimal(change)  # debit (negative change) consumes buying power

    allowed, info = evaluate_deploy_limit(
        used, available, consume, config.account_deploy_limit_pct
    )
    if not allowed:
        return [
            "Account deploy limit exceeded: order would bring deployed buying "
            f"power to {info['account_deployed_after']}, above the "
            f"{config.account_deploy_limit_pct}% cap of "
            f"{info['account_deploy_limit']} (capacity "
            f"{info['account_buying_power_capacity']})."
        ], info
    return [], info


_ACTION_MAP = {
    "buy to open": OrderAction.BUY_TO_OPEN,
    "sell to open": OrderAction.SELL_TO_OPEN,
    "buy to close": OrderAction.BUY_TO_CLOSE,
    "sell to close": OrderAction.SELL_TO_CLOSE,
}


def _build_order(spec: dict[str, Any]) -> NewOrder:
    """Translate a JSON order spec into a tastytrade NewOrder.

    Supports limit and stop-limit orders. Debit/credit direction is conveyed by
    the sign of ``price`` (negative = credit, positive = debit). For ergonomics
    the spec may instead pass ``price_effect`` ("Debit"/"Credit"), which is
    applied to the sign of ``price`` — note ``NewOrder`` has no ``price_effect``
    field in this SDK version, so it must not be passed through directly.
    """
    from tastytrade.order import Leg

    tif = OrderTimeInForce(str(spec.get("time_in_force", "Day")))
    otype = OrderType(str(spec.get("order_type", "Limit")))
    legs: list[Leg] = []
    for leg in spec.get("legs", []):
        action = _ACTION_MAP[str(leg["action"]).strip().lower()]
        legs.append(
            Leg(
                instrument_type=leg["instrument_type"],
                symbol=leg["symbol"],
                action=action,
                quantity=Decimal(str(leg["quantity"])),
            )
        )
    kwargs: dict[str, Any] = {
        "time_in_force": tif,
        "order_type": otype,
        "legs": legs,
    }
    if spec.get("price") is not None:
        price = Decimal(str(spec["price"]))
        effect = spec.get("price_effect")
        if effect is not None:
            # Apply direction via the sign of price; "Credit" => negative.
            magnitude = abs(price)
            price = -magnitude if str(effect).strip().lower() == "credit" else magnitude
        kwargs["price"] = price

    # Stop / stop-limit trigger price.
    if spec.get("stop_trigger") is not None:
        kwargs["stop_trigger"] = Decimal(str(spec["stop_trigger"]))

    return NewOrder(**kwargs)
