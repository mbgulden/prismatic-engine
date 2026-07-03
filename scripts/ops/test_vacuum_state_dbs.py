import importlib.util
import sqlite3
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().with_name("vacuum_state_dbs.py")
spec = importlib.util.spec_from_file_location("vacuum_state_dbs", MODULE_PATH)
assert spec is not None
vacuum_state_dbs = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = vacuum_state_dbs
spec.loader.exec_module(vacuum_state_dbs)


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE example(id INTEGER PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT INTO example(value) VALUES (?)", [("x",), ("y",)])
        conn.commit()
    finally:
        conn.close()


def test_parse_roots_uses_both_canonical_roots_when_unset(monkeypatch):
    monkeypatch.delenv("PRISMATIC_VACUUM_ROOTS", raising=False)

    roots = vacuum_state_dbs.parse_roots()

    assert Path.home() / ".prismatic" / "db" in roots
    assert MODULE_PATH.parents[2] / "prismatic_state" in roots


def test_parse_roots_accepts_comma_separated_env(monkeypatch, tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    monkeypatch.setenv("PRISMATIC_VACUUM_ROOTS", f"{first},{second}")

    assert vacuum_state_dbs.parse_roots() == [first, second]


def test_discover_databases_returns_sorted_unique_paths(tmp_path):
    root = tmp_path / "state"
    nested = root / "nested"
    nested.mkdir(parents=True)
    db_a = root / "a.db"
    db_b = nested / "b.db"
    txt = nested / "ignore.txt"
    for path in (db_a, db_b, txt):
        path.write_text("placeholder")

    found = vacuum_state_dbs.discover_databases([root, db_a])

    assert found == sorted({db_a.resolve(), db_b.resolve()})


def test_maintain_database_vacuums_and_analyzes_sqlite_db(tmp_path):
    db = tmp_path / "state.db"
    make_db(db)

    result = vacuum_state_dbs.maintain_database(db)

    assert result.ok is True
    assert result.before_bytes is not None
    assert result.after_bytes is not None
    assert "ok" in result.message
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT count(*) FROM example").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0] >= 1
    finally:
        conn.close()


def test_maintain_database_dry_run_does_not_create_statistics(tmp_path):
    db = tmp_path / "state.db"
    make_db(db)

    result = vacuum_state_dbs.maintain_database(db, dry_run=True)

    assert result.ok is True
    assert "dry-run" in result.message
    conn = sqlite3.connect(db)
    try:
        stat_exists = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'"
        ).fetchone()[0]
        assert stat_exists == 0
    finally:
        conn.close()


def test_run_continues_after_bad_database(tmp_path):
    good = tmp_path / "good.db"
    bad = tmp_path / "bad.db"
    make_db(good)
    bad.write_text("not sqlite")

    results = vacuum_state_dbs.run([tmp_path])

    by_name = {result.path.name: result for result in results}
    assert by_name["good.db"].ok is True
    assert by_name["bad.db"].ok is False
