from types import SimpleNamespace

from tastytrade_mcp import server, session
from tastytrade_mcp.config import Config


def _config():
    return Config(
        sandbox=True,
        enable_live_trading=False,
        force_dry_run=False,
        buying_power_buffer_pct=0.0,
        account_deploy_limit_pct=0.0,
        log_level="INFO",
        cors_origin="http://localhost:3333",
        rate_limit="120/minute",
        http_host="127.0.0.1",
        http_port=7698,
    )


def test_run_handles_ctrl_c_gracefully(monkeypatch):
    """A KeyboardInterrupt from the transport should not propagate, and the
    session must be closed on the way out."""
    closed = {"called": False}

    class FakeMCP:
        def run(self):
            raise KeyboardInterrupt()

    monkeypatch.setattr(server, "build_server", lambda config: FakeMCP())
    monkeypatch.setattr(
        server, "close_session", lambda: closed.__setitem__("called", True)
    )

    # Should return cleanly, not raise.
    server.run(transport="stdio", config=_config())
    assert closed["called"]


def test_close_session_closes_http_client():
    session.reset_session()
    closed = {"called": False}
    fake = SimpleNamespace(
        sync_client=SimpleNamespace(
            close=lambda: closed.__setitem__("called", True)
        )
    )
    # Inject a cached session directly.
    session._session = fake
    session._session_sandbox = True

    session.close_session()
    assert closed["called"]
    assert session._session is None
