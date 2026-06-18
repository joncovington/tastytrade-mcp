"""Account tools: account info, balances, positions."""

from __future__ import annotations

import logging
from typing import Any

from tastytrade.account import Account

from ..config import Config
from ..session import get_session
from ._helpers import error_payload, get_account, serialize

logger = logging.getLogger(__name__)


def register(mcp, config: Config) -> None:
    @mcp.tool()
    async def get_account_info(account_number: str | None = None) -> dict[str, Any]:
        """Retrieve account balances and buying power.

        Args:
            account_number: Specific account to query. Defaults to the stored
                default account, or the first account on the session.
        """
        try:
            account = await get_account(config, account_number)
            session = get_session(config)
            balances = await account.get_balances(session)
            return {
                "ok": True,
                "account_number": account.account_number,
                "nickname": getattr(account, "nickname", None),
                "balances": serialize(balances),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_account_info failed: %s", exc)
            return error_payload(exc)

    @mcp.tool()
    async def get_positions(account_number: str | None = None) -> dict[str, Any]:
        """List open positions with quantities and P&L for an account.

        Args:
            account_number: Specific account to query. Defaults to the stored
                default account, or the first account on the session.
        """
        try:
            account = await get_account(config, account_number)
            session = get_session(config)
            positions = await account.get_positions(session)
            return {
                "ok": True,
                "account_number": account.account_number,
                "positions": serialize(positions),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_positions failed: %s", exc)
            return error_payload(exc)

    @mcp.tool()
    async def list_accounts() -> dict[str, Any]:
        """List all accounts available to the authenticated session."""
        try:
            session = get_session(config)
            accounts = await Account.get(session)
            summary = [
                {
                    "account_number": a.account_number,
                    "nickname": getattr(a, "nickname", None),
                    "account_type": getattr(a, "account_type_name", None),
                }
                for a in accounts
            ]
            return {"ok": True, "accounts": summary}
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_accounts failed: %s", exc)
            return error_payload(exc)
