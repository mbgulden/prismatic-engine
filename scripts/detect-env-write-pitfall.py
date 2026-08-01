#!/usr/bin/env python3
import ast
import os
import sys


class EnvWritePitfallDetector(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.warnings = []

    def visit_FunctionDef(self, node):
        self.check_block(node.body, node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.check_block(node.body, node.name)
        self.generic_visit(node)

    def check_block(self, statements, function_name):
        seen_environ_mutation = False
        environ_mutation_line = None
        environ_mutation_var = None

        for stmt in statements:
            # Check for mutations in the current statement
            mutations = self._find_environ_mutations(stmt)
            if mutations:
                seen_environ_mutation = True
                environ_mutation_line = stmt.lineno
                environ_mutation_var = mutations[0]

            if seen_environ_mutation:
                writes = self._find_file_writes(stmt)
                if writes:
                    self.warnings.append(
                        {
                            "line": stmt.lineno,
                            "environ_line": environ_mutation_line,
                            "function": function_name,
                            "details": f"Risky env-var assignment to os.environ[{environ_mutation_var!r}] on line {environ_mutation_line} "
                            f"followed by file write ({writes[0]}) on line {stmt.lineno} in function '{function_name}'.",
                        }
                    )

            # Recurse into nested blocks
            if isinstance(stmt, ast.If) or isinstance(stmt, ast.For) or isinstance(stmt, ast.While):
                self.check_block(stmt.body, function_name)
                self.check_block(stmt.orelse, function_name)
            elif isinstance(stmt, ast.With):
                self.check_block(stmt.body, function_name)
            elif isinstance(stmt, ast.Try):
                self.check_block(stmt.body, function_name)
                for handler in stmt.handlers:
                    self.check_block(handler.body, function_name)
                self.check_block(stmt.orelse, function_name)
                self.check_block(stmt.finalbody, function_name)

    def _find_environ_mutations(self, node):
        mutations = []
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Subscript):
                        val = target.value
                        if isinstance(val, ast.Attribute) and val.attr == "environ":
                            if isinstance(val.value, ast.Name) and val.value.id in (
                                "os",
                                "_os",
                            ):
                                if isinstance(target.slice, ast.Constant):
                                    mutations.append(target.slice.value)
                                elif isinstance(target.slice, ast.Index) and isinstance(
                                    target.slice.value, ast.Constant
                                ):
                                    mutations.append(target.slice.value.value)
                                else:
                                    mutations.append("dynamic_key")
            elif isinstance(child, ast.Call):
                if isinstance(child.func, ast.Attribute) and child.func.attr in (
                    "update",
                    "setdefault",
                ):
                    val = child.func.value
                    if isinstance(val, ast.Attribute) and val.attr == "environ":
                        if isinstance(val.value, ast.Name) and val.value.id in (
                            "os",
                            "_os",
                        ):
                            mutations.append("environ_update")
        return mutations

    def _find_file_writes(self, node):
        writes = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Attribute):
                    if child.func.attr in (
                        "save_secret",
                        "write_text",
                        "write_bytes",
                        "write_file",
                    ):
                        writes.append(f"method call .{child.func.attr}()")
                elif isinstance(child.func, ast.Name):
                    if child.func.id in ("save_secret", "write_file"):
                        writes.append(f"function call {child.func.id}()")
            elif isinstance(child, ast.With):
                for item in child.items:
                    expr = item.context_expr
                    if isinstance(expr, ast.Call):
                        if isinstance(expr.func, ast.Name) and expr.func.id == "open":
                            is_write = False
                            for arg in expr.args:
                                if isinstance(arg, ast.Constant) and any(
                                    m in str(arg.value) for m in ("w", "a", "x")
                                ):
                                    is_write = True
                            for kw in expr.keywords:
                                if (
                                    kw.arg == "mode"
                                    and isinstance(kw.value, ast.Constant)
                                    and any(
                                        m in str(kw.value.value)
                                        for m in ("w", "a", "x")
                                    )
                                ):
                                    is_write = True
                            if is_write:
                                writes.append("open(..., mode='w/a/x')")
        return writes


def scan_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content, filename=filepath)
        detector = EnvWritePitfallDetector(filepath)
        detector.visit(tree)
        return detector.warnings
    except Exception:
        return []


def main():
    files_to_scan = []
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if os.path.isfile(arg) and arg.endswith(".py"):
                files_to_scan.append(arg)
    else:
        for root, _, files in os.walk("prismatic"):
            for file in files:
                if file.endswith(".py"):
                    files_to_scan.append(os.path.join(root, file))

    all_warnings = {}
    for filepath in files_to_scan:
        warnings = scan_file(filepath)
        if warnings:
            all_warnings[filepath] = warnings

    if all_warnings:
        print(
            "⚠️  [Prismatic Security Alert] Risky environment variable mutations followed by file writes detected:"
        )
        for filepath, warnings in all_warnings.items():
            print(f"\n  File: {filepath}")
            for w in warnings:
                print(f"    Line {w['line']}: {w['details']}")
        print(
            "\n💡 Tip: To prevent active process environment corruption if a file write fails,"
        )
        print(
            "   always perform the file write first, and only mutate 'os.environ' after the write succeeds."
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
