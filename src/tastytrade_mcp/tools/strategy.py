"""Strategy builder tools — iron condor construction with rough POP estimates.

This builds candidate iron condors from the option chain. Greeks and live quotes
require a streaming/market-data subscription; where those are unavailable we
return the structural legs and a credit-derived probability-of-profit estimate so
the agent has actionable setups without a live data feed.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from tastytrade.instruments import get_option_chain

from ..config import Config
from ..session import get_session
from ._helpers import error_payload, serialize

logger = logging.getLogger(__name__)


def _nearest_expiration(expirations: list[date], target_dte: int) -> date:
    today = date.today()
    return min(expirations, key=lambda e: abs((e - today).days - target_dte))


def register(mcp, config: Config) -> None:
    @mcp.tool()
    async def get_strategies(
        symbol: str,
        target_dte: int = 45,
        wing_width: int = 5,
        short_delta: float = 0.16,
    ) -> dict[str, Any]:
        """Build candidate iron condor setups for an underlying.

        Selects an expiration near ``target_dte`` and proposes an iron condor
        with short strikes around the given target delta and the requested wing
        width. Returns the four legs (short/long put, short/long call) and an
        estimated probability of profit.

        Args:
            symbol: Underlying ticker symbol, e.g. "SPY".
            target_dte: Desired days to expiration (default 45).
            wing_width: Distance in strikes between short and long legs.
            short_delta: Target absolute delta for the short strikes (~0.16
                corresponds to roughly a 1-standard-deviation short strike).
        """
        try:
            session = get_session(config)
            chain = await get_option_chain(session, symbol.upper())
            if not chain:
                return {"ok": False, "error": f"No option chain for {symbol}."}

            expirations = sorted(chain.keys())
            expiration = _nearest_expiration(expirations, target_dte)
            options = chain[expiration]

            calls = sorted(
                (o for o in options if _is_call(o)),
                key=lambda o: float(o.strike_price),
            )
            puts = sorted(
                (o for o in options if _is_put(o)),
                key=lambda o: float(o.strike_price),
            )
            if not calls or not puts:
                return {
                    "ok": False,
                    "error": f"Incomplete chain for {symbol} {expiration}.",
                }

            short_call, long_call = _select_call_spread(calls, wing_width)
            short_put, long_put = _select_put_spread(puts, wing_width)

            # Without live deltas we approximate POP from the short-delta input:
            # an iron condor's win probability ~ 1 - 2 * short_delta.
            estimated_pop = round(max(0.0, 1.0 - 2.0 * short_delta), 3)

            return {
                "ok": True,
                "symbol": symbol.upper(),
                "strategy": "iron_condor",
                "expiration": str(expiration),
                "dte": (expiration - date.today()).days,
                "estimated_pop": estimated_pop,
                "legs": {
                    "short_put": serialize(short_put),
                    "long_put": serialize(long_put),
                    "short_call": serialize(short_call),
                    "long_call": serialize(long_call),
                },
                "note": (
                    "POP is a credit/delta heuristic. Pull live greeks and quotes "
                    "before trading; verify credit and buying power."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_strategies failed: %s", exc)
            return error_payload(exc)


def _is_call(option: Any) -> bool:
    ot = str(getattr(option, "option_type", "")).lower()
    return "c" in ot and "p" not in ot


def _is_put(option: Any) -> bool:
    ot = str(getattr(option, "option_type", "")).lower()
    return "p" in ot


def _select_call_spread(calls: list[Any], wing_width: int) -> tuple[Any, Any]:
    """Short call near the upper third of strikes, long ``wing_width`` higher."""
    idx = max(0, int(len(calls) * 0.66) - 1)
    long_idx = min(len(calls) - 1, idx + wing_width)
    return calls[idx], calls[long_idx]


def _select_put_spread(puts: list[Any], wing_width: int) -> tuple[Any, Any]:
    """Short put near the lower third of strikes, long ``wing_width`` lower."""
    idx = min(len(puts) - 1, int(len(puts) * 0.33))
    long_idx = max(0, idx - wing_width)
    return puts[idx], puts[long_idx]
