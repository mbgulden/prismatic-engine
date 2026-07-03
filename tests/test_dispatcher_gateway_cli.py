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
    assert "PRISMATIC_PORT or 9000" in help_text
    assert "--log-level" in help_text
    assert "--grpc" in help_text
    assert "--grpc-port" in help_text
    assert "--reload" in help_text


def test_gateway_subcommand_runs_current_gateway_app_without_real_server(monkeypatch, capsys):
    captured = {}

    def fake_run(app, *, host, port, log_level, reload):
        captured.update(
            {
                "app": app,
                "host": host,
                "port": port,
                "log_level": log_level,
                "reload": reload,
            }
        )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prismatic-engine",
            "gateway",
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--log-level",
            "debug",
            "--reload",
        ],
    )
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))

    dispatcher.main()

    assert captured == {
        "app": "prismatic.gateway.server:app",
        "host": "127.0.0.1",
        "port": 8123,
        "log_level": "debug",
        "reload": True,
    }
    assert "Starting gateway on 127.0.0.1:8123" in capsys.readouterr().out


def test_gateway_subcommand_honors_prismatic_port(monkeypatch):
    captured = {}

    def fake_run(app, *, host, port, log_level, reload):
        captured.update({"app": app, "host": host, "port": port, "log_level": log_level, "reload": reload})

    monkeypatch.setenv("PRISMATIC_PORT", "9456")
    monkeypatch.setattr(sys, "argv", ["prismatic-engine", "gateway"])
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=fake_run))

    dispatcher.main()

    assert captured == {
        "app": "prismatic.gateway.server:app",
        "host": "127.0.0.1",
        "port": 9456,
        "log_level": "info",
        "reload": False,
    }
