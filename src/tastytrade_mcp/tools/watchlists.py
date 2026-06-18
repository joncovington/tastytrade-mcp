"""Watchlist tools.

`get_watchlists` is read-only and always available. `manage_watchlist` mutates
user watchlists and is gated behind ``config.enable_live_trading`` along with the
order tools (it changes account-side state).
"""

from __future__ import annotations

import logging
from typing import Any

from tastytrade.watchlists import PrivateWatchlist

from ..config import Config
from ..session import get_session
from ._helpers import error_payload, serialize

logger = logging.getLogger(__name__)


def register(mcp, config: Config) -> None:
    @mcp.tool()
    async def get_watchlists(name: str | None = None) -> dict[str, Any]:
        """Retrieve the user's private watchlists.

        Args:
            name: If given, return only the watchlist with this name; otherwise
                return all private watchlists.
        """
        try:
            session = get_session(config)
            if name:
                wl = await PrivateWatchlist.get(session, name)
                return {"ok": True, "watchlist": serialize(wl)}
            watchlists = await PrivateWatchlist.get(session)
            return {"ok": True, "watchlists": serialize(watchlists)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_watchlists failed: %s", exc)
            return error_payload(exc)

    if not config.enable_live_trading:
        return

    @mcp.tool()
    async def manage_watchlist(
        action: str,
        name: str,
        symbol: str | None = None,
        instrument_type: str = "Equity",
    ) -> dict[str, Any]:
        """Create, delete, or modify a private watchlist.

        Args:
            action: One of "create", "delete", "add_symbol", "remove_symbol".
            name: Watchlist name.
            symbol: Required for add_symbol / remove_symbol.
            instrument_type: Instrument type for add_symbol (default "Equity").
        """
        try:
            session = get_session(config)
            act = action.strip().lower()
            if act == "create":
                wl = PrivateWatchlist(name=name, watchlist_entries=[])
                await wl.upload(session)
                return {"ok": True, "action": "create", "name": name}
            if act == "delete":
                await PrivateWatchlist.remove(session, name)
                return {"ok": True, "action": "delete", "name": name}

            if not symbol:
                return {"ok": False, "error": f"'{act}' requires a symbol."}
            wl = await PrivateWatchlist.get(session, name)
            if act == "add_symbol":
                wl.add_symbol(symbol.upper(), instrument_type)
            elif act == "remove_symbol":
                wl.remove_symbol(symbol.upper(), instrument_type)
            else:
                return {"ok": False, "error": f"Unknown action '{action}'."}
            await wl.update(session)
            return {
                "ok": True,
                "action": act,
                "name": name,
                "symbol": symbol.upper(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("manage_watchlist failed: %s", exc)
            return error_payload(exc)
