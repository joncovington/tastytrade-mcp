"""Shared test fixtures: an in-memory keyring backend."""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        try:
            del self._store[(service, username)]
        except KeyError as exc:
            from keyring.errors import PasswordDeleteError

            raise PasswordDeleteError("not found") from exc


@pytest.fixture(autouse=True)
def memory_keyring(request):
    # Live integration tests must use the real OS keyring (stored credentials),
    # so skip the in-memory swap for anything marked @pytest.mark.live.
    if request.node.get_closest_marker("live"):
        yield None
        return
    backend = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)
