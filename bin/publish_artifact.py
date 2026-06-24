#!/usr/bin/env python3
"""publish_artifact.py — Helper CLI to publish files or directories to the Hermes Artifact Publisher.

Usage:
    python3 publish_artifact.py <source> [--workspace LABEL] [--rel REL]
"""
import argparse
import sys
import os
import json
import urllib.request
import urllib.error
import shutil
import re
from pathlib import Path

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
]

def _is_blocked(name: str) -> bool:
    return any(p.search(name) for p in BLOCKED_NAME_PATTERNS)

def get_workspace_path(label: str) -> Path:
    # Attempt to query the running publisher workspaces API
    try:
        req = urllib.request.Request("http://127.0.0.1:9120/workspaces")
        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                workspaces = data.get("workspaces", {})
                if label in workspaces:
                    return Path(workspaces[label])
    except Exception:
        pass

    # Fallback to local configuration matching bin/prismatic_artifact_publisher.py
    prismatic_home = os.environ.get("PRISMATIC_HOME", "/home/ubuntu/work")
    defaults = {
        "published": Path(prismatic_home) / "published",
        "hermes-research-reports": Path(prismatic_home) / "hermes-research-reports",
        "prismatic-engine": Path(prismatic_home) / "prismatic-engine",
        "agentic-swarm-ops": Path(prismatic_home) / "agentic-swarm-ops",
    }
    
    if label in defaults:
        return defaults[label]
    
    print(f"Error: Unknown workspace label '{label}'", file=sys.stderr)
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Publish a file or directory to Hermes Artifact Publisher.")
    parser.add_argument("source", help="Path to the file or directory to publish.")
    parser.add_argument("--workspace", default="published", help="Workspace label (default: 'published').")
    parser.add_argument("--rel", help="Relative path within the workspace destination.")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"Error: Source path '{source}' does not exist.", file=sys.stderr)
        sys.exit(1)

    workspace_path = get_workspace_path(args.workspace).expanduser().resolve()
    if not workspace_path.exists():
        if args.workspace == "published":
            workspace_path.mkdir(parents=True, exist_ok=True)
        else:
            print(f"Error: Workspace path '{workspace_path}' does not exist.", file=sys.stderr)
            sys.exit(1)
    
    # Resolve the destination relative path
    rel_path = args.rel if args.rel else source.name
    
    # Check for path traversal / escaping workspace
    dest_path = (workspace_path / rel_path).resolve()
    try:
        dest_path.relative_to(workspace_path)
    except ValueError:
        print("Error: Destination path escapes workspace root.", file=sys.stderr)
        sys.exit(1)

    # Ensure target directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if source.is_file():
        # If dest_path exists and is a directory, append the source filename
        if dest_path.is_dir():
            dest_file = dest_path / source.name
        else:
            dest_file = dest_path
        
        if _is_blocked(dest_file.name):
            print(f"Error: File '{dest_file.name}' is blocked by safety policy.", file=sys.stderr)
            sys.exit(1)
            
        shutil.copy2(source, dest_file)
        final_rel = dest_file.relative_to(workspace_path)
    else:
        # Source is a directory
        for root, dirs, files in os.walk(source):
            for file in files:
                if _is_blocked(file):
                    print(f"Error: Source contains blocked file '{file}'. Publication aborted.", file=sys.stderr)
                    sys.exit(1)
                    
        shutil.copytree(source, dest_path, dirs_exist_ok=True)
        final_rel = dest_path.relative_to(workspace_path)

    # Print the Cloudflare URL
    url_rel = str(final_rel).replace(os.sep, "/")
    url = f"https://files.growthwebdev.com/raw/{args.workspace}/{url_rel}"
    print(url)

if __name__ == "__main__":
    main()
