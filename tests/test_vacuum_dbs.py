import subprocess
import os
import sqlite3
from pathlib import Path
import pytest

def run_vacuum_script(args):
    result = subprocess.run(
        ["python3", "scripts/vacuum_dbs.py"] + args,
        capture_output=True,
        text=True,
        check=False
    )
    return result

def test_vacuum_dbs_success(tmp_path):
    # Create two mock database files
    db1_path = tmp_path / "test1.db"
    db2_path = tmp_path / "test2.sqlite"

    for path in (db1_path, db2_path):
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO test (val) VALUES ('hello')")
        conn.execute("INSERT INTO test (val) VALUES ('world')")
        conn.commit()
        conn.close()

    # Create an invalid database file that has the right extension but wrong magic bytes
    bad_db_path = tmp_path / "bad.db"
    bad_db_path.write_text("Not a sqlite database file contents.")

    # Run the script pointing to our temp state directory
    res = run_vacuum_script(["--state-dir", str(tmp_path)])
    
    assert res.returncode == 0
    assert "Found 2 SQLite database file(s)" in res.stdout
    assert f"Processing database: {db1_path}" in res.stdout
    assert f"Processing database: {db2_path}" in res.stdout
    assert f"Processing database: {bad_db_path}" not in res.stdout
    assert "[SUCCESS] Completed" in res.stdout
    assert "2 succeeded, 0 failed" in res.stdout

    # Verify databases are still functional and valid
    for path in (db1_path, db2_path):
        conn = sqlite3.connect(str(path))
        cursor = conn.execute("SELECT val FROM test ORDER BY id")
        rows = cursor.fetchall()
        assert rows == [("hello",), ("world",)]
        conn.close()

def test_vacuum_dbs_dry_run(tmp_path):
    db_path = tmp_path / "dry_run_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test (val TEXT)")
    conn.commit()
    conn.close()

    res = run_vacuum_script(["--state-dir", str(tmp_path), "--dry-run"])
    assert res.returncode == 0
    assert "Found 1 SQLite database file(s)" in res.stdout
    assert "[DRY RUN] Would check integrity, VACUUM, and ANALYZE" in res.stdout
    assert "1 succeeded, 0 failed" in res.stdout

def test_vacuum_dbs_empty_dir(tmp_path):
    res = run_vacuum_script(["--state-dir", str(tmp_path)])
    assert res.returncode == 0
    assert "No valid SQLite database files found." in res.stdout

def test_vacuum_dbs_missing_dir():
    res = run_vacuum_script(["--state-dir", "/nonexistent/directory/path/here"])
    assert res.returncode == 1
    assert "State directory does not exist" in res.stderr
