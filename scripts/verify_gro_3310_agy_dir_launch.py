#!/usr/bin/env python3
"""Verify GRO-3310 live AGY sandbox supervisor launch contract.

This intentionally checks the live orchestrator supervisor script because the
runtime source of truth for this task is outside the prismatic-engine checkout:
~/.hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py
"""
from __future__ import annotations

import ast
from pathlib import Path

SUPERVISOR = Path("/home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py")


def main() -> int:
    source = SUPERVISOR.read_text()
    tree = ast.parse(source, filename=str(SUPERVISOR))

    popens: list[ast.Call] = []
    cmd_assigns: list[ast.Assign] = []
    stdin_payload_markers = [
        "INJECTED_VIA_STDIN",
        "payload_data = json.dumps",
        "proc.stdin.write(payload_data",
        "stdin_pipe = subprocess.PIPE",
    ]

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "cmd":
                    cmd_assigns.append(node)
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "Popen"
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "cmd"
            ):
                popens.append(node)

    if not cmd_assigns:
        raise AssertionError("no cmd assignment found")
    cmd_src = ast.get_source_segment(source, cmd_assigns[0]) or ""
    if '"--dir", str(sandbox)' not in cmd_src:
        raise AssertionError("AGY cmd does not include --dir str(sandbox)")
    if '"--print", prompt' not in cmd_src:
        raise AssertionError("AGY cmd does not pass the bounded prompt as --print argument")

    leaked = [marker for marker in stdin_payload_markers if marker in source]
    if leaked:
        raise AssertionError(f"stdin/text-paste markers still present: {leaked}")

    if len(popens) < 2:
        raise AssertionError(f"expected at least 2 Popen sites, found {len(popens)}")
    for idx, call in enumerate(popens, start=1):
        kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        if "stdin" not in kwargs:
            raise AssertionError(f"Popen site {idx} does not set stdin explicitly")
        stdin_src = ast.get_source_segment(source, kwargs["stdin"]) or ""
        if stdin_src != "None":
            raise AssertionError(f"Popen site {idx} stdin is {stdin_src!r}, expected None")
        cwd_node = kwargs.get("cwd")
        cwd_src = ast.get_source_segment(source, cwd_node) if cwd_node is not None else ""
        if cwd_src != "str(sandbox)":
            raise AssertionError(f"Popen site {idx} cwd is {cwd_src!r}, expected str(sandbox)")

    print("GRO-3310 verification passed")
    print(f"supervisor={SUPERVISOR}")
    print("cmd includes --dir str(sandbox) and --print prompt")
    print(f"Popen sites checked={len(popens)}; stdin=None; cwd=str(sandbox)")
    print("stdin signed-payload/text-paste markers absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
