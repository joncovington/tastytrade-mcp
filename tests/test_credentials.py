import keyring.errors
import pytest

from tastytrade_mcp import credentials
from tastytrade_mcp.credentials import CredentialError


def test_set_get_roundtrip():
    credentials.set_secret(credentials.CLIENT_SECRET, "abc")
    assert credentials.get_secret(credentials.CLIENT_SECRET) == "abc"


def test_secrets_present_and_missing():
    assert not credentials.secrets_present()
    assert set(credentials.missing_secrets()) == {
        credentials.CLIENT_SECRET,
        credentials.REFRESH_TOKEN,
    }
    credentials.set_secret(credentials.CLIENT_SECRET, "a")
    credentials.set_secret(credentials.REFRESH_TOKEN, "b")
    assert credentials.secrets_present()


def test_delete_secret():
    credentials.set_secret(credentials.CLIENT_SECRET, "x")
    assert credentials.delete_secret(credentials.CLIENT_SECRET)
    assert not credentials.delete_secret(credentials.CLIENT_SECRET)


def test_get_backend_name_returns_string():
    name = credentials.get_backend_name()
    assert isinstance(name, str) and name


def test_no_keyring_raises_credential_error(monkeypatch):
    def _raise(*a, **kw):
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _raise)
    with pytest.raises(CredentialError, match="No keyring backend"):
        credentials.get_secret(credentials.CLIENT_SECRET)


def test_keyring_error_raises_credential_error(monkeypatch):
    def _raise(*a, **kw):
        raise keyring.errors.KeyringError("backend exploded")

    monkeypatch.setattr(keyring, "set_password", _raise)
    with pytest.raises(CredentialError, match="Keyring write failed"):
        credentials.set_secret(credentials.CLIENT_SECRET, "x")
