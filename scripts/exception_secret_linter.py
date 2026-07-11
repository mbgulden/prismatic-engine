#!/usr/bin/env python3
"""
Exception & Log Secret Linter.
Checks python source files for unredacted secrets/tokens in exception messages
and logger calls.
"""

import ast
import os
import re
import sys
from pathlib import Path

CREDENTIAL_REGEXES = [
    re.compile(r"sk-(?:or-|admin-|proj-|svcacct-|ant-)?[A-Za-z0-9+/=_]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9+/=_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"gsk_[A-Za-z0-9]{30,60}"),
    re.compile(r"ghp_[A-Za-z0-9]{36,40}"),
    re.compile(r"gho_[A-Za-z0-9]{36,40}"),
    re.compile(r"ghu_[A-Za-z0-9]{36,40}"),
    re.compile(r"ghs_[A-Za-z0-9]{36,40}"),
    re.compile(r"ghr_[A-Za-z0-9]{36,40}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,90}"),
    re.compile(r"lin_api_[A-Za-z0-9]{30,50}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[bpu]-[A-Za-z0-9-]+"),
    re.compile(r"eyJ[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{10,}"),
]

SENSITIVE_TERMS = ["token", "secret", "password", "passwd", "credential"]
SENSITIVE_KEY_PREFIXES = [
    "api",
    "secret",
    "private",
    "access",
    "auth",
    "encryption",
    "signing",
    "ssh",
    "client",
    "user",
    "admin",
    "token",
]
SAFE_TERMS = [
    "_type",
    "_expiry",
    "_length",
    "_count",
    "_time",
    "_at",
    "_id",
    "_path",
    "_file",
    "_url",
    "_name",
    "_status",
    "key_name",
    "keys",
    "dict_keys",
    "key_err",
]


def is_sensitive_var_name(name: str) -> bool:
    name_lower = name.lower()
    # Check if it contains standard sensitive terms, excluding safe terms
    if any(term in name_lower for term in SENSITIVE_TERMS):
        if not any(safe in name_lower for safe in SAFE_TERMS):
            return True

    # Check if it contains a sensitive key name
    if "key" in name_lower:
        if any(
            prefix + "key" in name_lower or prefix + "_key" in name_lower
            for prefix in SENSITIVE_KEY_PREFIXES
        ):
            if not any(safe in name_lower for safe in SAFE_TERMS):
                return True

    return False


def is_redactor_call(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id.lower() in (
                "redact",
                "mask",
                "obfuscate",
                "redact_token",
                "redact_secret",
            )
        elif isinstance(func, ast.Attribute):
            return func.attr.lower() in (
                "redact",
                "mask",
                "obfuscate",
                "redact_token",
                "redact_secret",
            )
    return False


class SecretLinterVisitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.violations = []

    def visit_Raise(self, node: ast.Raise):
        if node.exc:
            self._check_exc_expr(node.exc)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func = node.func
        is_log_call = False
        if isinstance(func, ast.Attribute):
            # matches e.g. logger.info, self.logger.error, logging.warning
            func_name = func.attr
            if func_name in (
                "info",
                "debug",
                "warning",
                "warn",
                "error",
                "critical",
                "exception",
                "log",
            ):
                is_log_call = True
        elif isinstance(func, ast.Name):
            # matches e.g. print, log
            if func.id in ("print", "log"):
                is_log_call = True

        if is_log_call:
            for arg in node.args:
                self._check_node(arg, is_exception=False)
            for kw in node.keywords:
                self._check_node(kw.value, is_exception=False)
        self.generic_visit(node)

    def _check_exc_expr(self, exc_node: ast.AST):
        if isinstance(exc_node, ast.Call):
            for arg in exc_node.args:
                self._check_node(arg, is_exception=True)
            for kw in exc_node.keywords:
                self._check_node(kw.value, is_exception=True)
        elif isinstance(exc_node, ast.Name):
            # raise exc
            pass

    def _check_node(self, node: ast.AST, is_exception: bool):
        # We recursively walk the node to look for variables or constants
        stack = [node]
        while stack:
            curr = stack.pop()
            if curr is None:
                continue

            if is_redactor_call(curr):
                # Wrapped in redaction function: safe!
                continue

            if isinstance(curr, ast.Constant) and isinstance(curr.value, str):
                for pattern in CREDENTIAL_REGEXES:
                    if pattern.search(curr.value):
                        ctx = "exception" if is_exception else "log"
                        self.violations.append(
                            (
                                curr.lineno,
                                f"Hardcoded credential pattern in {ctx} message: '{curr.value}'",
                            )
                        )
            elif isinstance(curr, ast.Name):
                if is_sensitive_var_name(curr.id):
                    ctx = "exception" if is_exception else "log"
                    self.violations.append(
                        (
                            curr.lineno,
                            f"Unredacted sensitive variable '{curr.id}' in {ctx} message",
                        )
                    )
            elif isinstance(curr, ast.JoinedStr):
                # f-string: inspect values
                stack.extend(curr.values)
            elif isinstance(curr, ast.FormattedValue):
                stack.append(curr.value)
            elif isinstance(curr, ast.BinOp):
                stack.append(curr.left)
                stack.append(curr.right)
            elif isinstance(curr, ast.Call):
                # check arguments of nested call
                stack.extend(curr.args)
                for kw in curr.keywords:
                    stack.append(kw.value)
                stack.append(curr.func)


def scan_file(filepath: Path) -> list[tuple[int, str]]:
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(filepath))
    except Exception:
        # If there's a syntax error, skip or report
        return []
    visitor = SecretLinterVisitor(str(filepath))
    visitor.visit(tree)
    return visitor.violations


def main():
    files = []
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            p = Path(arg)
            if p.exists() and p.suffix == ".py":
                files.append(p)
    else:
        # scan entire workspace
        exclude_dirs = {".git", "node_modules", "__pycache__", ".venv", ".venv_dev"}
        for root, dirs, filenames in os.walk("."):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for f in filenames:
                if f.endswith(".py"):
                    files.append(Path(root) / f)

    total_violations = 0
    for f in sorted(files):
        violations = scan_file(f)
        if violations:
            total_violations += len(violations)
            for line, msg in violations:
                print(f"❌ {f}:{line}: {msg}")

    if total_violations > 0:
        print(
            f"\n🚨 Exception & Log Secret Linter: Found {total_violations} violations!"
        )
        sys.exit(1)
    else:
        print("✅ Exception & Log Secret Linter: Clean!")
        sys.exit(0)


if __name__ == "__main__":
    main()
