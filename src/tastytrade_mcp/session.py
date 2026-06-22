"""Lazy Tastytrade OAuth session management.

The official ``tastytrade`` SDK (>=12) builds an OAuth2 session from a client
secret + refresh token and refreshes the short-lived (15 min) session token
automatically on every request. We only need to construct it once and reuse it.
"""

from __future__ import annotations

import logging
import threading

from tastytrade import Session

from . import credentials
from .config import Config, get_config

logger = logging.getLogger(__name__)


class CredentialsMissingError(RuntimeError):
    """Raised when required OAuth secrets are not present in the keyring."""


_lock = threading.Lock()
_session: Session | None = None


def _build_session() -> Session:
    missing = credentials.missing_secrets()
    if missing:
        raise CredentialsMissingError(
            f"Missing credentials: {', '.join(missing)}. "
            "Run `tastytrade-mcp secrets set` to store them."
        )
    client_secret = credentials.get_secret(credentials.CLIENT_SECRET)
    refresh_token = credentials.get_secret(credentials.REFRESH_TOKEN)
    logger.info("Building Tastytrade OAuth session")
    return Session(client_secret, refresh_token, is_test=False)


def get_session(config: Config | None = None) -> Session:
    """Return a cached OAuth session, building it on first use. Thread-safe."""
    global _session
    config = config or get_config()  # noqa: F841 - retained for call-site compat
    with _lock:
        if _session is None:
            _session = _build_session()
        return _session


def reset_session() -> None:
    """Drop the cached session (used by tests / after credential changes)."""
    global _session
    with _lock:
        _session = None


def close_session() -> None:
    """Best-effort release of the cached session's HTTP resources on shutdown.

    The OAuth refresh token is long-lived and intentionally NOT invalidated here
    — we only close any open httpx clients so a Ctrl-C exits cleanly without
    leaking sockets or emitting unclosed-client warnings.
    """
    global _session
    with _lock:
        session = _session
        _session = None

    if session is None:
        return

    for attr in ("sync_client", "_sync_client", "client", "_client"):
        client = getattr(session, attr, None)
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - best effort during shutdown
                logger.debug("Error closing client %s during shutdown", attr)
