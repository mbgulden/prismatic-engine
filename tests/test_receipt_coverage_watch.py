"""Tests for scripts/receipt_coverage_watch.py (WI-7 — merge-receipt coverage watcher).

Fail-first contract: a merged PR with no matching merge receipt in the
merge-receipts JSONL log MUST be flagged (exit 1); a fully receipted set
MUST be clean (exit 0). Fixtures reproduce the known #562-#570
zero-receipt state (GitHub-UI merges emit no receipts and nothing noticed).
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.receipt_coverage_watch import (
    WatcherError,
    check_coverage,
    fetch_merged_prs,
    format_json_report,
    load_receipt_index,
    main,
    resolve_receipt_log_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "receipt_coverage_watch.py"

MERGE_RECEIPT_MARKER = "PRISMATIC_MERGE_RECEIPT_OK"


# ── Fixtures ──────────────────────────────────────────────────────────


def _pr(number, merge_sha, head_sha=None, title=None, merged_at="2026-09-27T20:00:00Z"):
    return {
        "number": number,
        "title": title or f"WI fixture PR #{number}",
        "mergedAt": merged_at,
        "mergeCommit": {"oid": merge_sha},
        "headRefOid": head_sha or ("h" * 40),
        "headRefName": f"wi-7-fixture-{number}",
        "url": f"https://github.com/mbgulden/prismatic-engine/pull/{number}",
    }


def _receipt(merge_sha, candidate_sha=None, signed=True, receipt_id=None):
    return {
        "marker": MERGE_RECEIPT_MARKER,
        "schema_version": "merge-receipt/v1",
        "receipt_id": receipt_id or f"rcpt-{merge_sha[:8]}",
        "emitted_at": "2026-09-27T21:00:00Z",
        "repository": "mbgulden/prismatic-engine",
        "candidate_sha": candidate_sha or ("c" * 40),
        "merge_sha": merge_sha,
        "verifier_id": "rf-merge-executor",
        "explicit_non_claims": [],
        "signature_or_attestation": (
            {"type": "attestation", "algorithm": "ed25519", "key_id": "k1",
             "issuer": "rf-merge-executor", "value": "sig=="}
            if signed
            else None
        ),
    }


def _nine_prs():
    """The known zero-receipt state: PRs #562-#570, merged via GitHub UI."""
    prs = []
    for n in range(562, 571):
        prs.append(
            _pr(
                n,
                merge_sha="%040x" % n,
                head_sha="%040x" % (n * 7),
            )
        )
    return prs


def _write_receipts(path, receipts):
    path.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in receipts),
        encoding="utf-8",
    )


# ── Pure-function tests ───────────────────────────────────────────────


def test_zero_receipt_state_flags_all_nine(tmp_path):
    """#562-#570 with an empty receipts log -> all nine flagged missing."""
    prs = _nine_prs()
    log = tmp_path / "merge-receipts.jsonl"
    log.write_text("", encoding="utf-8")

    by_merge, by_candidate, stats = load_receipt_index(log)
    report = check_coverage(prs, by_merge, by_candidate)

    assert report["total"] == 9
    assert report["covered"] == 0
    assert sorted(p["number"] for p in report["missing"]) == list(range(562, 571))
    assert report["missing_pr_numbers"] == list(range(562, 571))


def test_fully_receipted_state_is_clean(tmp_path):
    """#562-#570 each with a matching merge_sha receipt -> clean."""
    prs = _nine_prs()
    log = tmp_path / "merge-receipts.jsonl"
    _write_receipts(log, [_receipt(p["mergeCommit"]["oid"]) for p in prs])

    by_merge, by_candidate, stats = load_receipt_index(log)
    report = check_coverage(prs, by_merge, by_candidate)

    assert report["total"] == 9
    assert report["covered"] == 9
    assert report["missing"] == []
    assert stats["receipts"] == 9


def test_candidate_sha_fallback_covers(tmp_path):
    """Receipt matching the PR head OID via candidate_sha counts as covered."""
    pr = _pr(999, merge_sha="a" * 40, head_sha="b" * 40)
    log = tmp_path / "merge-receipts.jsonl"
    _write_receipts(log, [_receipt("c" * 40, candidate_sha="b" * 40)])

    by_merge, by_candidate, _ = load_receipt_index(log)
    report = check_coverage([pr], by_merge, by_candidate)

    assert report["covered"] == 1
    assert report["prs"][0]["status"] == "covered"
    assert report["prs"][0]["matched_via"] == "candidate_sha"


def test_unsigned_receipt_still_counts_as_covered(tmp_path):
    """Coverage is about presence of the receipt, not signature validity;
    unsigned receipts are reported as unsigned but still cover the merge."""
    pr = _pr(999, merge_sha="a" * 40)
    log = tmp_path / "merge-receipts.jsonl"
    _write_receipts(log, [_receipt("a" * 40, signed=False)])

    by_merge, by_candidate, _ = load_receipt_index(log)
    report = check_coverage([pr], by_merge, by_candidate)

    assert report["covered"] == 1
    assert report["prs"][0]["receipt"]["signed"] is False


def test_malformed_and_foreign_lines_are_ignored(tmp_path):
    """Garbage lines and non-merge-receipt rows don't crash the loader."""
    pr = _pr(999, merge_sha="a" * 40)
    log = tmp_path / "merge-receipts.jsonl"
    log.write_text(
        "not json at all\n"
        '{"marker": "SOMETHING_ELSE", "merge_sha": "' + "a" * 40 + '"}\n'
        + json.dumps(_receipt("a" * 40)) + "\n",
        encoding="utf-8",
    )

    by_merge, by_candidate, stats = load_receipt_index(log)
    assert stats["malformed_lines"] == 1
    assert stats["skipped_non_receipt_lines"] == 1
    report = check_coverage([pr], by_merge, by_candidate)
    assert report["covered"] == 1


def test_missing_log_file_means_zero_coverage(tmp_path):
    """A log path that doesn't exist -> every merge flagged, path noted."""
    prs = _nine_prs()
    log = tmp_path / "does-not-exist.jsonl"

    by_merge, by_candidate, stats = load_receipt_index(log)
    assert stats["log_exists"] is False
    report = check_coverage(prs, by_merge, by_candidate)
    assert report["covered"] == 0
    assert len(report["missing"]) == 9


def test_pr_without_merge_commit_oid_is_unverifiable(tmp_path):
    """A merged PR with no mergeCommit OID can't be proven covered."""
    pr = _pr(999, merge_sha="")
    pr["mergeCommit"] = None
    pr["headRefOid"] = ""
    log = tmp_path / "merge-receipts.jsonl"
    log.write_text("", encoding="utf-8")

    by_merge, by_candidate, _ = load_receipt_index(log)
    report = check_coverage([pr], by_merge, by_candidate)
    assert report["prs"][0]["status"] == "missing"
    assert "no merge-commit sha" in report["prs"][0]["note"]


# ── main() exit-code tests ────────────────────────────────────────────


def _run_main(monkeypatch, tmp_path, prs, receipts, argv_extra=()):
    log = tmp_path / "merge-receipts.jsonl"
    _write_receipts(log, receipts)
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPTS", str(log))
    import scripts.receipt_coverage_watch as mod

    monkeypatch.setattr(mod, "fetch_merged_prs", lambda **kw: prs)
    return main(["--repo", "mbgulden/prismatic-engine", *argv_extra])


def test_main_exits_1_on_zero_receipt_state(monkeypatch, tmp_path, capsys):
    prs = _nine_prs()
    rc = _run_main(monkeypatch, tmp_path, prs, [])
    out = capsys.readouterr().out
    assert rc == 1
    assert "0/9" in out
    for n in range(562, 571):
        assert f"#{n}" in out


def test_main_exits_0_when_fully_covered(monkeypatch, tmp_path, capsys):
    prs = _nine_prs()
    receipts = [_receipt(p["mergeCommit"]["oid"]) for p in prs]
    rc = _run_main(monkeypatch, tmp_path, prs, receipts)
    out = capsys.readouterr().out
    assert rc == 0
    assert "9/9" in out


def test_main_exits_2_when_gh_fails(monkeypatch, tmp_path, capsys):
    """A broken gh is a tool error (exit 2), not a coverage gap (exit 1)."""
    import scripts.receipt_coverage_watch as mod

    def boom(**kw):
        raise WatcherError("gh not authenticated")

    monkeypatch.setattr(mod, "fetch_merged_prs", boom)
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPTS", str(tmp_path / "x.jsonl"))
    rc = main(["--repo", "mbgulden/prismatic-engine"])
    assert rc == 2
    assert "gh" in capsys.readouterr().err.lower()


def test_main_json_output_shape(monkeypatch, tmp_path, capsys):
    prs = _nine_prs()
    receipts = [_receipt(prs[0]["mergeCommit"]["oid"])]
    rc = _run_main(monkeypatch, tmp_path, prs, receipts, argv_extra=("--json",))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["total"] == 9
    assert payload["covered"] == 1
    assert payload["missing_pr_numbers"] == list(range(563, 571))
    assert payload["coverage_pct"] == pytest.approx(100.0 / 9)


# ── env/path resolution ───────────────────────────────────────────────


def test_resolve_receipt_log_path_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPTS", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    assert resolve_receipt_log_path(None) == tmp_path / "a.jsonl"


def test_resolve_receipt_log_path_falls_back_to_state_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("PRISMATIC_MERGE_RECEIPTS", raising=False)
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    assert resolve_receipt_log_path(None) == tmp_path / "state" / "merge-receipts.jsonl"


def test_resolve_receipt_log_path_explicit_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPTS", str(tmp_path / "a.jsonl"))
    assert resolve_receipt_log_path(str(tmp_path / "b.jsonl")) == tmp_path / "b.jsonl"


# ── end-to-end: real subprocess with a fake `gh` on PATH ──────────────


def test_fetch_merged_prs_parses_gh_json(tmp_path, monkeypatch):
    """fetch_merged_prs shells out to gh; exercise it with a fake gh binary."""
    prs = _nine_prs()
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\ncat <<'EOF'\n" + json.dumps(prs) + "\nEOF\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    got = fetch_merged_prs(gh_bin="gh", repo="mbgulden/prismatic-engine", limit=9)
    assert [p["number"] for p in got] == list(range(562, 571))
    assert got[0]["mergeCommit"]["oid"] == "%040x" % 562


def test_fetch_merged_prs_raises_watcher_error_on_gh_failure(tmp_path, monkeypatch):
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho 'auth failed' >&2\nexit 1\n", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    with pytest.raises(WatcherError):
        fetch_merged_prs(gh_bin="gh", repo="x/y", limit=5)


def test_script_runs_standalone_with_fake_gh(tmp_path, monkeypatch):
    """The script file itself runs as `python3 scripts/...` with no repo deps."""
    prs = _nine_prs()
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\ncat <<'EOF'\n" + json.dumps(prs) + "\nEOF\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "merge-receipts.jsonl"
    log.write_text("", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    env["PRISMATIC_MERGE_RECEIPTS"] = str(log)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", "mbgulden/prismatic-engine",
         "--limit", "9"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 1, proc.stderr
    assert "0/9" in proc.stdout
    assert "#562" in proc.stdout


# ── JSON report helper ────────────────────────────────────────────────


def test_format_json_report_round_trips():
    report = {
        "repo": "mbgulden/prismatic-engine",
        "total": 2, "covered": 1,
        "coverage_pct": 50.0,
        "missing_pr_numbers": [570],
        "missing": [{"number": 570}],
        "prs": [],
        "log_path": "/tmp/x.jsonl",
        "log_exists": True,
    }
    payload = json.loads(format_json_report(report))
    assert payload["coverage_pct"] == 50.0
    assert payload["missing_pr_numbers"] == [570]
