from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "dry_runs" / "e2e_theme_dry_run.py"
spec = importlib.util.spec_from_file_location("e2e_theme_dry_run", MODULE_PATH)
assert spec is not None and spec.loader is not None
dry_run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = dry_run
spec.loader.exec_module(dry_run)


def test_fixture_dry_run_generates_expected_artifacts(tmp_path: Path) -> None:
    result = dry_run.run_dry_run(dry_run.DEFAULT_FIXTURE, tmp_path, run_id="fixture-run")

    assert result.output_dir == tmp_path / "fixture-run"
    for path in [
        result.route_plan,
        result.module_plan,
        result.linear_tree,
        result.theme_manifest,
        result.verification_report,
    ]:
        assert path.exists(), path

    route_plan = json.loads(result.route_plan.read_text())
    module_plan = json.loads(result.module_plan.read_text())
    linear_tree = json.loads(result.linear_tree.read_text())
    manifest = json.loads(result.theme_manifest.read_text())
    report = json.loads(result.verification_report.read_text())

    assert route_plan["route_count"] >= 6
    assert route_plan["routes"][0]["path"] == "/"
    assert module_plan["theme_family"] == "trust-light"
    assert "lead-capture" in manifest["modules"]
    assert linear_tree["mode"] == "artifact_only_no_linear_mutation"
    assert linear_tree["assertions"]["no_dispatch_ready"] is True
    assert linear_tree["assertions"]["no_production_deploy"] is True
    assert report["ok"] is True


def test_fixture_dry_run_rejects_incomplete_intake(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"client_id":"bad"}')

    try:
        dry_run.run_dry_run(bad, tmp_path)
    except ValueError as exc:
        assert "missing required field" in str(exc)
        assert "business_name" in str(exc)
    else:
        raise AssertionError("expected incomplete intake to be rejected")


def test_generated_scaffold_contains_emdash_locked_fields(tmp_path: Path) -> None:
    result = dry_run.run_dry_run(dry_run.DEFAULT_FIXTURE, tmp_path, run_id="fields")
    fields = json.loads((result.output_dir / "theme-scaffold" / "emdash" / "fields.json").read_text())

    assert "complianceClaims" in fields["lockedFields"]
    assert "legalName" in fields["lockedFields"]
    assert {block["blockId"] for block in fields["blocks"]} >= {"hero", "lead-capture", "trust-panel"}
