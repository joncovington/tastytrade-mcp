"""HTTP transport for the MCP server, hardened with CORS and rate limiting.

Wraps FastMCP's streamable-HTTP ASGI app behind Starlette middleware:

- CORS is restricted to the single ``MCP_CORS_ORIGIN`` (default
  ``http://localhost:3333``).
- A per-IP rate limit (default 120 requests/minute) is applied to all MCP
  endpoints, returning HTTP 429 when exceeded.
"""

from __future__ import annotations

import logging

import uvicorn
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import Config

logger = logging.getLogger(__name__)


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"ok": False, "error": "rate limit exceeded", "detail": str(exc.detail)},
    )


def build_http_app(mcp, config: Config) -> Starlette:
    """Build the Starlette app exposing the MCP server over streamable HTTP."""
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[config.rate_limit],
    )

    # FastMCP provides a ready-made streamable-HTTP ASGI sub-app.
    mcp_app = mcp.streamable_http_app()

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=[config.cors_origin],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            allow_credentials=True,
        ),
        Middleware(SlowAPIMiddleware),
    ]

    app = Starlette(
        routes=mcp_app.routes,
        lifespan=getattr(mcp_app.router, "lifespan_context", None),
        middleware=middleware,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    return app


def run_http(mcp, config: Config) -> None:
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
