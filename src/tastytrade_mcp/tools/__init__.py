"""MCP tool modules.

Each module exposes a ``register(mcp, config)`` function that attaches its tools
to the given FastMCP instance. Read-only tools register unconditionally; modules
with order-placing tools check ``config.enable_live_trading`` before registering
them.
"""

from . import account, market, orders, status, strategy, watchlists

REGISTRARS = (
    status.register,
    market.register,
    strategy.register,
    account.register,
    orders.register,
    watchlists.register,
)


def register_all(mcp, config) -> None:
    for registrar in REGISTRARS:
        registrar(mcp, config)


__all__ = ["register_all"]
