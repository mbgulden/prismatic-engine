"""Tests for --emit-missing (WR-3 receipt backfill) in scripts/receipt_coverage_watch.py.

Fail-first contract: a merged PR with no receipt MUST get a signed,
verifiable receipt on --emit-missing; a second run MUST NOT duplicate it;
a merge commit absent from local git MUST be skipped without raising.

Signing uses a throwaway Ed25519 key per test (never the production key):
PRISMATIC_MERGE_RECEIPT_KEY_FILE points at the temp key and
PRISMATIC_MERGE_RECEIPTS at a temp log. Git is faked by monkeypatching the
watcher's _git helper.
"""

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from prismatic.verification.attestation import canonicalize_receipt
from scripts.receipt_coverage_watch import (
    emit_missing_receipts,
    resolve_backfill_fields,
)

REPO = "mbgulden/prismatic-engine"

# Fake git objects: two merges, one squash (1 parent), one true merge (2 parents).
SQUASH_MERGE = "a" * 40
SQUASH_BASE = "b" * 40
SQUASH_HEAD = "c" * 40
SQUASH_TREE = "d" * 40
TRUE_MERGE = "e" * 40
TRUE_BASE = "f" * 40
TRUE_HEAD = "0" * 39 + "1"
TRUE_TREE = "1" * 40
GHOST_MERGE = "9" * 40


def _squash_pr():
    return {
        "number": 573,
        "title": "fix(dispatcher): port dispatch-cap trio (#573)",
        "mergedAt": "2026-09-28T03:37:39Z",
        "mergeCommit": {"oid": SQUASH_MERGE},
        "headRefOid": SQUASH_HEAD,
        "headRefName": "fix/dispatcher-cap",
        "url": "https://github.com/mbgulden/prismatic-engine/pull/573",
        "mergedBy": {"login": "mbgulden"},
    }


def _true_merge_pr():
    return {
        "number": 424,
        "title": "Merge pull request #424 from mbgulden/fix/jules",
        "mergedAt": "2026-08-01T12:00:00Z",
        "mergeCommit": {"oid": TRUE_MERGE},
        "headRefOid": TRUE_HEAD,
        "headRefName": "fix/jules",
        "url": "https://github.com/mbgulden/prismatic-engine/pull/424",
        "mergedBy": {"login": "mbgulden"},
    }


def _ghost_pr():
    pr = _squash_pr()
    pr["number"] = 999
    pr["mergeCommit"] = {"oid": GHOST_MERGE}
    return pr


def _fake_git_factory():
    """Return a fake _git(git_dir, *args) serving the fixture objects."""
    table = {
        ("cat-file", "-t", SQUASH_MERGE): "commit",
        ("cat-file", "-t", TRUE_MERGE): "commit",
        ("rev-parse", SQUASH_MERGE + "^1"): SQUASH_BASE,
        ("rev-parse", TRUE_MERGE + "^1"): TRUE_BASE,
        ("rev-parse", TRUE_MERGE + "^2"): TRUE_HEAD,
        ("rev-parse", SQUASH_HEAD + "^{tree}"): SQUASH_TREE,
        ("rev-parse", TRUE_HEAD + "^{tree}"): TRUE_TREE,
        ("log", "--format=%P", "-n", "1", SQUASH_MERGE): SQUASH_BASE,
        ("log", "--format=%P", "-n", "1", TRUE_MERGE): TRUE_BASE + " " + TRUE_HEAD,
    }

    def fake_git(git_dir, *args):
        return table.get(tuple(args))

    return fake_git


@pytest.fixture()
def receipt_env(tmp_path, monkeypatch):
    """Isolate receipt log + signing key (throwaway Ed25519)."""
    log = tmp_path / "merge-receipts.jsonl"
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPTS", str(log))
    monkeypatch.delenv("PRISMATIC_MERGE_RECEIPT_SIGNING_KEY", raising=False)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_file = tmp_path / "test-receipt-key.pem"
    key_file.write_bytes(pem)
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPT_KEY_FILE", str(key_file))
    return {"log": log, "public_key": key.public_key()}


@pytest.fixture()
def fake_git(monkeypatch):
    import scripts.receipt_coverage_watch as watch

    monkeypatch.setattr(watch, "_git", _fake_git_factory())


def _log_rows(log: Path):
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_signed(row: dict, public_key) -> None:
    att = row.get("signature_or_attestation") or {}
    assert att.get("algorithm") == "ed25519", "receipt must be Ed25519-signed"
    assert att.get("value"), "signature value must be present"
    check = dict(row)
    check["signature_or_attestation"] = dict(att, value="")
    public_key.verify(base64.b64decode(att["value"]), canonicalize_receipt(check))


def test_resolve_squash_merge_fields(fake_git):
    fields = resolve_backfill_fields(_squash_pr(), git_dir="/fake")
    assert fields["merge_sha"] == SQUASH_MERGE
    assert fields["base_sha"] == SQUASH_BASE
    assert fields["candidate_sha"] == SQUASH_HEAD
    assert fields["candidate_tree"] == SQUASH_TREE
    assert fields["actor"] == "mbgulden"


def test_resolve_true_merge_fields(fake_git):
    fields = resolve_backfill_fields(_true_merge_pr(), git_dir="/fake")
    assert fields["merge_sha"] == TRUE_MERGE
    assert fields["base_sha"] == TRUE_BASE
    assert fields["candidate_sha"] == TRUE_HEAD  # ^2, not headRefOid
    assert fields["candidate_tree"] == TRUE_TREE
    assert fields["actor"] == "mbgulden"


def test_resolve_missing_commit_returns_none(fake_git):
    assert resolve_backfill_fields(_ghost_pr(), git_dir="/fake") is None


def test_emit_missing_writes_signed_receipts(receipt_env, fake_git):
    log = receipt_env["log"]
    summary = emit_missing_receipts(
        [_squash_pr(), _true_merge_pr()],
        repo=REPO, log_path=log, git_dir="/fake",
    )
    assert len(summary["emitted"]) == 2
    assert summary["failed"] == []
    rows = _log_rows(log)
    assert len(rows) == 2
    by_merge = {r["merge_sha"]: r for r in rows}
    squash = by_merge[SQUASH_MERGE]
    assert squash["marker"] == "PRISMATIC_MERGE_RECEIPT_OK"
    assert squash["actor"] == "mbgulden"
    assert squash["base_sha"] == SQUASH_BASE
    assert squash["candidate_sha"] == SQUASH_HEAD
    assert squash["authorization_id"] == "github-ui-manual-merge"
    assert squash["policy_version"] == "wr-3"
    assert squash["change_class"] == "github-ui-backfill"
    assert squash["task_id"] == "PR-573"
    assert squash["verified_receipt_refs"] == []
    non_claims = squash["explicit_non_claims"]
    assert any("merge_executor" in c for c in non_claims)
    assert any("no Prismatic verification receipts" in c for c in non_claims)
    for row in rows:
        _assert_signed(row, receipt_env["public_key"])


def test_emit_missing_is_idempotent(receipt_env, fake_git):
    log = receipt_env["log"]
    prs = [_squash_pr(), _true_merge_pr()]
    first = emit_missing_receipts(prs, repo=REPO, log_path=log, git_dir="/fake")
    assert len(first["emitted"]) == 2
    second = emit_missing_receipts(prs, repo=REPO, log_path=log, git_dir="/fake")
    assert second["emitted"] == []
    assert sorted(second["skipped_duplicate"]) == [424, 573]
    assert len(_log_rows(log)) == 2


def test_emit_missing_skips_ghost_commit(receipt_env, fake_git):
    log = receipt_env["log"]
    summary = emit_missing_receipts([_ghost_pr()], repo=REPO, log_path=log, git_dir="/fake")
    assert summary["emitted"] == []
    assert len(summary["failed"]) == 1
    assert summary["failed"][0]["number"] == 999
    assert _log_rows(log) == []
