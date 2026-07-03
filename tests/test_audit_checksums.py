import subprocess
import json
import os
from pathlib import Path

def run_script(manifest):
    result = subprocess.run(
        ["python3", "scripts/audit-checksums.py", "--manifest", str(manifest)],
        capture_output=True,
        text=True
    )
    return result

def test_audit_pass(tmp_path):
    f = tmp_path / "good.txt"
    f.write_text("hello")
    # sha256 of "hello" is 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        str(f): "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    }))

    res = run_script(manifest)
    assert res.returncode == 0
    assert "Audit PASSED" in res.stdout

def test_audit_fail(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text("world")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        str(f): "wrong_hash"
    }))

    res = run_script(manifest)
    assert res.returncode == 1
    assert "Audit FAILED" in res.stdout
    assert "Mismatch" in res.stdout

def test_audit_missing_file(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "nonexistent.txt": "somehash"
    }))

    res = run_script(manifest)
    assert res.returncode == 1
    assert "Missing: nonexistent.txt" in res.stdout

def test_audit_missing_manifest():
    res = run_script("/tmp/nonexistent_manifest.json")
    assert res.returncode == 0
    assert "missing. Skipping" in res.stdout
