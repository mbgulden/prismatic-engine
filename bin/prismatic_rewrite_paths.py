#!/usr/bin/env python3
"""prismatic-rewrite-paths — Prismatic Engine post-processor (harness-agnostic).

Scans a string for local file paths and rewrites them to files.growthwebdev.com
links. The string can come from stdin or a file. Path detection rules:

  1. Anything that looks like /home/.../<name>.<ext> or /tmp/.../<name>.<ext>
  2. Anything that looks like /home/.../<dir>/<name>  (treat the dir as a tree link)

For each detected path:
  - Run `prismatic-publish` (one subprocess per path, in parallel).
  - If the publisher returns 0, replace the local path with the URL.
  - If the publisher returns non-zero, leave the path alone and append a
    short note like "(publish failed: <reason>)" so the user knows the
    link wasn't generated.

Usage:
  echo "See \$PRISMATIC_HOME/foo.md" | prismatic-rewrite-paths
  prismatic-rewrite-paths --in draft.md --out draft.rewrite.md
  cat reply.txt | prismatic-rewrite-paths --emit-links
  cat reply.txt | prismatic-rewrite-paths --json   # for testing

Environment:
  PRISMATIC_PUBLISH_BIN         (default: prismatic-publish)
  PRISMATIC_ARTIFACTS_HOSTNAME  (default: files.growthwebdev.com)
  PRISMATIC_PUBLISH_WORKSPACE   (default: published)
  PRISMATIC_PUBLISH_MAX_PARALLEL (default: 4)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PUBLISH_BIN = os.environ.get("PRISMATIC_PUBLISH_BIN", "prismatic-publish")
HOSTNAME = os.environ.get("PRISMATIC_ARTIFACTS_HOSTNAME", "files.growthwebdev.com")
WORKSPACE = os.environ.get("PRISMATIC_PUBLISH_WORKSPACE", "published")
MAX_PARALLEL = int(os.environ.get("PRISMATIC_PUBLISH_MAX_PARALLEL", "4"))

# Match absolute paths under /home, /tmp, /mnt, /var/*, or /root. We allow dots in
# the middle of the path so filenames like "foo.md" or "tarball.tar.gz" stay intact.
# We stop only at whitespace and a small set of sentence punctuation that is
# almost never part of a filename.
PATH_RE = re.compile(
    r"""(?P<path>/(?:home|tmp|mnt|root|var/[a-z]+)/(?:[^\s`'"\)\]\}\,\?!\:;]+))""",
    re.VERBOSE,
)

# Skip paths that look like a 1-character system dir (e.g. /etc, /var) and our own publisher path.
EXCLUDE_PATH_SUBSTRINGS = (
    "/.antigravity/",
    "/.cache/",
    "/node_modules/",
    "/.git/",
    "/__pycache__/",
)


def _strip_trailing_punct(p: str) -> str:
    # Strip a single trailing period that's not part of an extension
    # (.md, .json, .txt etc are common — don't strip those).
    if p.endswith(".") and not re.search(r"\.[A-Za-z0-9]{1,6}$", p):
        return p[:-1]
    return p.rstrip(",;:!?)'\"")


def _is_under_excluded(p: str) -> bool:
    return any(sub in p for sub in EXCLUDE_PATH_SUBSTRINGS)


def _publish_one(path: str) -> dict:
    """Call prismatic-publish; return {path, url, ok, error}."""
    try:
        proc = subprocess.run(
            [PUBLISH_BIN, path, "--workspace", WORKSPACE, "--skip-health-check", "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"path": path, "ok": False, "error": "timeout"}
    except FileNotFoundError:
        return {"path": path, "ok": False, "error": "prismatic-publish not found"}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()[-1] if (proc.stderr or proc.stdout) else f"exit {proc.returncode}"
        return {"path": path, "ok": False, "error": err}
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"path": path, "ok": False, "error": "bad json from publisher"}
    if isinstance(out, list) and out:
        out = out[0]
    return {"path": path, "ok": True, "url": out.get("url_raw", ""), "url_download": out.get("url_download", "")}


def _rewrite_text(text: str, parallel: int) -> tuple[str, list[dict]]:
    matches = list(PATH_RE.finditer(text))
    # Filter out excluded substrings and dedupe
    seen = set()
    candidates: list[str] = []
    for m in matches:
        p = _strip_trailing_punct(m.group("path"))
        if _is_under_excluded(p) or p in seen:
            continue
        seen.add(p)
        candidates.append(p)
    results: dict[str, dict] = {}
    if candidates:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futs = {ex.submit(_publish_one, p): p for p in candidates}
            for fut in as_completed(futs):
                p = futs[fut]
                try:
                    results[p] = fut.result()
                except Exception as e:
                    results[p] = {"path": p, "ok": False, "error": str(e)}
    # Build a single-pass replacer by walking matches in order
    out: list[str] = []
    last_end = 0
    summary: list[dict] = []
    for m in PATH_RE.finditer(text):
        p = _strip_trailing_punct(m.group("path"))
        if p not in results:
            continue
        out.append(text[last_end:m.start()])
        r = results[p]
        if r["ok"] and r.get("url"):
            out.append(r["url"])
            summary.append({"path": p, "ok": True, "url": r["url"]})
        else:
            tail = f" (publish failed: {r.get('error', '?')})"
            out.append(p + tail)
            summary.append({"path": p, "ok": False, "error": r.get("error", "?")})
        last_end = m.end()
    out.append(text[last_end:])
    return "".join(out), summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Rewrite local paths in text to files.growthwebdev.com URLs.")
    ap.add_argument("--in", dest="inp", help="Input file (default: stdin)")
    ap.add_argument("--out", dest="outp", help="Output file (default: stdout)")
    ap.add_argument("--emit-links", action="store_true", help="Also write a list of generated links to stdout after the rewritten text")
    ap.add_argument("--json", action="store_true", help="Emit JSON {rewritten, links} instead of plain text")
    ap.add_argument("--parallel", type=int, default=MAX_PARALLEL, help="Max parallel publish calls")
    args = ap.parse_args()

    text = Path(args.inp).read_text() if args.inp else sys.stdin.read()
    rewritten, summary = _rewrite_text(text, args.parallel)

    if args.json:
        print(json.dumps({"rewritten": rewritten, "links": summary}, indent=2))
    else:
        if args.outp:
            Path(args.outp).write_text(rewritten)
        else:
            print(rewritten, end="")
        if args.emit_links and not args.outp:
            for s in summary:
                if s.get("ok"):
                    print(s["url"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
