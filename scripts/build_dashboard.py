#!/usr/bin/env python3
"""Build the canonical Prismatic dashboard template from lossless fragments."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC_ROOT = REPO_ROOT / "prismatic/gateway/dashboard_src"
DEFAULT_MANIFEST = DEFAULT_SRC_ROOT / "manifest.json"
DEFAULT_CSS = REPO_ROOT / "prismatic/gateway/static/dashboard.css"
CSS_HASH_TOKEN = b"__DASHBOARD_CSS_HASH__"


class DashboardBuildError(RuntimeError):
    """Raised when the dashboard source manifest is unsafe or incomplete."""


def _resolve_under(root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise DashboardBuildError("fragment path must be a non-empty string")
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DashboardBuildError(
            f"fragment escapes dashboard_src root: {relative_path}"
        )
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise DashboardBuildError(
            f"fragment escapes dashboard_src root: {relative_path}"
        ) from exc
    return resolved


def load_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    if not manifest_path.exists():
        raise DashboardBuildError(f"manifest missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DashboardBuildError(f"manifest is not valid JSON: {exc}") from exc

    src_root = manifest_path.parent
    generated = manifest.get("generated")
    fragments = manifest.get("fragments")
    if not isinstance(generated, str) or not generated:
        raise DashboardBuildError("manifest.generated must be a non-empty string")
    if not isinstance(fragments, list) or not fragments:
        raise DashboardBuildError("manifest.fragments must be a non-empty list")

    generated_path = _resolve_generated_path(generated)
    seen: set[str] = set()
    normalized_fragments: list[dict[str, str]] = []
    for index, entry in enumerate(fragments):
        if not isinstance(entry, dict):
            raise DashboardBuildError(f"fragment entry {index} must be an object")
        rel = entry.get("path")
        if not isinstance(rel, str) or not rel:
            raise DashboardBuildError(f"fragment entry {index} has invalid path")
        if rel in seen:
            raise DashboardBuildError(f"duplicate fragment path: {rel}")
        seen.add(rel)
        fragment_path = _resolve_under(src_root, rel)
        if not fragment_path.exists() or not fragment_path.is_file():
            raise DashboardBuildError(f"fragment missing: {rel}")
        normalized_fragments.append({"path": rel})

    return {
        "version": manifest.get("version"),
        "generated_path": generated_path,
        "src_root": src_root,
        "fragments": normalized_fragments,
    }


def _resolve_generated_path(generated: str) -> Path:
    candidate = Path(generated)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise DashboardBuildError(f"generated path escapes repo root: {generated}")
    resolved_root = REPO_ROOT.resolve()
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise DashboardBuildError(
            f"generated path escapes repo root: {generated}"
        ) from exc
    return resolved


def build_bytes(manifest_path: Path = DEFAULT_MANIFEST) -> bytes:
    manifest = load_manifest(manifest_path)
    src_root: Path = manifest["src_root"]
    chunks: list[bytes] = []
    for entry in manifest["fragments"]:
        chunks.append((src_root / entry["path"]).read_bytes())
    payload = b"".join(chunks)
    if CSS_HASH_TOKEN in payload:
        if not DEFAULT_CSS.is_file():
            raise DashboardBuildError(f"built dashboard CSS missing: {DEFAULT_CSS}")
        css_hash = hashlib.sha256(DEFAULT_CSS.read_bytes()).hexdigest()[:12].encode()
        payload = payload.replace(CSS_HASH_TOKEN, css_hash)
    return payload


def write_dashboard(manifest_path: Path = DEFAULT_MANIFEST) -> Path:
    manifest = load_manifest(manifest_path)
    generated_path: Path = manifest["generated_path"]
    generated_path.parent.mkdir(parents=True, exist_ok=True)
    generated_path.write_bytes(build_bytes(manifest_path))
    return generated_path


def check_dashboard(manifest_path: Path = DEFAULT_MANIFEST) -> tuple[bool, Path]:
    manifest = load_manifest(manifest_path)
    generated_path: Path = manifest["generated_path"]
    expected = build_bytes(manifest_path)
    if not generated_path.exists():
        raise DashboardBuildError(f"generated dashboard missing: {generated_path}")
    return generated_path.read_bytes() == expected, generated_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated dashboard.html differs from fragments",
    )
    args = parser.parse_args(argv)

    try:
        if args.check:
            ok, generated_path = check_dashboard(args.manifest)
            if not ok:
                print(
                    f"dashboard generated file is stale: {generated_path}",
                    file=sys.stderr,
                )
                return 1
            print(f"dashboard generated file is fresh: {generated_path}")
            return 0
        generated_path = write_dashboard(args.manifest)
        print(f"wrote {generated_path}")
        return 0
    except DashboardBuildError as exc:
        print(f"dashboard build failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
