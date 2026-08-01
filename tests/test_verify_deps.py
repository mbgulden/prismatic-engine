import subprocess


def run_script(phase_id, state_dir):
    result = subprocess.run(
        ["python3", "scripts/verify-phase-dependencies.py", phase_id, "--state-dir", str(state_dir)],
        capture_output=True,
        text=True
    )
    return result

def test_verify_deps_pass():
    res = run_script("phase_2", "tests/fixtures")
    assert res.returncode == 0
    assert "Success" in res.stdout

def test_verify_deps_fail():
    res = run_script("phase_3", "tests/fixtures")
    assert res.returncode == 1
    assert "Violation" in res.stdout

def test_verify_deps_missing_manifest():
    res = run_script("phase_1", "/tmp/nonexistent")
    assert res.returncode == 0
    assert "missing. Skipping" in res.stdout

def test_verify_deps_no_deps_defined():
    res = run_script("unknown_phase", "tests/fixtures")
    assert res.returncode == 0
    assert "no defined dependencies" in res.stdout
