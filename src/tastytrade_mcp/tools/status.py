"""Connection / health tools."""

from __future__ import annotations

import logging
from typing import Any

from tastytrade.account import Account

from .. import credentials
from ..config import Config
from ..session import get_session
from ._helpers import error_payload

logger = logging.getLogger(__name__)


def register(mcp, config: Config) -> None:
    @mcp.tool()
    async def get_connection_status() -> dict[str, Any]:
        """Check Tastytrade connectivity and report environment configuration.

        Returns whether credentials are present, the active environment
        (sandbox vs production), whether live trading is enabled, and how many
        accounts the session can see.
        """
        status: dict[str, Any] = {
            "ok": True,
            "environment": "sandbox" if config.sandbox else "production",
            "mock_mode": config.mock_mode,
            "live_trading_enabled": config.enable_live_trading,
            "credentials_present": config.mock_mode
            or credentials.secrets_present(sandbox=config.sandbox),
        }
        if not status["credentials_present"]:
            status["ok"] = False
            status["hint"] = "Run `tastytrade-mcp secrets set` to store credentials."
            return status
        try:
            session = get_session(config)
            accounts = await Account.get(session)
            status["connected"] = True
            status["account_count"] = len(accounts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Connection check failed: %s", exc)
            status["ok"] = False
            status["connected"] = False
            status.update(error_payload(exc))
        return status
