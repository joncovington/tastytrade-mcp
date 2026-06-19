"""FastMCP server wiring and transport selection."""

from __future__ import annotations

import logging
import signal

from mcp.server.fastmcp import FastMCP

from .config import Config, get_config
from .logging_utils import configure_logging
from .session import close_session
from .tools import register_all

logger = logging.getLogger(__name__)


def build_server(config: Config | None = None) -> FastMCP:
    """Construct the FastMCP server with all tools registered."""
    config = config or get_config()
    if config.mock_mode:
        from .mocks import install_mocks

        install_mocks(config)
        logger.warning(
            "MOCK MODE: serving simulated SDK responses — no real Tastytrade "
            "connection, no credentials used, orders are never submitted."
        )
    mcp = FastMCP(
        "tastytrade-mcp",
        instructions=(
            "Tools to connect to Tastytrade: scan markets, build option "
            "strategies, inspect accounts/positions/orders, and (when live "
            "trading is enabled) place and manage orders. Default account is "
            "used when none is supplied. Order-placing tools are only present "
            "when the server was started with live trading enabled."
        ),
    )
    register_all(mcp, config)
    return mcp


def run(transport: str = "stdio", config: Config | None = None) -> None:
    """Run the server with the chosen transport ("stdio" or "http")."""
    config = config or get_config()
    configure_logging(config.log_level)
    logger.info(
        "Starting tastytrade-mcp (transport=%s, environment=%s, live_trading=%s)",
        transport,
        "sandbox" if config.sandbox else "production",
        config.enable_live_trading,
    )

    mcp = build_server(config)

    # Translate SIGTERM into the same KeyboardInterrupt path as Ctrl-C so both
    # trigger a single graceful shutdown. Best-effort: signal handlers can only
    # be installed from the main thread.
    def _handle_term(signum, _frame):  # pragma: no cover - signal plumbing
        raise KeyboardInterrupt()

    try:
        signal.signal(signal.SIGTERM, _handle_term)
    except (ValueError, OSError):  # not main thread / unsupported platform
        pass

    try:
        if transport == "http":
            from .http_app import run_http

            run_http(mcp, config)
        else:
            mcp.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt (Ctrl-C); shutting down gracefully.")
    finally:
        close_session()
        logger.info("tastytrade-mcp stopped.")


if __name__ == "__main__":
    run()
