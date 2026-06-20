import os

import tastytrade_mcp.server as server
from tastytrade_mcp.cli import main


def test_serve_flags_set_env_and_serve(monkeypatch):
    # Isolate env writes: give the process a private copy of os.environ.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)
    monkeypatch.delenv("TASTYTRADE_SANDBOX", raising=False)

    captured = {}
    monkeypatch.setattr(
        server, "run", lambda transport="stdio": captured.update(transport=transport)
    )

    rc = main(
        ["--sandbox", "--enable-live-trading", "--transport", "http"]
    )

    assert rc == 0
    assert captured["transport"] == "http"
    assert os.environ["TASTYTRADE_SANDBOX"] == "true"
    assert os.environ["ENABLE_LIVE_TRADING"] == "true"


def test_no_flags_leaves_env_untouched(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)
    monkeypatch.delenv("TASTYTRADE_SANDBOX", raising=False)
    monkeypatch.setattr(server, "run", lambda transport="stdio": None)

    main([])
    assert "ENABLE_LIVE_TRADING" not in os.environ
    assert "TASTYTRADE_SANDBOX" not in os.environ
