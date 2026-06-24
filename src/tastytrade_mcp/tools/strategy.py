"""Strategy builder tools — iron condor construction with POP and credit estimates."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from tastytrade.instruments import get_option_chain

from ..config import Config
from ..session import get_session
from ._helpers import error_payload, serialize
from .market import _atm_window, _collect_greeks, _collect_quotes, _num

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
        around_price: float | None = None,
        greeks_timeout: float = 6.0,
        quotes_timeout: float = 6.0,
    ) -> dict[str, Any]:
        """Build candidate iron condor setups for an underlying.

        Selects an expiration near ``target_dte`` and proposes an iron condor
        with short strikes around the given target delta and the requested wing
        width. Returns the four legs (short/long put, short/long call), an
        estimated probability of profit, and a live net credit estimate derived
        from bid/ask midpoints on each leg.

        Short strike selection uses live greeks from the DXLink feed when
        available (recommended — pass ``around_price`` to center the greeks
        window on the underlying's last price). Falls back to a positional
        heuristic when greeks are unavailable.

        ``net_credit`` is the per-share credit (multiply by 100 for the contract
        dollar value). It is ``null`` when quotes are unavailable — in that case
        use ``get_option_chain`` with ``include_quotes=true`` on the specific
        expiration to get individual leg prices.

        Args:
            symbol: Underlying ticker symbol, e.g. "SPY".
            target_dte: Desired days to expiration (default 45).
            wing_width: Distance in strikes between short and long legs.
            short_delta: Target absolute delta for the short strikes (~0.16
                corresponds to roughly a 1-standard-deviation short strike).
            around_price: Underlying last price — centers the greeks window
                for accurate delta-based strike selection. Pass this from
                ``get_market_overview`` for best results.
            greeks_timeout: Seconds to wait for live greeks from DXLink.
            quotes_timeout: Seconds to wait for bid/ask quotes from DXLink
                before returning without a credit estimate.
        """
        try:
            session = get_session(config)
            chain = await get_option_chain(session, symbol.upper())
            if not chain:
                return {"ok": False, "error": f"No option chain for {symbol}."}

            expirations = sorted(chain.keys())
            expiration = _nearest_expiration(expirations, target_dte)
            options = list(chain[expiration])

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

            # Fetch live greeks for a window of strikes to drive delta selection.
            # Use a 40-strike window each side to cover typical 0.10–0.30 delta
            # targets while keeping the subscription manageable.
            window = _atm_window(options, strike_count=40, around_price=around_price)
            window_symbols = [
                getattr(o, "streamer_symbol", None) for o in window
                if getattr(o, "streamer_symbol", None)
            ]
            greeks = await _collect_greeks(session, window_symbols, greeks_timeout)

            short_call, long_call = _select_call_spread(calls, wing_width, short_delta, greeks)
            short_put, long_put = _select_put_spread(puts, wing_width, short_delta, greeks)
            greeks_used = bool(greeks)

            # Without live deltas we approximate POP from the short-delta input:
            # an iron condor's win probability ~ 1 - 2 * short_delta.
            estimated_pop = round(max(0.0, 1.0 - 2.0 * short_delta), 3)

            # Stream bid/ask quotes for the four legs to estimate net credit.
            legs = [short_put, long_put, short_call, long_call]
            streamer_symbols = [
                getattr(leg, "streamer_symbol", None) for leg in legs
            ]
            quotes = await _collect_quotes(
                session, [s for s in streamer_symbols if s], quotes_timeout
            )

            def _mid(leg: Any) -> float | None:
                sym = getattr(leg, "streamer_symbol", None)
                q = quotes.get(sym) if sym else None
                if q is None:
                    return None
                bid, ask = _num(q.bid_price), _num(q.ask_price)
                return round((bid + ask) / 2, 4) if bid is not None and ask is not None else None

            short_put_mid = _mid(short_put)
            long_put_mid = _mid(long_put)
            short_call_mid = _mid(short_call)
            long_call_mid = _mid(long_call)

            mids = [short_put_mid, long_put_mid, short_call_mid, long_call_mid]
            net_credit: float | None = None
            if all(m is not None for m in mids):
                net_credit = round(
                    (short_put_mid + short_call_mid) - (long_put_mid + long_call_mid), 4  # type: ignore[operator]
                )

            def _leg(option: Any, mid: float | None) -> dict[str, Any]:
                data = serialize(option)
                base: dict[str, Any] = data if isinstance(data, dict) else {"symbol": str(data)}
                return {**base, "mid": mid}

            return {
                "ok": True,
                "symbol": symbol.upper(),
                "strategy": "iron_condor",
                "expiration": str(expiration),
                "dte": (expiration - date.today()).days,
                "estimated_pop": estimated_pop,
                "net_credit": net_credit,
                "net_credit_per_contract": round(net_credit * 100, 2) if net_credit is not None else None,
                "quotes_complete": all(m is not None for m in mids),
                "greeks_used_for_strike_selection": greeks_used,
                "legs": {
                    "short_put": _leg(short_put, short_put_mid),
                    "long_put": _leg(long_put, long_put_mid),
                    "short_call": _leg(short_call, short_call_mid),
                    "long_call": _leg(long_call, long_call_mid),
                },
                "note": (
                    "POP is a delta heuristic. net_credit is derived from live "
                    "bid/ask midpoints — verify before trading."
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


def _select_call_spread(
    calls: list[Any], wing_width: int, short_delta: float, greeks: dict[str, Any]
) -> tuple[Any, Any]:
    """Short call nearest to +short_delta; long leg wing_width strikes higher.

    Falls back to the upper-third positional heuristic when greeks are absent.
    """
    if greeks:
        best = _closest_by_delta(calls, short_delta, greeks)
        if best is not None:
            idx = calls.index(best)
            long_idx = min(len(calls) - 1, idx + wing_width)
            return calls[idx], calls[long_idx]
    # Fallback: positional heuristic (upper third of the sorted call list).
    idx = max(0, int(len(calls) * 0.66) - 1)
    long_idx = min(len(calls) - 1, idx + wing_width)
    return calls[idx], calls[long_idx]


def _select_put_spread(
    puts: list[Any], wing_width: int, short_delta: float, greeks: dict[str, Any]
) -> tuple[Any, Any]:
    """Short put nearest to -short_delta; long leg wing_width strikes lower.

    Falls back to the lower-third positional heuristic when greeks are absent.
    """
    if greeks:
        best = _closest_by_delta(puts, -short_delta, greeks)
        if best is not None:
            idx = puts.index(best)
            long_idx = max(0, idx - wing_width)
            return puts[idx], puts[long_idx]
    # Fallback: positional heuristic (lower third of the sorted put list).
    idx = min(len(puts) - 1, int(len(puts) * 0.33))
    long_idx = max(0, idx - wing_width)
    return puts[idx], puts[long_idx]


def _closest_by_delta(
    options: list[Any], target_delta: float, greeks: dict[str, Any]
) -> Any | None:
    """Return the option whose live delta is closest to target_delta, or None."""
    best: Any = None
    best_diff = float("inf")
    for o in options:
        sym = getattr(o, "streamer_symbol", None)
        g = greeks.get(sym) if sym else None
        if g is None:
            continue
        delta = _num(getattr(g, "delta", None))
        if delta is None:
            continue
        diff = abs(delta - target_delta)
        if diff < best_diff:
            best_diff = diff
            best = o
    return best
