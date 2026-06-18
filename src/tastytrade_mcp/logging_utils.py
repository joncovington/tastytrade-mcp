"""Logging setup with PII masking.

Account numbers are masked to the last 4 digits (e.g. ``****1234``) wherever they
appear in log records. Secrets are never logged at all — this module only guards
against accidental account-number leakage in log output.
"""

from __future__ import annotations

import logging
import re

# Tastytrade account numbers look like ``5WT00000`` / ``U1234567`` etc. Match a
# letter-or-digit prefix of length >= 5 so we only mask plausible account ids and
# leave short tokens alone. We also catch the common "account number 12345678"
# phrasing.
_ACCOUNT_RE = re.compile(r"\b([A-Z0-9]{4,})(\d{4})\b")


def mask_account_number(value: str) -> str:
    """Mask a single account number to its last 4 digits."""
    if value is None:
        return value
    if len(value) <= 4:
        return value
    return "****" + value[-4:]


def _mask_text(text: str) -> str:
    """Mask anything in free text that looks like an account number."""
    return _ACCOUNT_RE.sub(lambda m: "****" + m.group(2), text)


class AccountMaskingFilter(logging.Filter):
    """Logging filter that masks account-number-like tokens in messages."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        masked = _mask_text(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with the account-masking filter installed."""
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.addFilter(AccountMaskingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
