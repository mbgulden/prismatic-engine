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
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PUBLISH_BIN = os.environ.get("PRISMATIC_PUBLISH_BIN", "prismatic-publish")
HOSTNAME = os.environ.get("PRISMATIC_ARTIFACTS_HOSTNAME", "files.growthwebdev.com")
WORKSPACE = os.environ.get("PRISMATIC_PUBLISH_WORKSPACE", "published")
MAX_PARALLEL = int(os.environ.get("PRISMATIC_PUBLISH_MAX_PARALLEL", "4"))

# Backward-compatible public API used by older tests/callers. Direct Telegram
# fallback is retained only as an explicit fallback helper; the default CLI path
# publishes to the artifact host and does not notify Michael.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_HOME_CHANNEL = os.environ.get("TELEGRAM_HOME_CHANNEL") or os.environ.get("TELEGRAM_HOME_CHAT_ID", "")

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
        # Backward compatibility: older publisher invocations returned a plain URL.
        url = proc.stdout.strip()
        if url.startswith("http://") or url.startswith("https://"):
            return {"path": path, "ok": True, "url": url, "url_download": url}
        return {"path": path, "ok": False, "error": "bad json from publisher"}
    if isinstance(out, list) and out:
        out = out[0]
    return {"path": path, "ok": True, "url": out.get("url_raw", ""), "url_download": out.get("url_download", "")}


def upload_to_telegram(path: str) -> tuple[bool, str | None]:
    """Legacy fallback helper for callers that explicitly request Telegram upload."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_HOME_CHANNEL:
        return False, "Telegram credentials not configured"
    body = json.dumps({
        "chat_id": TELEGRAM_HOME_CHANNEL,
        "text": f"Artifact fallback: {path}",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode() or "{}")
        if data.get("ok") is False:
            return False, data.get("description", "Telegram API returned ok=false")
        return True, None
    except Exception as e:
        return False, str(e)


def publish_path(path: str) -> tuple[str | None, str | None]:
    """Legacy public wrapper returning (url, error)."""
    result = _publish_one(path)
    if result.get("ok") and result.get("url"):
        return result["url"], None
    err = result.get("error", "unknown error")
    tg_ok, tg_err = upload_to_telegram(path)
    if tg_ok:
        return None, f"failed to publish ({err}), uploaded to Telegram"
    return None, f"failed to publish ({err}) and Telegram upload failed ({tg_err})"


def rewrite_paths_in_text(text: str) -> str:
    """Legacy public wrapper that rewrites paths as Markdown links."""
    out: list[str] = []
    last_end = 0
    for m in PATH_RE.finditer(text):
        path = _strip_trailing_punct(m.group("path"))
        if _is_under_excluded(path):
            continue
        out.append(text[last_end:m.start()])
        url, err = publish_path(path)
        name = Path(path).name
        if url:
            out.append(f"[{name}]({url})")
        else:
            out.append(f"[{name} ({err})]")
        last_end = m.end()
    out.append(text[last_end:])
    return "".join(out)


def fred_prepare_reply(text: str) -> dict:
    """Legacy reply-prep helper: returns rewritten text plus fallback upload metadata."""
    uploads: list[dict] = []
    out: list[str] = []
    last_end = 0
    for m in PATH_RE.finditer(text):
        path = _strip_trailing_punct(m.group("path"))
        if _is_under_excluded(path):
            continue
        out.append(text[last_end:m.start()])
        url, err = publish_path(path)
        name = Path(path).name
        if url:
            out.append(f"[{name}]({url})")
        else:
            out.append(f"[{name} ({err})]")
            uploads.append({"path": path, "success": "uploaded to Telegram" in (err or ""), "error": err})
        last_end = m.end()
    out.append(text[last_end:])
    return {"text": "".join(out), "uploads": uploads}


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
