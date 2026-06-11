"""
Workspace Tree Navigator — Backend API

Serves real directory trees from configured workspace roots.
Supports text preview, PDF download, and mobile-responsive layout.
"""
from __future__ import annotations

import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import tempfile
import zipfile

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()


def _cleanup_temp_file(temp_path: str):
    """Safely delete a temporary file."""
    try:
        os.remove(temp_path)
    except Exception:
        pass


# ── Configuration ──────────────────────────────────────────

# Workspace roots: label → absolute path
# Edit this dict to add more workspaces. Each becomes a top-level folder.
WORKSPACE_ROOTS: dict[str, str] = {
    "HD Reports": "/home/ubuntu/work/hd-reports",
    "HD Birth Data": "/home/ubuntu/work/next-step-bot",
}

IGNORED_DIRS = {
    "node_modules",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "env",
    ".ipynb_checkpoints",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}


def get_workspace_roots() -> dict[str, str]:
    """Dynamically discover all workspace roots in /home/ubuntu/work/"""
    roots = dict(WORKSPACE_ROOTS)
    work_dir = Path("/home/ubuntu/work")
    if work_dir.exists() and work_dir.is_dir():
        for item in work_dir.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                label = " ".join(
                    word.capitalize()
                    for word in item.name.replace("-", " ")
                    .replace("_", " ")
                    .split()
                )
                resolved_item = str(item.resolve())
                if resolved_item not in [
                    str(Path(p).resolve()) for p in roots.values()
                ]:
                    roots[label] = resolved_item
    return roots

# File extensions we can preview as text
PREVIEWABLE_EXTENSIONS = {
    ".json", ".md", ".txt", ".yaml", ".yml",
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".css", ".html", ".toml", ".cfg", ".ini",
    ".sh", ".bash", ".env", ".gitignore",
}

# Max file size for text preview (bytes)
MAX_PREVIEW_SIZE = int(os.environ.get("HERMES_WORKSPACE_MAX_PREVIEW", "524288"))  # 512 KiB

# ── Helpers ────────────────────────────────────────────────

def _resolve_tree(root_path: str, rel: str = "") -> dict[str, Any]:
    """Build a recursive tree node for a directory or file."""
    full = Path(root_path) / rel if rel else Path(root_path)
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
            "relative_path": rel if rel else full.name,
            "size": st.st_size,
            "size_human": _human_bytes(st.st_size),
            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
            "content_type": ctype,
            "extension": ext,
            "previewable": ext in PREVIEWABLE_EXTENSIONS,
            "is_pdf": ext == ".pdf" or ctype == "application/pdf",
            "is_image": ctype.startswith("image/"),
            "is_audio": ctype.startswith("audio/"),
            "is_video": ctype.startswith("video/"),
        }

    # Directory
    children = []
    try:
        for entry in sorted(full.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name.startswith(".") or entry.name in IGNORED_DIRS:
                continue
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            children.append(_resolve_tree(root_path, child_rel))
    except PermissionError:
        pass

    return {
        "name": full.name,
        "type": "directory",
        "path": str(full),
        "relative_path": rel if rel else full.name,
        "children": children,
    }


def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"


# ── Routes ─────────────────────────────────────────────────

@router.get("/health")
async def health():
    roots = get_workspace_roots()
    return {
        "ok": True,
        "workspaces": list(roots.keys()),
        "max_preview_size": MAX_PREVIEW_SIZE,
        "max_preview_human": _human_bytes(MAX_PREVIEW_SIZE),
    }


@router.get("/tree")
async def get_tree():
    """Return the full workspace tree."""
    workspaces = []
    roots = get_workspace_roots()
    for label, root in roots.items():
        root_path = Path(root).expanduser().resolve()
        if not root_path.exists():
            workspaces.append({
                "name": label,
                "type": "directory",
                "path": str(root_path),
                "relative_path": label,
                "children": [],
                "error": f"path not found: {root_path}",
            })
            continue
        node = _resolve_tree(str(root_path))
        node["name"] = label  # override with display label
        node["relative_path"] = label
        workspaces.append(node)

    return {
        "ok": True,
        "workspaces": workspaces,
        "workspace_count": len(workspaces),
    }


@router.get("/preview")
async def preview_file(path: str = Query(..., description="Absolute file path")):
    """Return text content of a previewable file."""
    target = Path(path).expanduser().resolve()

    # Security: must be under a configured workspace root
    allowed = False
    roots = get_workspace_roots()
    for root in roots.values():
        root_path = Path(root).expanduser().resolve()
        try:
            if target.is_relative_to(root_path):
                allowed = True
                break
        except (ValueError, OSError):
            continue

    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied — not under a configured workspace root")

    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

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
        "name": target.name,
        "path": str(target),
        "size": st.st_size,
        "size_human": _human_bytes(st.st_size),
        "content": content,
        "lines": content.count("\n") + 1,
    }


@router.get("/download")
async def download_file(
    path: str = Query(..., description="Absolute file path"),
    background_tasks: BackgroundTasks = None,
):
    """Download any file or folder from a configured workspace."""
    target = Path(path).expanduser().resolve()

    # Security: must be under a configured workspace root
    allowed = False
    roots = get_workspace_roots()
    for root in roots.values():
        root_path = Path(root).expanduser().resolve()
        try:
            if target.is_relative_to(root_path):
                allowed = True
                break
        except (ValueError, OSError):
            continue

    if not allowed:
        raise HTTPException(status_code=403, detail="Access denied")

    if target.is_dir():
        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            temp_file.close()

            with zipfile.ZipFile(temp_file.name, "w", zipfile.ZIP_DEFLATED) as zipf:
                for root_dir, _, files in os.walk(target):
                    for file in files:
                        file_path = Path(root_dir) / file
                        # Calculate relative path inside the zip file
                        arcname = file_path.relative_to(target)
                        zipf.write(file_path, arcname)

            if background_tasks:
                background_tasks.add_task(_cleanup_temp_file, temp_file.name)

            return FileResponse(
                temp_file.name,
                media_type="application/zip",
                filename=f"{target.name}.zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{target.name}.zip"',
                },
            )
        except Exception as e:
            try:
                os.remove(temp_file.name)
            except Exception:
                pass
            raise HTTPException(
                status_code=500, detail=f"Failed to package folder as ZIP: {str(e)}"
            )

    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(
        target,
        media_type=ctype,
        filename=target.name,
        headers={
            "Content-Disposition": f'attachment; filename="{target.name}"',
        },
    )
