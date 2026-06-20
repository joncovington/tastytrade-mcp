import pytest

from tastytrade_mcp import credentials, session


@pytest.fixture(autouse=True)
def _reset():
    session.reset_session()
    yield
    session.reset_session()


def test_missing_credentials_raises(make_config):
    with pytest.raises(session.CredentialsMissingError):
        session.get_session(make_config())


def test_builds_and_caches_session(monkeypatch, make_config):
    credentials.set_secret(credentials.CLIENT_SECRET, "cs", sandbox=True)
    credentials.set_secret(credentials.REFRESH_TOKEN, "rt", sandbox=True)

    calls = []

    class FakeSession:
        def __init__(self, client_secret, refresh_token, is_test=False):
            calls.append((client_secret, refresh_token, is_test))

    monkeypatch.setattr(session, "Session", FakeSession)

    s1 = session.get_session(make_config())
    s2 = session.get_session(make_config())
    assert s1 is s2  # cached
    assert calls == [("cs", "rt", True)]
