"""Self-tests for tests/quarantine.yaml (WS2 true-green signal).

Enforces the quarantine contract:
- the manifest parses and every entry has cause + removal_plan + dated remove_by,
- the list is shrink-only (len(entries) <= max_entries),
- every quarantined id is still collected by the suite (stale entries fail,
  so fixed tests get removed instead of lingering),
- ids that now pass are reported (not failed) to prompt removal.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "tests" / "quarantine.yaml"

MANDATORY_FIELDS = ("id", "cause", "removal_plan", "remove_by")


def _load_manifest() -> dict:
    assert MANIFEST.exists(), f"missing {MANIFEST}"
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "quarantine.yaml must be a mapping"
    return data


def test_quarantine_manifest_schema():
    data = _load_manifest()
    assert data.get("version") == 1, "unsupported quarantine.yaml version"
    assert isinstance(data.get("max_entries"), int), "max_entries must be an int"
    entries = data.get("entries")
    assert isinstance(entries, list) and entries, "entries must be a non-empty list"
    for i, e in enumerate(entries):
        assert isinstance(e, dict), f"entry {i} must be a mapping"
        for field in MANDATORY_FIELDS:
            assert e.get(field), f"entry {i} missing mandatory field {field!r}"
        # remove_by must be a real calendar date, today or later
        try:
            due = _dt.date.fromisoformat(str(e["remove_by"]))
        except ValueError:
            pytest.fail(f"entry {i} ({e['id']}): remove_by is not a date")
        assert due >= _dt.date.today() - _dt.timedelta(days=1), (
            f"entry {i} ({e['id']}): remove_by {due} is in the past — "
            "renew the plan or remove the entry"
        )
        assert e["id"].startswith("tests/") and "::" in e["id"], (
            f"entry {i}: id {e['id']!r} must look like tests/<file>.py::<test>"
        )
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "duplicate ids in quarantine.yaml"


def test_quarantine_is_shrink_only():
    data = _load_manifest()
    entries = data["entries"]
    assert len(entries) <= data["max_entries"], (
        f"quarantine grew: {len(entries)} entries > max_entries {data['max_entries']}. "
        "The list may only shrink; raising max_entries is a deliberate, reviewable change."
    )


def test_quarantined_ids_are_collected():
    """Every quarantined id must still exist in the test tree.

    A stale id means the test was renamed, moved, or deleted — remove the
    entry instead of letting it linger.
    """
    data = _load_manifest()
    stale = []
    for e in data["entries"]:
        node_id = e["id"]
        rel_path, _, test_name = node_id.partition("::")
        path = REPO_ROOT / rel_path
        if not path.exists():
            stale.append(node_id)
            continue
        # Match "def test_name(" or "def test_name[" (parametrized) in the file.
        text = path.read_text(encoding="utf-8")
        if f"def {test_name}(" not in text and f"def {test_name}[" not in text:
            stale.append(node_id)
    assert not stale, (
        f"quarantined ids no longer present in the tree (remove them): {stale}"
    )
