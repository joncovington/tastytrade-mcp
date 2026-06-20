"""HTTP transport for the MCP server, hardened with CORS and rate limiting.

Wraps FastMCP's streamable-HTTP ASGI app behind Starlette middleware:

- CORS is restricted to the single ``MCP_CORS_ORIGIN`` (default
  ``http://localhost:3333``).
- A per-IP rate limit (default 120 requests/minute) is applied to all MCP
  endpoints, returning HTTP 429 when exceeded.

The rate limiter is a small fixed-window per-IP counter implemented directly as
Starlette middleware. (slowapi's middleware introspects ``handler.__name__``,
which the MCP ASGI sub-app — a class instance — does not have, so it cannot be
used here.)
"""

from __future__ import annotations

import logging
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from .config import Config

logger = logging.getLogger(__name__)

_PERIOD_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def parse_rate(rate: str) -> tuple[int, int]:
    """Parse a ``"<count>/<period>"`` string into (count, window_seconds).

    Accepts singular or plural periods, e.g. ``"120/minute"`` or ``"5/seconds"``.
    """
    count_str, period = rate.split("/")
    period = period.strip().lower().rstrip("s")
    if period not in _PERIOD_SECONDS:
        raise ValueError(f"Unknown rate-limit period: {period!r}")
    return int(count_str), _PERIOD_SECONDS[period]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-IP rate limit. Returns HTTP 429 when exceeded."""

    def __init__(self, app, limit: int, window_seconds: int):
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[int, int]] = {}  # ip -> (window_index, count)

    async def dispatch(self, request, call_next):
        ip = request.client.host if request.client else "unknown"
        window_index = int(time.monotonic() // self.window)
        with self._lock:
            w, count = self._hits.get(ip, (window_index, 0))
            if w != window_index:
                count = 0
            count += 1
            self._hits[ip] = (window_index, count)
        if count > self.limit:
            return JSONResponse(
                status_code=429,
                content={"ok": False, "error": "rate limit exceeded"},
            )
        return await call_next(request)


def build_http_app(mcp, config: Config) -> Starlette:
    """Build the Starlette app exposing the MCP server over streamable HTTP."""
    limit, window = parse_rate(config.rate_limit)

    # FastMCP provides a ready-made streamable-HTTP ASGI sub-app.
    mcp_app = mcp.streamable_http_app()

    middleware = [
        # CORS outermost so even a 429 carries the right CORS headers.
        Middleware(
            CORSMiddleware,
            allow_origins=[config.cors_origin],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            allow_credentials=True,
        ),
        Middleware(RateLimitMiddleware, limit=limit, window_seconds=window),
    ]

    return Starlette(
        routes=mcp_app.routes,
        lifespan=getattr(mcp_app.router, "lifespan_context", None),
        middleware=middleware,
    )


def run_http(mcp, config: Config) -> None:  # pragma: no cover - uvicorn launcher
    app = build_http_app(mcp, config)
    logger.info(
        "Serving MCP over HTTP at http://%s:%s (CORS origin=%s, rate limit=%s)",
        config.http_host,
        config.http_port,
        config.cors_origin,
        config.rate_limit,
    )
    uvicorn.run(
        app,
        host=config.http_host,
        port=config.http_port,
        log_level=config.log_level.lower(),
    )
