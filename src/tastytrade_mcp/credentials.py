"""Keyring-backed credential storage.

Secrets are stored in the OS keyring:
  - Windows  → Credential Manager (DPAPI-backed)
  - macOS    → Keychain
  - Linux    → SecretStorage (GNOME Keyring / KWallet)
  - Headless → encrypted file via ``keyrings.alt`` (install with the
               ``[headless]`` extra: ``pip install tastytrade-mcp[headless]``)

Nothing is ever written to disk in plaintext, and secrets are never logged.
"""

from __future__ import annotations

import keyring
import keyring.backend
import keyring.errors

SERVICE_NAME = "tastytrade-mcp"

# Logical secret keys.
CLIENT_SECRET = "client_secret"
REFRESH_TOKEN = "refresh_token"
ACCOUNT_NUMBER = "account_number"

# The optional/required secrets needed to build an OAuth session.
REQUIRED_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN)
ALL_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN, ACCOUNT_NUMBER)

# Keyring username prefix — kept for backward compatibility with existing entries.
_PREFIX = "production"


class CredentialError(RuntimeError):
    """Raised when a keyring operation fails due to missing backend or secret."""


def _entry(key: str) -> str:
    return f"{_PREFIX}:{key}"


def _no_keyring_hint() -> str:
    return (
        "No keyring backend is available on this system.\n"
        "  Linux (headless / server / Docker):\n"
        "    pip install 'tastytrade-mcp[headless]'   # installs keyrings.alt\n"
        "    export PYTHON_KEYRING_BACKEND=keyrings.alt.file.EncryptedKeyring\n"
        "  Linux (desktop): ensure gnome-keyring or kwallet is running.\n"
        "  macOS / Windows: the native keyring should work; "
        "check that Python has keyring >= 24."
    )


def get_backend_name() -> str:
    """Return the name of the active keyring backend (for diagnostics)."""
    try:
        backend = keyring.get_keyring()
        return type(backend).__name__
    except Exception:  # noqa: BLE001
        return "unknown"


def get_secret(key: str) -> str | None:
    """Fetch a secret from the keyring, or ``None`` if not set."""
    try:
        return keyring.get_password(SERVICE_NAME, _entry(key))
    except keyring.errors.NoKeyringError as exc:
        raise CredentialError(_no_keyring_hint()) from exc
    except keyring.errors.KeyringError as exc:
        raise CredentialError(f"Keyring read failed: {exc}") from exc


def set_secret(key: str, value: str) -> None:
    """Store a secret in the keyring."""
    try:
        keyring.set_password(SERVICE_NAME, _entry(key), value)
    except keyring.errors.NoKeyringError as exc:
        raise CredentialError(_no_keyring_hint()) from exc
    except keyring.errors.KeyringError as exc:
        raise CredentialError(f"Keyring write failed: {exc}") from exc


def delete_secret(key: str) -> bool:
    """Delete a secret. Returns True if it existed, False otherwise."""
    try:
        keyring.delete_password(SERVICE_NAME, _entry(key))
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.NoKeyringError as exc:
        raise CredentialError(_no_keyring_hint()) from exc
    except keyring.errors.KeyringError as exc:
        raise CredentialError(f"Keyring delete failed: {exc}") from exc


def secrets_present() -> bool:
    """True when all required secrets for an OAuth session are present."""
    return all(get_secret(k) for k in REQUIRED_SECRETS)


def missing_secrets() -> list[str]:
    """Return the list of required secrets that are not yet stored."""
    return [k for k in REQUIRED_SECRETS if not get_secret(k)]
