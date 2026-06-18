#!/usr/bin/env python3
"""Hermes Artifact Publisher — small FastAPI service that exposes
explicitly published files (and previews) on a stable, durable root
unrelated to the pipx-managed Hermes dashboard.

Rules:
- Only files/directories under an explicit ARTIFACT_ROOT are visible.
- Reads are READ-ONLY by default.
- Safety filter: refuses to serve any path matching BLOCKED_PATTERNS.
- /workspace-tree endpoint re-exports the existing Workspace Tree
  plugin's tree + preview + download behavior, but limited to the
  ARTIFACT_ROOT plus a small allowlist of additional safe read-only
  paths (so we can host journal reports, spec docs, AGY crack audits).
"""
from __future__ import annotations

import io
import mimetypes
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

# ── Configuration ──────────────────────────────────────────
HOST = os.environ.get("PRISMATIC_ARTIFACT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PRISMATIC_ARTIFACT_PORT", "9120"))
BASE_DIR = Path(__file__).resolve().parent

# Where published artifacts live. Anything outside these roots is invisible.
# All paths are anchored at $PRISMATIC_HOME (default: ~/work) so the engine
# binary is portable to any deployment.
PRISMATIC_HOME = os.environ.get("PRISMATIC_HOME", str(Path.home() / "work"))

ALLOWED_ROOTS: dict[str, str] = {
    "published": str(BASE_DIR / "published"),
    "hermes-research-reports": f"{PRISMATIC_HOME}/Hermes-Research/reports",
    "prismatic-engine": f"{PRISMATIC_HOME}/prismatic-engine",
    "agentic-swarm-ops": f"{PRISMATIC_HOME}/agentic-swarm-ops",
}

# Blocklists (lowercased). Refuse to serve any path whose name matches.
BLOCKED_NAME_PATTERNS = [
    re.compile(r"^\.env($|\.)", re.IGNORECASE),
    re.compile(r"id_(rsa|dsa|ecdsa|ed25519)$", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"credentials", re.IGNORECASE),
    re.compile(r"\.db$", re.IGNORECASE),
    re.compile(r"\.sqlite($|-)", re.IGNORECASE),
    re.compile(r"state\.json$", re.IGNORECASE),
    re.compile(r"auth\.json$", re.IGNORECASE),
    re.compile(r"\.htpasswd$", re.IGNORECASE),
]

# Previews are text-only and bounded in size.
PREVIEWABLE_EXTENSIONS = {
    ".json", ".md", ".txt", ".yaml", ".yml",
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".css", ".html", ".toml", ".cfg", ".ini",
    ".sh", ".bash", ".env", ".gitignore",
    ".csv", ".log", ".diff", ".patch",
}
MAX_PREVIEW_SIZE = int(os.environ.get("PRISMATIC_ARTIFACT_MAX_PREVIEW", str(1 * 1024 * 1024)))  # 1 MiB
MAX_DOWNLOAD_SIZE = int(os.environ.get("PRISMATIC_ARTIFACT_MAX_DOWNLOAD", str(50 * 1024 * 1024)))  # 50 MiB

app = FastAPI(title="Hermes Artifact Publisher", version="0.1.0")


# ── Helpers ────────────────────────────────────────────────
def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"


def _resolve(label: str) -> Path:
    if label not in ALLOWED_ROOTS:
        raise HTTPException(status_code=404, detail=f"Workspace '{label}' not found")
    p = Path(ALLOWED_ROOTS[label]).expanduser().resolve()
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Workspace path missing: {p}")
    return p


def _safe_join(root: Path, rel: str) -> Path:
    if rel and (rel.startswith("/") or ".." in Path(rel).parts):
        raise HTTPException(status_code=400, detail="Invalid relative path")
    candidate = (root / rel).resolve() if rel else root
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path escapes workspace root")
    return candidate


def _is_blocked(name: str) -> bool:
    return any(p.search(name) for p in BLOCKED_NAME_PATTERNS)


def _build_tree(root: Path, rel: str = "") -> dict[str, Any]:
    full = root / rel if rel else root
    if not full.exists():
        return {"name": full.name, "type": "error", "error": "not found"}

    if full.is_file():
        st = full.stat()
        ext = full.suffix.lower()
        ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
        return {
            "name": full.name,
            "type": "file",
            "path": str(full),
            "relative_path": rel or full.name,
            "size": st.st_size,
            "size_human": _human_bytes(st.st_size),
            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            "content_type": ctype,
            "extension": ext,
            "previewable": ext in PREVIEWABLE_EXTENSIONS,
            "blocked": _is_blocked(full.name),
        }

    children = []
    try:
        for entry in sorted(full.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith("."):
                continue
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            children.append(_build_tree(root, child_rel))
    except PermissionError:
        pass

    return {
        "name": full.name,
        "type": "directory",
        "path": str(full),
        "relative_path": rel or full.name,
        "children": children,
    }


# ── Routes ─────────────────────────────────────────────────
@app.get("/")
def index() -> dict[str, Any]:
    return {
        "service": "hermes-artifact-publisher",
        "version": app.version,
        "workspaces": list(ALLOWED_ROOTS.keys()),
        "endpoints": [
            "/health",
            "/workspaces",
            "/tree/{workspace}",
            "/tree/{workspace}/{path:path}",
            "/preview/{workspace}/{path:path}",
            "/download/{workspace}/{path:path}",
            "/download-all/{workspace}",
        ],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "hermes-artifact-publisher",
        "workspaces": {
            label: {
                "path": str(Path(p).expanduser().resolve()),
                "exists": Path(p).expanduser().exists(),
            }
            for label, p in ALLOWED_ROOTS.items()
        },
        "max_preview_size": MAX_PREVIEW_SIZE,
        "max_download_size": MAX_DOWNLOAD_SIZE,
    }


@app.get("/workspaces")
def workspaces() -> dict[str, Any]:
    return {
        "ok": True,
        "workspaces": [
            {
                "label": label,
                "path": str(Path(p).expanduser().resolve()),
                "exists": Path(p).expanduser().exists(),
            }
            for label, p in ALLOWED_ROOTS.items()
        ],
    }


@app.get("/tree/{workspace}")
@app.get("/tree/{workspace}/{path:path}")
def tree(workspace: str, path: str = "") -> dict[str, Any]:
    root = _resolve(workspace)
    target = _safe_join(root, path)
    if target.is_file():
        raise HTTPException(status_code=400, detail="Path is a file; use /preview or /download")
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    node = _build_tree(root, path)
    return {
        "ok": True,
        "workspace": workspace,
        "root": str(root),
        "tree": node,
    }


@app.get("/preview/{workspace}/{path:path}")
def preview(workspace: str, path: str) -> dict[str, Any]:
    root = _resolve(workspace)
    target = _safe_join(root, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if _is_blocked(target.name):
        raise HTTPException(status_code=403, detail="This file is blocked from preview by safety policy")
    ext = target.suffix.lower()
    if ext not in PREVIEWABLE_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"File type '{ext}' is not previewable as text")
    st = target.stat()
    if st.st_size > MAX_PREVIEW_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large for preview (max {_human_bytes(MAX_PREVIEW_SIZE)})")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="File is not valid UTF-8 text")
    return {
        "ok": True,
        "workspace": workspace,
        "path": str(target),
        "size": st.st_size,
        "size_human": _human_bytes(st.st_size),
        "content": content,
        "lines": content.count("\n") + 1,
    }


@app.get("/raw/{workspace}/{path:path}")
def raw(workspace: str, path: str) -> Any:
    """Serve a file with its real content type, read-only.

    Useful for viewing in a browser (e.g. markdown rendered as text/plain).
    """
    root = _resolve(workspace)
    target = _safe_join(root, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if _is_blocked(target.name):
        raise HTTPException(status_code=403, detail="This file is blocked from serving by safety policy")
    st = target.stat()
    if st.st_size > MAX_DOWNLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {_human_bytes(MAX_DOWNLOAD_SIZE)})")
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=ctype, filename=target.name)


@app.get("/download/{workspace}/{path:path}")
def download(workspace: str, path: str) -> Any:
    root = _resolve(workspace)
    target = _safe_join(root, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if _is_blocked(target.name):
        raise HTTPException(status_code=403, detail="This file is blocked from download by safety policy")
    st = target.stat()
    if st.st_size > MAX_DOWNLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {_human_bytes(MAX_DOWNLOAD_SIZE)})")
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        target,
        media_type=ctype,
        filename=target.name,
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


@app.get("/download-all/{workspace}")
def download_all(workspace: str) -> Any:
    root = _resolve(workspace)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail="Workspace is not a directory")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(root.rglob("*")):
            if fpath.is_file() and not fpath.name.startswith("."):
                if _is_blocked(fpath.name):
                    continue
                arcname = str(fpath.relative_to(root))
                zf.write(fpath, arcname)
    buf.seek(0)
    safe_name = workspace.lower().replace(" ", "-")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-all.zip"'},
    )


@app.get("/publish")
def publish_help() -> dict[str, Any]:
    return {
        "ok": True,
        "instructions": "Use the publish CLI helper, not this HTTP endpoint.",
        "cli": "python3 publish_artifact.py <local_source_path> [--workspace <label>] [--rel <rel/path>]",
        "default_workspace": "published",
        "root": str(BASE_DIR / "published"),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
