"""Shared helpers for tool modules: serialization and account resolution."""

from __future__ import annotations

from typing import Any

from tastytrade.account import Account

from .. import credentials
from ..config import Config
from ..session import get_session


def serialize(obj: Any) -> Any:
    """Convert Pydantic models / lists into JSON-friendly primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    # Pydantic v2 model
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return str(obj)


async def get_account(config: Config, account_number: str | None = None) -> Account:
    """Resolve a single Account.

    Uses the supplied account number, else the stored default, else the first
    account on the session.
    """
    session = get_session(config)
    number = account_number or credentials.get_secret(
        credentials.ACCOUNT_NUMBER, sandbox=config.sandbox
    )
    if number:
        return await Account.get(session, number)
    accounts = await Account.get(session)
    if not accounts:
        raise RuntimeError("No accounts found for these credentials.")
    return accounts[0]


def error_payload(exc: Exception) -> dict[str, Any]:
    """Standard error shape returned to the agent (never includes secrets)."""
    msg = f"{type(exc).__name__}: {exc}"
    # Surface retryability for transient upstream HTTP errors (5xx).
    retryable = any(f" {code} " in str(exc) or str(exc).endswith(str(code))
                    for code in (500, 502, 503, 504))
    result: dict[str, Any] = {"ok": False, "error": msg}
    if retryable:
        result["retryable"] = True
    return result
