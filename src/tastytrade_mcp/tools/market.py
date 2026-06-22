"""Market data tools: metrics overview and option chains."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from tastytrade.instruments import get_option_chain
from tastytrade.metrics import get_market_metrics

from ..config import Config
from ..session import get_session
from ._helpers import error_payload, serialize

logger = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    """Coerce a greek/IV/price value to a float, or None if unavailable."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


async def _collect_greeks(
    session: Any, streamer_symbols: list[str], timeout: float
) -> dict[str, Any]:
    """Stream live Greeks for the given DXLink streamer symbols.

    Returns a mapping of ``event_symbol -> Greeks``. Best-effort: on timeout or
    streaming error it returns whatever greeks arrived (possibly empty) so the
    caller can still serve the chain without greeks.
    """
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Greeks

    out: dict[str, Any] = {}
    symbols = [s for s in streamer_symbols if s]
    if not symbols:
        return out

    async def _drain(streamer) -> None:
        remaining = set(symbols)
        async for event in streamer.listen(Greeks):
            out[event.event_symbol] = event
            remaining.discard(event.event_symbol)
            if not remaining:
                return

    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Greeks, symbols)
            await asyncio.wait_for(_drain(streamer), timeout=timeout)
    except asyncio.TimeoutError:
        logger.info(
            "greeks collection timed out (%d/%d received)", len(out), len(symbols)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("greeks collection failed: %s", exc)
    return out


async def _collect_quotes(
    session: Any, streamer_symbols: list[str], timeout: float
) -> dict[str, Any]:
    """Stream live bid/ask quotes for the given DXLink streamer symbols.

    Returns a mapping of ``event_symbol -> Quote``. Best-effort: on timeout or
    streaming error it returns whatever quotes arrived (possibly empty).
    """
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Quote

    out: dict[str, Any] = {}
    symbols = [s for s in streamer_symbols if s]
    if not symbols:
        return out

    async def _drain(streamer) -> None:
        remaining = set(symbols)
        async for event in streamer.listen(Quote):
            out[event.event_symbol] = event
            remaining.discard(event.event_symbol)
            if not remaining:
                return

    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Quote, symbols)
            await asyncio.wait_for(_drain(streamer), timeout=timeout)
    except asyncio.TimeoutError:
        logger.info(
            "quotes collection timed out (%d/%d received)", len(out), len(symbols)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("quotes collection failed: %s", exc)
    return out


def _nearest_expiration(expirations: list[date]) -> date:
    today = date.today()
    return min(expirations, key=lambda e: abs((e - today).days))


def _strike(option: Any) -> float | None:
    try:
        return float(option.strike_price)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _atm_window(
    options: list[Any], strike_count: int, around_price: float | None
) -> list[Any]:
    """Keep only the strikes within ``strike_count`` of the money.

    The center is ``around_price`` when given (pass the underlying's last price),
    otherwise the median strike — a reasonable ATM proxy for a centered chain.
    Both calls and puts at each kept strike are retained.
    """
    strikes = sorted({s for s in (_strike(o) for o in options) if s is not None})
    if not strikes:
        return options
    center = around_price if around_price is not None else strikes[len(strikes) // 2]
    nearest = min(range(len(strikes)), key=lambda i: abs(strikes[i] - center))
    lo = max(0, nearest - strike_count)
    hi = min(len(strikes), nearest + strike_count + 1)
    keep = set(strikes[lo:hi])
    return [o for o in options if _strike(o) in keep]


def register(mcp, config: Config) -> None:
    @mcp.tool()
    async def get_market_overview(symbols: list[str]) -> dict[str, Any]:
        """Scan symbols for market metrics.

        Returns implied volatility rank/percentile, IV, beta, liquidity, and
        upcoming earnings (when available) for each underlying symbol.

        Args:
            symbols: Underlying ticker symbols, e.g. ["SPY", "QQQ", "AAPL"].
        """
        try:
            session = get_session(config)
            metrics = await get_market_metrics(session, [s.upper() for s in symbols])
            return {"ok": True, "metrics": serialize(metrics)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_market_overview failed: %s", exc)
            return error_payload(exc)

    @mcp.tool(name="get_option_chain")
    async def get_option_chain_tool(
        symbol: str,
        expiration: str | None = None,
        include_greeks: bool = False,
        include_quotes: bool = False,
        strike_count: int | None = 15,
        around_price: float | None = None,
        greeks_timeout: float = 6.0,
        quotes_timeout: float = 6.0,
    ) -> dict[str, Any]:
        """Retrieve the option chain for an underlying symbol.

        Returns expirations and strikes (with option symbols) grouped by
        expiration date. Each strike entry carries the instrument fields plus,
        when requested, live per-strike greeks and/or bid/ask quotes.

        Args:
            symbol: Underlying ticker symbol, e.g. "SPY".
            expiration: ISO date (YYYY-MM-DD) to return only that expiration.
                Recommended with ``include_greeks`` or ``include_quotes`` to
                bound the data fetched.
            include_greeks: When true, stream live greeks and merge ``delta``,
                ``gamma``, ``theta``, and ``iv`` into each strike. Adds latency;
                greeks come from the DXLink feed, not the chain endpoint. If the
                feed is unavailable, the chain is still returned and
                ``greeks_complete`` is false.
            include_quotes: When true, stream live bid/ask quotes and merge
                ``bid``, ``ask``, and ``mid`` (midpoint) into each strike. Same
                DXLink feed as greeks. If unavailable, ``quotes_complete`` is
                false but the chain is still returned.
            strike_count: Keep only this many strikes on each side of the money
                (an ATM window). Defaults to 15, which keeps the subscription
                small and fast. Pass null/None to return the full chain.
            around_price: Underlying price to center the ATM window on (pass the
                last price from get_market_overview). Defaults to the median
                strike when omitted.
            greeks_timeout: Seconds to wait for greeks before returning partial.
            quotes_timeout: Seconds to wait for quotes before returning partial.
        """
        try:
            session = get_session(config)
            chain = await get_option_chain(session, symbol.upper())
            if not chain:
                return {"ok": False, "error": f"No option chain for {symbol}."}

            # Resolve the expiration filter. With streaming data and no explicit
            # date, default to the nearest expiration to bound the subscription.
            expirations = sorted(chain.keys())
            selected: list[date]
            if expiration:
                want = date.fromisoformat(expiration)
                if want not in chain:
                    return {
                        "ok": False,
                        "error": f"No {expiration} expiration for {symbol}.",
                        "available_expirations": [str(e) for e in expirations],
                    }
                selected = [want]
            elif include_greeks or include_quotes:
                selected = [_nearest_expiration(expirations)]
            else:
                selected = expirations

            # Optionally trim each expiration to an ATM window of strikes.
            options_by_exp = {exp: list(chain[exp]) for exp in selected}
            if strike_count is not None:
                options_by_exp = {
                    exp: _atm_window(opts, strike_count, around_price)
                    for exp, opts in options_by_exp.items()
                }

            serialized = {
                str(exp): [_entry(o) for o in opts]
                for exp, opts in options_by_exp.items()
            }

            result: dict[str, Any] = {
                "ok": True,
                "symbol": symbol.upper(),
                "chain": serialized,
            }

            # Collect streamer symbols once; reuse for both greeks and quotes.
            streamer_symbols = [
                o.streamer_symbol
                for opts in options_by_exp.values()
                for o in opts
                if getattr(o, "streamer_symbol", None)
            ]

            if include_greeks:
                greeks = await _collect_greeks(session, streamer_symbols, greeks_timeout)
                received = 0
                for entries in serialized.values():
                    for entry in entries:
                        g = greeks.get(entry.get("streamer_symbol"))
                        if g is not None:
                            entry["delta"] = _num(g.delta)
                            entry["gamma"] = _num(g.gamma)
                            entry["theta"] = _num(g.theta)
                            entry["iv"] = _num(g.volatility)
                            received += 1
                result["greeks_included"] = True
                result["greeks_complete"] = received == len(streamer_symbols)
                result["greeks_received"] = received

            if include_quotes:
                quotes = await _collect_quotes(session, streamer_symbols, quotes_timeout)
                received = 0
                for entries in serialized.values():
                    for entry in entries:
                        q = quotes.get(entry.get("streamer_symbol"))
                        if q is not None:
                            bid = _num(q.bid_price)
                            ask = _num(q.ask_price)
                            entry["bid"] = bid
                            entry["ask"] = ask
                            entry["mid"] = (
                                round((bid + ask) / 2, 4)
                                if bid is not None and ask is not None
                                else None
                            )
                            received += 1
                result["quotes_included"] = True
                result["quotes_complete"] = received == len(streamer_symbols)
                result["quotes_received"] = received

            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_option_chain failed: %s", exc)
            return error_payload(exc)


def _entry(option: Any) -> dict[str, Any]:
    """Serialize one option instrument to a plain dict (greeks/quotes merged later)."""
    data = serialize(option)
    if isinstance(data, dict):
        return data
    # Fallback for non-Pydantic fakes/instruments.
    return {
        "symbol": getattr(option, "symbol", None),
        "strike_price": str(getattr(option, "strike_price", "")),
        "option_type": getattr(option, "option_type", None),
        "streamer_symbol": getattr(option, "streamer_symbol", None),
    }
