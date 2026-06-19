import os

import tastytrade_mcp.server as server
from tastytrade_mcp.cli import main


def test_mock_flags_set_env_and_serve(monkeypatch):
    # Isolate env writes: give the process a private copy of os.environ.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("TASTYTRADE_MOCK", raising=False)
    monkeypatch.delenv("TASTYTRADE_MOCK_FIXTURE", raising=False)
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)

    captured = {}
    monkeypatch.setattr(
        server, "run", lambda transport="stdio": captured.update(transport=transport)
    )

    rc = main(
        [
            "--mock",
            "--mock-fixture",
            "scenario.json",
            "--enable-live-trading",
            "--transport",
            "http",
        ]
    )

    assert rc == 0
    assert captured["transport"] == "http"
    assert os.environ["TASTYTRADE_MOCK"] == "true"
    assert os.environ["TASTYTRADE_MOCK_FIXTURE"] == "scenario.json"
    assert os.environ["ENABLE_LIVE_TRADING"] == "true"


def test_mock_fixture_implies_mock(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("TASTYTRADE_MOCK", raising=False)
    monkeypatch.setattr(server, "run", lambda transport="stdio": None)

    main(["--mock-fixture", "f.json"])
    assert os.environ["TASTYTRADE_MOCK"] == "true"


def test_no_flags_leaves_env_untouched(monkeypatch):
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.delenv("TASTYTRADE_MOCK", raising=False)
    monkeypatch.setattr(server, "run", lambda transport="stdio": None)

    main([])
    assert "TASTYTRADE_MOCK" not in os.environ
