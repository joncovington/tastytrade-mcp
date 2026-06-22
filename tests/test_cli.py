import os

import tastytrade_mcp.server as server
from tastytrade_mcp.cli import main


def test_serve_flags_set_env_and_serve(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)

    captured = {}
    monkeypatch.setattr(
        server, "run", lambda transport="stdio": captured.update(transport=transport)
    )

    rc = main(["--enable-live-trading", "--transport", "http"])

    assert rc == 0
    assert captured["transport"] == "http"
    assert os.environ["ENABLE_LIVE_TRADING"] == "true"


def test_no_flags_leaves_env_untouched(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)
    monkeypatch.setattr(server, "run", lambda transport="stdio": None)

    main([])
    assert "ENABLE_LIVE_TRADING" not in os.environ
