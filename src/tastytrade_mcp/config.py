"""Runtime configuration loaded from environment / .env (non-secret only).

Secrets (client secret, refresh token) are never read from here — they live in
the OS keyring and are accessed via :mod:`tastytrade_mcp.credentials`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load a local .env if present; real environment variables take precedence.
load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Resolved server configuration."""

    sandbox: bool
    enable_live_trading: bool
    force_dry_run: bool
    buying_power_buffer_pct: float
    account_deploy_limit_pct: float
    log_level: str

    # HTTP transport
    cors_origin: str
    rate_limit: str
    http_host: str
    http_port: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            sandbox=_as_bool(os.getenv("TASTYTRADE_SANDBOX"), default=False),
            enable_live_trading=_as_bool(
                os.getenv("ENABLE_LIVE_TRADING"), default=False
            ),
            force_dry_run=_as_bool(os.getenv("FORCE_DRY_RUN"), default=False),
            buying_power_buffer_pct=float(
                os.getenv("BUYING_POWER_BUFFER_PCT", "0")
            ),
            account_deploy_limit_pct=float(
                os.getenv("ACCOUNT_DEPLOY_LIMIT_PCT", "0")
            ),
            log_level=os.getenv("TASTYTRADE_MCP_LOG_LEVEL", "INFO").upper(),
            cors_origin=os.getenv("MCP_CORS_ORIGIN", "http://localhost:3333"),
            rate_limit=os.getenv("MCP_RATE_LIMIT", "120/minute"),
            http_host=os.getenv("MCP_HTTP_HOST", "127.0.0.1"),
            http_port=int(os.getenv("MCP_HTTP_PORT", "7698")),
        )


def get_config() -> Config:
    """Return freshly-resolved configuration."""
    return Config.from_env()
