"""Keyring-backed credential storage — mirrors the meic-trader pattern.

Secrets are stored in the OS keyring (Windows Credential Manager / DPAPI on
Windows, Keychain on macOS, Secret Service on Linux). Nothing is ever written to
disk in plaintext, and secrets are never logged.

Secrets are namespaced per environment (production vs sandbox) so a single
machine can hold credentials for both.
"""

from __future__ import annotations

import keyring

SERVICE_NAME = "tastytrade-mcp"

# Logical secret keys.
CLIENT_SECRET = "client_secret"
REFRESH_TOKEN = "refresh_token"
ACCOUNT_NUMBER = "account_number"

# The optional/required secrets needed to build an OAuth session.
REQUIRED_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN)
ALL_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN, ACCOUNT_NUMBER)


def _entry(key: str, *, sandbox: bool) -> str:
    """Build the namespaced keyring username for a secret."""
    env = "sandbox" if sandbox else "production"
    return f"{env}:{key}"


def get_secret(key: str, *, sandbox: bool) -> str | None:
    """Fetch a secret from the keyring, or ``None`` if not set."""
    return keyring.get_password(SERVICE_NAME, _entry(key, sandbox=sandbox))


def set_secret(key: str, value: str, *, sandbox: bool) -> None:
    """Store a secret in the keyring."""
    keyring.set_password(SERVICE_NAME, _entry(key, sandbox=sandbox), value)


def delete_secret(key: str, *, sandbox: bool) -> bool:
    """Delete a secret. Returns True if it existed, False otherwise."""
    try:
        keyring.delete_password(SERVICE_NAME, _entry(key, sandbox=sandbox))
        return True
    except keyring.errors.PasswordDeleteError:
        return False


def secrets_present(*, sandbox: bool) -> bool:
    """True when all required secrets for an OAuth session are present."""
    return all(get_secret(k, sandbox=sandbox) for k in REQUIRED_SECRETS)


def missing_secrets(*, sandbox: bool) -> list[str]:
    """Return the list of required secrets that are not yet stored."""
    return [k for k in REQUIRED_SECRETS if not get_secret(k, sandbox=sandbox)]
