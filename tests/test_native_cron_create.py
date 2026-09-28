"""tests/test_native_cron_create.py — WI-3: create path + register_native_cron.

Covers the CLI `add` subcommand, the shared create path
(`create_native_cron`), duplicate-id and bad-schedule rejection, the
`register_native_cron` import that `prismatic/gateway/routes/pwp.py` needs,
the new `POST /native-crons` route registration, and the PWP cron status
endpoint no longer falling back.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest

from prismatic import native_crons
from prismatic.native_crons import (
    DuplicateCronIdError,
    NativeCron,
    NativeCronStore,
    create_native_cron,
    register_native_cron,
    validate_cron_schedule,
)


@pytest.fixture()
def no_crontab(monkeypatch: pytest.MonkeyPatch) -> None:
    """Creating a cron re-exports best-effort; tests must not touch a real crontab."""
    monkeypatch.setattr(native_crons, "read_user_crontab", lambda: None)
    monkeypatch.setattr(native_crons, "write_user_crontab", lambda content: None)


def _store(tmp_path: Path) -> NativeCronStore:
    return NativeCronStore(path=tmp_path / "native_crons.json")


# ── register_native_cron (pwp.py:455 ImportError gate) ─────────────────


def test_register_native_cron_import_succeeds() -> None:
    """Fail-first: `register_native_cron` did not exist before WI-3."""
    from prismatic.native_crons import register_native_cron as imported

    assert callable(imported)


def test_register_native_cron_accepts_native_cron_object(
    tmp_path: Path, no_crontab: None
) -> None:
    """pwp.py passes a NativeCron object and reads the returned dict."""
    store = _store(tmp_path)
    result = register_native_cron(
        NativeCron(
            id="cron-pwp-auto-sync",
            name="PWP Multi-Site Vitals",
            schedule="*/5 * * * *",
            command=["python3", "-m", "some.module"],
            group="pwp",
            tags=["pwp"],
        ),
        store=store,
    )
    assert result["id"] == "cron-pwp-auto-sync"
    assert result["enabled"] is True
    assert store.get("cron-pwp-auto-sync").schedule == "*/5 * * * *"


def test_register_native_cron_rejects_duplicate(tmp_path: Path, no_crontab: None) -> None:
    store = _store(tmp_path)
    cron = NativeCron(id="dup.cron", name="Dup", schedule="* * * * *", command=["true"])
    register_native_cron(cron, store=store)
    with pytest.raises(DuplicateCronIdError):
        register_native_cron(cron, store=store)


# ── schedule validation ───────────────────────────────────────────────


@pytest.mark.parametrize("schedule", ["*/5 * * * *", "0 3 * * 1", "manual"])
def test_validate_cron_schedule_accepts(schedule: str) -> None:
    validate_cron_schedule(schedule)


@pytest.mark.parametrize(
    "schedule", ["", "every 5 minutes", "*/5 * * *", "* * * * * *", "  ", "@daily"]
)
def test_validate_cron_schedule_rejects(schedule: str) -> None:
    with pytest.raises(ValueError):
        validate_cron_schedule(schedule)


# ── create path ───────────────────────────────────────────────────────


def test_create_native_cron_round_trip(tmp_path: Path, no_crontab: None) -> None:
    store = _store(tmp_path)
    created = create_native_cron(
        {
            "id": "test.created",
            "name": "Created cron",
            "schedule": "15 2 * * *",
            "command": ["echo", "created"],
            "group": "test",
            "tags": ["a", "b"],
            "env": {"FOO": "bar"},
            "description": "made by test",
        },
        store=store,
    )
    assert created["id"] == "test.created"
    assert created["tags"] == ["a", "b"]
    assert created["env"] == {"FOO": "bar"}
    assert created["state"] == "active"
    fetched = store.get("test.created")
    assert fetched.command == ["echo", "created"]


def test_create_rejects_duplicate_id(tmp_path: Path, no_crontab: None) -> None:
    store = _store(tmp_path)
    payload = {"id": "test.dupe", "name": "D", "schedule": "* * * * *", "command": ["true"]}
    create_native_cron(payload, store=store)
    with pytest.raises(DuplicateCronIdError):
        create_native_cron(payload, store=store)


def test_create_rejects_bad_schedule(tmp_path: Path, no_crontab: None) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        create_native_cron(
            {"id": "test.bad-sched", "name": "B", "schedule": "soon", "command": ["true"]},
            store=store,
        )


def test_create_rejects_bad_id_and_missing_fields(tmp_path: Path, no_crontab: None) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        create_native_cron(
            {"id": "not an id!!", "name": "B", "schedule": "* * * * *", "command": ["true"]},
            store=store,
        )
    with pytest.raises(ValueError):
        create_native_cron({"id": "test.noname", "schedule": "* * * * *", "command": ["true"]},
                           store=store)
    with pytest.raises(ValueError):
        create_native_cron({"id": "test.nocmd", "name": "B", "schedule": "* * * * *",
                            "command": []}, store=store)


# ── CLI: add ──────────────────────────────────────────────────────────


def _add_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store_path = tmp_path / "native_crons.json"
    monkeypatch.setenv("PRISMATIC_NATIVE_CRON_STORE", str(store_path))
    return store_path


def test_cli_add_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                            no_crontab: None) -> None:
    store_path = _add_env(tmp_path, monkeypatch)
    rc = native_crons.main([
        "add", "--id", "test.cli-add", "--name", "CLI add", "--schedule", "*/10 * * * *",
        "--command", "echo", "from-cli", "--group", "test",
        "--tags", "a,b", "--description", "cli made", "--env", "FOO=1", "--env", "BAR=2",
    ])
    assert rc == 0
    cron = NativeCronStore(path=store_path).get("test.cli-add")
    assert cron.command == ["echo", "from-cli"]
    assert cron.env == {"FOO": "1", "BAR": "2"}
    assert cron.tags == ["a", "b"]
    assert cron.description == "cli made"


def test_cli_add_duplicate_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                    no_crontab: None, capsys) -> None:
    _add_env(tmp_path, monkeypatch)
    args = ["add", "--id", "test.cli-dupe", "--name", "D", "--schedule", "* * * * *",
            "--command", "true"]
    assert native_crons.main(args) == 0
    assert native_crons.main(args) == 1
    assert "already exists" in capsys.readouterr().err


def test_cli_add_bad_schedule_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                       no_crontab: None, capsys) -> None:
    _add_env(tmp_path, monkeypatch)
    rc = native_crons.main(["add", "--id", "test.cli-bad", "--name", "B",
                            "--schedule", "whenever", "--command", "true"])
    assert rc == 1
    assert "Invalid cron schedule" in capsys.readouterr().err


def test_cli_add_bad_env_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                  no_crontab: None, capsys) -> None:
    _add_env(tmp_path, monkeypatch)
    rc = native_crons.main(["add", "--id", "test.cli-env", "--name", "E",
                            "--schedule", "* * * * *", "--command", "true",
                            "--env", "NOEQUALS"])
    assert rc == 2
    assert "KEY=VAL" in capsys.readouterr().err


# ── POST /native-crons route registration ────────────────────────────


def _server_source() -> str:
    server_py = Path(native_crons.__file__).resolve().parents[1] / "prismatic" / "gateway" / "server.py"
    assert server_py.exists(), f"server.py not found at {server_py}"
    return server_py.read_text(encoding="utf-8")


def test_post_native_crons_route_registered() -> None:
    """server.py must expose POST /native-crons wired to the create path."""
    tree = ast.parse(_server_source())
    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not (isinstance(func, ast.Attribute) and func.attr == "post"):
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant) and dec.args[0].value == "/native-crons":
                matches.append(node.name)
    assert matches, "POST /native-crons route not found in server.py"
    assert "create_native_cron" in _server_source()


# ── PWP status endpoint ───────────────────────────────────────────────


def test_pwp_cron_status_registers_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail-first: before WI-3 the import raised and the endpoint fell back."""
    pytest.importorskip("fastapi")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    store_path = tmp_path / "state" / "native_crons.json"
    monkeypatch.setenv("PRISMATIC_NATIVE_CRON_STORE", str(store_path))
    monkeypatch.setattr(native_crons, "read_user_crontab", lambda: None)
    monkeypatch.setattr(native_crons, "write_user_crontab", lambda content: None)

    from prismatic.gateway.routes.pwp import get_cron_status

    # With the stock SEO seeds present, the endpoint resolves the pwp-tagged
    # ubersuggest cron — the point here is the import no longer explodes.
    with caplog.at_level(logging.WARNING, logger="prismatic.gateway.routes.pwp"):
        data = get_cron_status()
    assert data["ok"] is True
    assert data["native_cron_id"] == "cron-pwp-auto-sync"
    assert "Native cron lookup fallback" not in caplog.text

    # With no pwp-tagged cron present, the endpoint takes the register path.
    monkeypatch.setattr(native_crons, "SEO_NATIVE_CRONS", [])
    NativeCronStore(path=store_path).save([])
    data = get_cron_status()
    assert data["ok"] is True
    assert "Native cron lookup fallback" not in caplog.text

    cron = NativeCronStore(path=store_path).get("cron-pwp-auto-sync")
    assert cron.schedule == "*/5 * * * *"
    assert cron.state == "active"

    # Second call finds the existing cron — registration is not duplicated.
    data2 = get_cron_status()
    assert data2["native_cron_state"] == "active"
    pwp_crons = [c for c in NativeCronStore(path=store_path).load()
                 if c.id == "cron-pwp-auto-sync"]
    assert len(pwp_crons) == 1
