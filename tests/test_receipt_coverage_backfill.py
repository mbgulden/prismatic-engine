"""Tests for scripts/receipt_coverage_watch.py --backfill (WR-3).

Fail-first contract: merged PRs with no merge receipt MUST get signed
backfilled receipts — exactly the missing set, each marked as a backfill
attestation (parseable ``backfill`` block + explicit non-claims, covered by
the Ed25519 signature). Re-running MUST be a no-op (idempotent). The
read-only watch/report mode keeps its exit 0/1/2 contract: it flags the
pre-backfill gap and passes post-backfill.

The signing key is a throwaway generated per test (never the live key):
``PRISMATIC_MERGE_RECEIPT_SIGNING_KEY`` is pointed at a generated PEM via
monkeypatch, mirroring tests/test_merge_receipt.py.
"""

import base64
import json
from datetime import datetime
from pathlib import Path

import pytest

import scripts.receipt_coverage_watch as mod
from scripts.receipt_coverage_watch import main

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from prismatic.verification import merge_receipt as mr
from prismatic.verification.attestation import canonicalize_receipt

MERGE_RECEIPT_MARKER = "PRISMATIC_MERGE_RECEIPT_OK"
BACKFILL_SOURCE = "scripts/receipt_coverage_watch.py --backfill"


# ── Fixtures ──────────────────────────────────────────────────────────


def _pr(number, merge_sha, head_sha, merged_by="mbgulden"):
    return {
        "number": number,
        "title": f"WR-3 fixture PR #{number}",
        "mergedAt": "2026-09-28T10:00:00Z",
        "mergeCommit": {"oid": merge_sha},
        "headRefOid": head_sha,
        "headRefName": f"wr3-fixture-{number}",
        "baseRefOid": "b" * 40,
        "author": {"login": "some-author"},
        "mergedBy": {"login": merged_by},
        "url": f"https://github.com/mbgulden/prismatic-engine/pull/{number}",
    }


def _five_ui_prs():
    return [
        _pr(571, "%040x" % 571, "%040x" % (571 * 7)),
        _pr(572, "%040x" % 572, "%040x" % (572 * 7)),
        _pr(573, "%040x" % 573, "%040x" % (573 * 7)),
        _pr(574, "%040x" % 574, "%040x" % (574 * 7)),
        _pr(575, "%040x" % 575, "%040x" % (575 * 7)),
    ]


def _pem(private_key) -> str:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture()
def throwaway_key(monkeypatch, tmp_path):
    """Point merge-receipt signing at a throwaway generated key."""
    key = Ed25519PrivateKey.generate()
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPT_SIGNING_KEY", _pem(key))
    monkeypatch.setenv(
        "PRISMATIC_MERGE_RECEIPT_KEY_FILE", str(tmp_path / "no-such-key.pem")
    )
    return key


@pytest.fixture()
def stub_prs(monkeypatch):
    """Stub out the gh fetch; returns a setter for the PR list."""
    prs = _five_ui_prs()

    def _setter(new_prs):
        prs.clear()
        prs.extend(new_prs)

    monkeypatch.setattr(mod, "fetch_merged_prs", lambda **kw: prs)
    return _setter


def _log_path(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "merge-receipts.jsonl"
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPTS", str(path))
    return path


def _read_rows(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _verify_signature(row: dict, public_key) -> bool:
    att = row.get("signature_or_attestation") or {}
    sig = base64.b64decode(att.get("value", ""))
    public_key.verify(sig, canonicalize_receipt(row))
    return True


# ── Backfill creates exactly the missing signed receipts ─────────────


def test_backfill_creates_signed_receipts_for_exactly_the_missing(
    monkeypatch, tmp_path, throwaway_key, stub_prs
):
    log = _log_path(monkeypatch, tmp_path)
    prs = _five_ui_prs()
    stub_prs(prs)

    rc = main(["--backfill", "--receipts", str(log)])

    assert rc == 0
    rows = _read_rows(log)
    assert len(rows) == 5
    assert {r["merge_sha"] for r in rows} == {p["mergeCommit"]["oid"] for p in prs}
    for row in rows:
        assert row["marker"] == MERGE_RECEIPT_MARKER
        assert row["schema_version"] == "merge-receipt/v1"
        # Signed by the throwaway key.
        assert _verify_signature(row, throwaway_key.public_key())
        # Bound to the PR's head sha; unrecoverable fields are empty.
        pr = next(p for p in prs if p["mergeCommit"]["oid"] == row["merge_sha"])
        assert row["candidate_sha"] == pr["headRefOid"]
        assert row["base_sha"] == pr["baseRefOid"]
        assert row["actor"] == "mbgulden"
        assert row["candidate_tree"] == ""
        assert row["authorization_id"] == ""
        assert row["verified_receipt_refs"] == []
        # Backfill marking present and parseable.
        bf = row["backfill"]
        assert bf["source"] == BACKFILL_SOURCE
        assert bf["pr_number"] == pr["number"]
        datetime.fromisoformat(bf["backfilled_at"])  # raises if not ISO
        non_claims = " ".join(row["explicit_non_claims"])
        assert "backfilled attestation" in non_claims
        assert "not live merge_executor evidence" in non_claims


def test_backfill_skips_prs_with_existing_receipts(
    monkeypatch, tmp_path, throwaway_key, stub_prs
):
    log = _log_path(monkeypatch, tmp_path)
    prs = _five_ui_prs()
    stub_prs(prs)
    # Two PRs already have live executor-style receipts (no backfill block).
    for p in prs[:2]:
        r = mr.build_merge_receipt(
            repository="mbgulden/prismatic-engine",
            candidate_sha=p["headRefOid"],
            candidate_tree="t" * 40,
            base_sha="b" * 40,
            merge_sha=p["mergeCommit"]["oid"],
            actor="rf-merge-executor",
            authorization_id="auth-1",
            job_id="job-1",
        )
        mr.sign_merge_receipt(r)
        assert mr.persist_merge_receipt(r, log_path=log)

    rc = main(["--backfill", "--receipts", str(log)])

    assert rc == 0
    rows = _read_rows(log)
    assert len(rows) == 5
    backfilled = [r for r in rows if "backfill" in r]
    live = [r for r in rows if "backfill" not in r]
    assert len(backfilled) == 3
    assert len(live) == 2
    assert {r["merge_sha"] for r in backfilled} == {
        p["mergeCommit"]["oid"] for p in prs[2:]
    }
    # Live receipts untouched: still no backfill block, original actor kept.
    assert all(r["actor"] == "rf-merge-executor" for r in live)


def test_backfill_is_idempotent(monkeypatch, tmp_path, throwaway_key, stub_prs,
                                capsys):
    log = _log_path(monkeypatch, tmp_path)
    stub_prs(_five_ui_prs())

    assert main(["--backfill", "--receipts", str(log)]) == 0
    first = log.read_text(encoding="utf-8")

    rc = main(["--backfill", "--receipts", str(log)])
    out = capsys.readouterr().out

    assert rc == 0
    assert log.read_text(encoding="utf-8") == first  # byte-identical: no dupes
    assert "Nothing to do" in out
    rows = _read_rows(log)
    assert len(rows) == 5
    assert len({r["merge_sha"] for r in rows}) == 5


def test_backfill_skips_receipt_landed_after_index_load(
    monkeypatch, tmp_path, throwaway_key, stub_prs
):
    """The per-PR fresh find_merge_receipts guard skips late arrivals."""
    log = _log_path(monkeypatch, tmp_path)
    prs = _five_ui_prs()
    stub_prs(prs)
    # The on-disk log already has a receipt for PR #571, but the index the
    # backfill loads is (artificially) empty — the fresh per-PR check must
    # still skip it.
    r = mr.build_merge_receipt(
        repository="mbgulden/prismatic-engine",
        candidate_sha=prs[0]["headRefOid"],
        candidate_tree="",
        base_sha="",
        merge_sha=prs[0]["mergeCommit"]["oid"],
        actor="x",
        authorization_id="",
        job_id="late",
    )
    mr.sign_merge_receipt(r)
    mr.persist_merge_receipt(r, log_path=log)
    monkeypatch.setattr(
        mod, "load_receipt_index", lambda path: ({}, {}, {
            "log_path": str(path), "log_exists": True, "lines": 0,
            "receipts": 0, "malformed_lines": 0,
            "skipped_non_receipt_lines": 0,
        }),
    )

    rc = main(["--backfill", "--receipts", str(log)])

    assert rc == 0
    rows = _read_rows(log)
    assert len(rows) == 5  # 1 pre-existing + 4 backfilled, no duplicate
    assert len({r["merge_sha"] for r in rows}) == 5


# ── Watcher contract around the backfill ──────────────────────────────


def test_watcher_flags_gap_before_and_passes_after_backfill(
    monkeypatch, tmp_path, throwaway_key, stub_prs, capsys
):
    log = _log_path(monkeypatch, tmp_path)
    stub_prs(_five_ui_prs())

    assert main(["--receipts", str(log)]) == 1
    out = capsys.readouterr().out
    for n in (571, 572, 573, 574, 575):
        assert f"#{n}" in out

    assert main(["--backfill", "--receipts", str(log)]) == 0
    capsys.readouterr()

    assert main(["--receipts", str(log)]) == 0
    out = capsys.readouterr().out
    assert "5/5" in out


# ── Failure modes ─────────────────────────────────────────────────────


def test_backfill_requires_signing_key(monkeypatch, tmp_path, stub_prs, capsys):
    log = _log_path(monkeypatch, tmp_path)
    stub_prs(_five_ui_prs())
    monkeypatch.delenv("PRISMATIC_MERGE_RECEIPT_SIGNING_KEY", raising=False)
    monkeypatch.setenv(
        "PRISMATIC_MERGE_RECEIPT_KEY_FILE", str(tmp_path / "no-such-key.pem")
    )

    rc = main(["--backfill", "--receipts", str(log)])

    assert rc == 2
    assert not log.exists()  # refuses to write unsigned backfills
    err = capsys.readouterr().err
    assert "signing key" in err


def test_backfill_unbindable_pr_fails_without_aborting_rest(
    monkeypatch, tmp_path, throwaway_key, stub_prs, capsys
):
    log = _log_path(monkeypatch, tmp_path)
    prs = _five_ui_prs()
    bad = _pr(576, "", "")  # no merge sha, no head sha: cannot bind
    bad["mergeCommit"] = {"oid": ""}
    bad["headRefOid"] = ""
    stub_prs(prs + [bad])

    rc = main(["--backfill", "--receipts", str(log)])
    out = capsys.readouterr().out

    assert rc == 1
    rows = _read_rows(log)
    assert len(rows) == 5  # the five bindable PRs still backfilled
    assert "#576" in out and "cannot bind" in out


def test_backfill_json_output_shape(monkeypatch, tmp_path, throwaway_key,
                                   stub_prs, capsys):
    log = _log_path(monkeypatch, tmp_path)
    stub_prs(_five_ui_prs())

    rc = main(["--backfill", "--json", "--receipts", str(log)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["mode"] == "backfill"
    assert payload["total"] == 5
    assert payload["already_covered"] == 0
    assert payload["backfilled"] == [571, 572, 573, 574, 575]
    assert payload["skipped_already_covered"] == []
    assert payload["failed"] == []
    assert payload["log_path"] == str(log)
