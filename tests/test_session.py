import pytest

from tastytrade_mcp import credentials, session
from tastytrade_mcp.config import Config


def _config(sandbox=True, live=False):
    return Config(
        sandbox=sandbox,
        enable_live_trading=live,
        force_dry_run=False,
        buying_power_buffer_pct=0.0,
        account_deploy_limit_pct=0.0,
        log_level="INFO",
        cors_origin="http://localhost:3333",
        rate_limit="120/minute",
        http_host="127.0.0.1",
        http_port=7698,
    )


@pytest.fixture(autouse=True)
def _reset():
    session.reset_session()
    yield
    session.reset_session()


def test_missing_credentials_raises():
    with pytest.raises(session.CredentialsMissingError):
        session.get_session(_config())


def test_builds_and_caches_session(monkeypatch):
    credentials.set_secret(credentials.CLIENT_SECRET, "cs", sandbox=True)
    credentials.set_secret(credentials.REFRESH_TOKEN, "rt", sandbox=True)

    calls = []

    class FakeSession:
        def __init__(self, client_secret, refresh_token, is_test=False):
            calls.append((client_secret, refresh_token, is_test))

    monkeypatch.setattr(session, "Session", FakeSession)

    s1 = session.get_session(_config())
    s2 = session.get_session(_config())
    assert s1 is s2  # cached
    assert calls == [("cs", "rt", True)]
