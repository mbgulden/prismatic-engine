"""Tests for the prompt registry: versioned files, content hashes, fail-closed."""

import pytest

from prismatic.jev import DecisionError
from prismatic.jev.prompts import PromptRef, PromptRegistry


def _write_registry(tmp_path, prompts, files):
    (tmp_path / "registry.json").write_text(
        __import__("json").dumps({"prompts": prompts})
    )
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return str(tmp_path)


def test_unknown_prompt_id_fails_closed():
    registry = PromptRegistry(dirs=[])
    # package registry ships empty: nothing resolves
    with pytest.raises(DecisionError, match="unknown prompt_id"):
        registry.resolve("anything")


def test_empty_prompt_id_fails_closed(tmp_path):
    d = _write_registry(tmp_path, {}, {})
    with pytest.raises(DecisionError, match="empty prompt_id"):
        PromptRegistry(dirs=[d]).resolve("  ")


def test_resolve_returns_versioned_hashed_ref(tmp_path):
    d = _write_registry(
        tmp_path,
        {"triage_v1": {"version": "3", "file": "triage_v1.txt"}},
        {"triage_v1.txt": "Triage this failure.\n"},
    )
    ref = PromptRegistry(dirs=[d]).resolve("triage_v1")
    assert isinstance(ref, PromptRef)
    assert ref.id == "triage_v1"
    assert ref.version == "3"
    assert ref.text == "Triage this failure.\n"
    assert len(ref.sha256) == 64
    assert ref.label() == f"triage_v1@3#{ref.sha256}"


def test_sha_changes_with_content(tmp_path):
    d = _write_registry(
        tmp_path,
        {"p": {"version": "1", "file": "p.txt"}},
        {"p.txt": "version one"},
    )
    r1 = PromptRegistry(dirs=[d]).resolve("p")
    (tmp_path / "p.txt").write_text("version two")
    r2 = PromptRegistry(dirs=[d]).resolve("p")
    assert r1.sha256 != r2.sha256


def test_missing_file_fails_closed(tmp_path):
    d = _write_registry(tmp_path, {"p": {"version": "1", "file": "missing.txt"}}, {})
    with pytest.raises(DecisionError, match="unknown prompt_id"):
        PromptRegistry(dirs=[d]).resolve("p")


def test_malformed_registry_fails_closed(tmp_path):
    (tmp_path / "registry.json").write_text("not json {{{")
    with pytest.raises(DecisionError, match="unknown prompt_id"):
        PromptRegistry(dirs=[str(tmp_path)]).resolve("p")


def test_first_dir_wins(tmp_path):
    d1 = tmp_path / "one"
    d1.mkdir()
    _write_registry(d1, {"p": {"version": "1", "file": "p.txt"}}, {"p.txt": "from one"})
    d2 = tmp_path / "two"
    d2.mkdir()
    _write_registry(d2, {"p": {"version": "2", "file": "p.txt"}}, {"p.txt": "from two"})
    ref = PromptRegistry(dirs=[str(d1), str(d2)]).resolve("p")
    assert ref.text == "from one"
    assert ref.version == "1"
