from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "prismatic/gateway/dashboard_src/tabs/dashboard.html"
SCRIPT = ROOT / "prismatic/gateway/dashboard_src/scripts/dashboard.js"
GENERATED = ROOT / "prismatic/gateway/templates/dashboard.html"
BUILD_SCRIPT = ROOT / "scripts/build_dashboard.py"


def _build_module():
    spec = importlib.util.spec_from_file_location("build_dashboard", BUILD_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task_admission_panel_is_in_canonical_source_and_generated_dashboard() -> None:
    source = SOURCE.read_text()
    generated = GENERATED.read_text()
    for marker in (
        'id="admission-panel"',
        'data-proof-marker="dashboard-admission-v1"',
        "Records durable intent only; does not launch a producer.",
        'id="admission-token" type="password" autocomplete="off"',
        'id="admission-writer-cap" value="1" readonly',
    ):
        assert marker in source
        assert marker in generated


def test_generated_dashboard_is_exact_deterministic_build() -> None:
    module = _build_module()
    assert module.build_bytes() == GENERATED.read_bytes()


def test_browser_code_uses_transient_bearer_and_clears_it() -> None:
    script = SCRIPT.read_text()
    assert '"Authorization": `Bearer ${token}`' in script
    assert 'headers["Idempotency-Key"] = idempotencyKey' in script
    assert 'tokenInput.value = ""' in script
    assert "finally" in script
    assert (
        "localStorage.setItem"
        not in script[script.index("function taskAdmissionValue") :]
    )
    assert "sessionStorage" not in script[script.index("function taskAdmissionValue") :]
    assert "console.log" not in script[script.index("function taskAdmissionValue") :]


def test_rendered_proof_is_bounded_and_never_renders_token() -> None:
    script = SCRIPT.read_text()
    block = script[
        script.index("function renderTaskAdmissionProof") : script.index(
            "async function fetchTaskAdmissionHistory"
        )
    ]
    assert "token" not in block.lower()
    assert "authorization" not in block.lower()
    assert "launch_performed: false" in block
    assert ".slice(0, 12)" in block
    assert ".slice(0, 16)" in block


def test_ui_exposes_bounded_error_and_conflict_status_path() -> None:
    script = SCRIPT.read_text()
    assert "boundedAdmissionError" in script
    assert "response.ok" in script
    assert "Exact admission replay confirmed; no duplicate event created." in script
    assert "Durable admission recorded. Producer was not launched." in script
