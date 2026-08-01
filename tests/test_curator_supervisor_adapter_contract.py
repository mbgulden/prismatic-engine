from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from prismatic.curator import issue_to_task
from prismatic.curator.dispatcher import build_supervisor_cmd

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "agy_sandbox_event_supervisor.py"


def _load_supervisor():
    spec = importlib.util.spec_from_file_location(
        "agy_supervisor_parser_contract_under_test", SUPERVISOR_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_curator_command_parses_as_exclusive_exact_issue():
    supervisor = _load_supervisor()
    command = build_supervisor_cmd(
        "GRO-4407",
        lane="codex",
        model="sonnet",
        supervisor_path=str(SUPERVISOR_PATH),
        python_executable=sys.executable,
        expected_release_root=str(REPO_ROOT),
    )

    args = supervisor.build_argument_parser().parse_args(command[2:])

    assert command.count("--issue") == 1
    assert "--from-linear" not in command
    assert "--lane" not in command
    assert args.issue == "GRO-4407"
    assert args.from_linear is False
    assert args.issues is None
    assert args.model == "sonnet"


def test_curator_module_resolves_inside_same_source_root():
    module_path = Path(issue_to_task.__file__).resolve()
    assert module_path.is_relative_to(REPO_ROOT)
