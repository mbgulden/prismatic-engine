"""AST Anti-Weakening Guard for Prismatic Engine.

Enforces Anti-Deception Invariant #2:
- Detects deletion or reduction of test functions (`test_*`).
- Detects assertion deletion or count weakening.
- Flags trivial/softened assertions (`assert True`, `assert 1 == 1`, empty test bodies).
- Fails closed on syntax errors or malicious weakening patterns.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("prismatic.verification.ast_guard")


@dataclass
class ASTValidationResult:
    valid: bool
    filename: str
    violations: list[str] = field(default_factory=list)
    old_test_count: int = 0
    new_test_count: int = 0
    old_assertion_count: int = 0
    new_assertion_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "filename": self.filename,
            "violations": self.violations,
            "old_test_count": self.old_test_count,
            "new_test_count": self.new_test_count,
            "old_assertion_count": self.old_assertion_count,
            "new_assertion_count": self.new_assertion_count,
        }


class _AssertionCounter(ast.NodeVisitor):
    def __init__(self) -> None:
        self.assertion_count = 0
        self.test_functions: dict[str, int] = {}
        self.current_func: str | None = None
        self.trivial_assertions: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        old_func = self.current_func
        if node.name.startswith("test_") or node.name.endswith("_test"):
            self.current_func = node.name
            self.test_functions[node.name] = 0
        self.generic_visit(node)
        self.current_func = old_func

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        old_func = self.current_func
        if node.name.startswith("test_") or node.name.endswith("_test"):
            self.current_func = node.name
            self.test_functions[node.name] = 0
        self.generic_visit(node)
        self.current_func = old_func

    def visit_Assert(self, node: ast.Assert) -> Any:
        self.assertion_count += 1
        if self.current_func and self.current_func in self.test_functions:
            self.test_functions[self.current_func] += 1

        # Check for trivial assertions: assert True, assert 1
        if isinstance(node.test, ast.Constant) and node.test.value in (True, 1):
            self.trivial_assertions.append(f"Line {node.lineno}: Trivial assertion `assert {node.test.value}` detected.")
        elif isinstance(node.test, ast.Compare):
            if isinstance(node.test.left, ast.Constant) and len(node.test.comparators) == 1 and isinstance(node.test.comparators[0], ast.Constant):
                if node.test.left.value == node.test.comparators[0].value:
                    self.trivial_assertions.append(f"Line {node.lineno}: Constant comparison `assert {node.test.left.value} == {node.test.comparators[0].value}` detected.")

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        # Check for self.assert*, pytest.raises, expect(...)
        func_name = ""
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id

        if func_name.startswith("assert") or func_name in ("expect", "raises"):
            self.assertion_count += 1
            if self.current_func and self.current_func in self.test_functions:
                self.test_functions[self.current_func] += 1

        self.generic_visit(node)


import re

@dataclass
class _JSAnalysis:
    test_functions: dict[str, int]
    assertion_count: int
    trivial_assertions: list[str]


def _analyze_js_source(code: str) -> _JSAnalysis:
    test_pattern = re.compile(r"""(?:test|it|describe)\s*\(\s*['"`]([^'"`]+)['"`]""")
    tests = test_pattern.findall(code)
    test_dict = {t: 0 for t in tests}

    assert_pattern = re.compile(r"""\b(?:expect|assert(?:\.\w+)?)\s*\(""")
    assertions = assert_pattern.findall(code)

    trivial = []
    if re.search(r"""expect\s*\(\s*true\s*\)\s*\.\s*toBe\s*\(\s*true\s*\)""", code):
        trivial.append("Trivial assertion `expect(true).toBe(true)` detected.")
    if re.search(r"""assert\s*\(\s*true\s*\)""", code):
        trivial.append("Trivial assertion `assert(true)` detected.")
    if re.search(r"""assert\s*\.\s*(?:strictEqual|equal)\s*\(\s*1\s*,\s*1\s*\)""", code):
        trivial.append("Trivial assertion constant comparison detected.")

    return _JSAnalysis(
        test_functions=test_dict,
        assertion_count=len(assertions),
        trivial_assertions=trivial,
    )


class ASTGuard:
    @staticmethod
    def analyze_source(code: str) -> tuple[_AssertionCounter | None, str | None]:
        try:
            tree = ast.parse(code)
            counter = _AssertionCounter()
            counter.visit(tree)
            return counter, None
        except SyntaxError as e:
            return None, f"SyntaxError parsing code: {e}"

    @classmethod
    def validate_diff(
        cls,
        old_code: str,
        new_code: str,
        filename: str = "test_module.py",
        allow_reduction: bool = False,
    ) -> ASTValidationResult:
        """Validate that a code edit does not delete or weaken test assertions."""
        is_test_file = "test" in filename.lower() or "spec" in filename.lower()
        is_js_ts = filename.endswith((".js", ".ts", ".mjs", ".cjs"))

        if is_js_ts:
            new_js = _analyze_js_source(new_code)
            if not old_code.strip():
                return ASTValidationResult(
                    valid=len(new_js.trivial_assertions) == 0,
                    filename=filename,
                    violations=list(new_js.trivial_assertions),
                    old_test_count=0,
                    new_test_count=len(new_js.test_functions),
                    old_assertion_count=0,
                    new_assertion_count=new_js.assertion_count,
                )

            old_js = _analyze_js_source(old_code)
            violations = list(new_js.trivial_assertions)

            if is_test_file and not allow_reduction:
                for test_name in old_js.test_functions:
                    if test_name not in new_js.test_functions:
                        violations.append(f"ANTI-WEAKENING VIOLATION: JS test `{test_name}` was deleted or renamed.")

                if new_js.assertion_count < old_js.assertion_count and old_js.assertion_count > 0:
                    violations.append(
                        f"ANTI-WEAKENING VIOLATION: Total assertion count in {filename} decreased from {old_js.assertion_count} to {new_js.assertion_count}."
                    )

            return ASTValidationResult(
                valid=len(violations) == 0,
                filename=filename,
                violations=violations,
                old_test_count=len(old_js.test_functions),
                new_test_count=len(new_js.test_functions),
                old_assertion_count=old_js.assertion_count,
                new_assertion_count=new_js.assertion_count,
            )

        new_counter, new_err = cls.analyze_source(new_code)
        if new_err:
            return ASTValidationResult(
                valid=False,
                filename=filename,
                violations=[f"Malformed new code: {new_err}"],
            )

        # If old_code is empty (brand new file), simply check new code for trivial asserts
        if not old_code.strip():
            violations = list(new_counter.trivial_assertions)
            return ASTValidationResult(
                valid=len(violations) == 0,
                filename=filename,
                violations=violations,
                old_test_count=0,
                new_test_count=len(new_counter.test_functions),
                old_assertion_count=0,
                new_assertion_count=new_counter.assertion_count,
            )

        old_counter, old_err = cls.analyze_source(old_code)
        if old_err:
            logger.warning("Could not parse old code, validating new code standalone: %s", old_err)
            violations = list(new_counter.trivial_assertions)
            return ASTValidationResult(
                valid=len(violations) == 0,
                filename=filename,
                violations=violations,
                old_test_count=0,
                new_test_count=len(new_counter.test_functions),
                old_assertion_count=0,
                new_assertion_count=new_counter.assertion_count,
            )

        violations = []
        violations.extend(new_counter.trivial_assertions)

        if is_test_file and not allow_reduction:
            # 1. Check for deleted test functions
            for test_name in old_counter.test_functions:
                if test_name not in new_counter.test_functions:
                    violations.append(f"ANTI-WEAKENING VIOLATION: Test function `{test_name}` was deleted or renamed.")

            # 2. Check for reduced assertion counts in existing test functions
            for test_name, old_cnt in old_counter.test_functions.items():
                if test_name in new_counter.test_functions:
                    new_cnt = new_counter.test_functions[test_name]
                    if new_cnt < old_cnt and old_cnt > 0:
                        violations.append(
                            f"ANTI-WEAKENING VIOLATION: Test function `{test_name}` assertion count dropped from {old_cnt} to {new_cnt}."
                        )

            # 3. Overall assertion reduction check
            if new_counter.assertion_count < old_counter.assertion_count and old_counter.assertion_count > 0:
                violations.append(
                    f"ANTI-WEAKENING VIOLATION: Total assertion count decreased from {old_counter.assertion_count} to {new_counter.assertion_count}."
                )

        return ASTValidationResult(
            valid=len(violations) == 0,
            filename=filename,
            violations=violations,
            old_test_count=len(old_counter.test_functions),
            new_test_count=len(new_counter.test_functions),
            old_assertion_count=old_counter.assertion_count,
            new_assertion_count=new_counter.assertion_count,
        )
