from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from prismatic import dispatcher


def test_gateway_subcommand_help_includes_safe_defaults(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prismatic-engine", "gateway", "--help"])

    with pytest.raises(SystemExit) as exc:
        dispatcher.main()

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "prismatic-engine gateway" in help_text
    assert "127.0.0.1" in help_text
    assert "--reload" in help_text


def test_gateway_subcommand_runs_current_gateway_app(monkeypatch, capsys):
    captured = {}

    def fake_run(app, *, host, port, reload):
        captured.update({"app": app, "host": host, "port": port, "reload": reload})

    monkeypatch.setattr(sys, "argv", ["prismatic-engine", "gateway", "--host", "127.0.0.1", "--port", "8123", "--reload"])
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))

    dispatcher.main()

    from prismatic.gateway.server import app as gateway_app

    assert captured == {"app": gateway_app, "host": "127.0.0.1", "port": 8123, "reload": True}
    assert "Starting gateway on 127.0.0.1:8123" in capsys.readouterr().out
