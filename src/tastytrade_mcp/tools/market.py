"""Market data tools: metrics overview and option chains."""

from __future__ import annotations

import logging
from typing import Any

from tastytrade.instruments import get_option_chain
from tastytrade.metrics import get_market_metrics

from ..config import Config
from ..session import get_session
from ._helpers import error_payload, serialize

logger = logging.getLogger(__name__)


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
    async def get_option_chain_tool(symbol: str) -> dict[str, Any]:
        """Retrieve the option chain for an underlying symbol.

        Returns expirations and strikes with their option symbols, grouped by
        expiration date.

        Args:
            symbol: Underlying ticker symbol, e.g. "SPY".
        """
        try:
            session = get_session(config)
            chain = await get_option_chain(session, symbol.upper())
            # chain maps expiration date -> list of Option instruments.
            serialized = {
                str(expiration): serialize(options)
                for expiration, options in chain.items()
            }
            return {"ok": True, "symbol": symbol.upper(), "chain": serialized}
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_option_chain failed: %s", exc)
            return error_payload(exc)
