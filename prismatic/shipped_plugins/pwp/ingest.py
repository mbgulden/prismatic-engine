"""
PWP Ingest - parses any client's 5 Website Dev Framework docs into structured JSON.

Library API:
    from prismatic_web_publisher.ingest import run_ingest
    result = run_ingest(docs_dir, output_dir=None, dry_run=False)
    # Returns: dict with keys: client_profile, content_brief, report, missing_fields, paths

CLI:
    python -m prismatic_web_publisher.ingest <docs-dir> [--out DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Framework docs to look for (5 canonical names)
FRAMEWORK_DOCS = [
    ("content_gathering_guide", "Content Gathering Guide"),
    ("partner_interview", "Partner Interview"),
    ("brand_design_interview", "Brand & Design Interview"),
    ("conversion_launch_kit", "Conversion & Launch Kit"),
    ("post_purchase_automation", "Post-Purchase Automation"),
]

# Schema for client_profile.json
CLIENT_PROFILE_SCHEMA = {
    "client_profile": {
        "name": "",
        "mission": "",
        "values": [],
        "service_area": "",
        "instructor_bio": "",
        "differentiators": [],
    },
    "brand": {
        "colors": {
            "primary": "#1E293B",
            "secondary": "#78866B",
            "accent": "#C87971",
            "neutral_light": "#F8FAFC",
            "neutral_dark": "#334155",
        },
        "typography": {
            "heading_font": "Outfit",
            "body_font": "Inter",
        },
        "mood": [],
    },
    "content": {
        "classes": [],
        "lead_magnets": [],
        "case_studies": [],
    },
    "automation": {
        "email_sequences": [],
        "post_purchase_flows": [],
    },
    "tech": {
        "platform": "",
        "domain": "",
        "analytics": "",
    },
}


def slugify(s: str) -> str:
    """Convert a string to a URL-safe slug."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def read_doc(path: Path) -> str:
    """Read a doc file, return contents (handles encoding)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def find_5_docs(docs_dir: Path) -> dict:
    """Find the 5 framework docs in a directory by name pattern matching.

    Returns: dict mapping canonical_name -> Path
    """
    docs = {}
    if not docs_dir.exists():
        return docs

    # Patterns: "Doc 1 - Content Gathering" or "content_gathering" or "content-gathering"
    for path in docs_dir.iterdir():
        if not path.is_file():
            continue
        name = path.stem.lower()
        for canonical, _ in FRAMEWORK_DOCS:
            # Match: "doc_1_-_content_gathering_guide" or "content_gathering_guide"
            if canonical in name or canonical.replace("_", "-") in name or canonical.replace("_", " ") in name:
                if canonical not in docs:  # take first match
                    docs[canonical] = path
                break
    return docs


def extract_with_agy(docs: dict) -> dict:
    """Use AGY to extract structured data from the 5 docs."""
    # Combine all docs into one prompt
    docs_text = ""
    for canonical, path in docs.items():
        content = read_doc(path)[:8000]  # truncate per doc to fit context
        docs_text += f"\n\n=== {canonical} ===\n{content}\n"

    prompt = (
        "You are parsing a client's 5 Website Development Framework documents. "
        "Extract structured data and return ONLY valid JSON (no markdown, no commentary). "
        "Use the keys: client_profile (name, mission, values, service_area, instructor_bio, differentiators), "
        "brand (colors, typography, mood), content (classes, lead_magnets, case_studies), "
        "automation (email_sequences, post_purchase_flows), tech (platform, domain, analytics). "
        "If a field isn't in the docs, use an empty string or empty list. "
        "Output ONLY the JSON object.\n\n"
        f"DOCUMENTS:\n{docs_text}"
    )

    result = subprocess.run(
        [
            "/home/ubuntu/.local/bin/agy",
            "--model", "Gemini 3.5 Flash (High)",
            "--prompt", prompt,
            "--dangerously-skip-permissions",
            "--print-timeout", "3m0s",
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )

    if result.returncode != 0:
        print(f"  AGY error: {result.stderr[:300]}", file=sys.stderr)
        return {}

    output = result.stdout.strip()
    # Strip any leading/trailing markdown code fences
    output = re.sub(r"^```(?:json)?\s*\n", "", output, flags=re.IGNORECASE)
    output = re.sub(r"\n```\s*$", "", output)

    # Try to find JSON
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        # Try to find a JSON block
        for line in output.split("\n"):
            if line.strip().startswith("{") and line.strip().endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return {}


def write_ingest_report(report_path: Path, docs: dict, extracted: dict, missing: list):
    """Write the human-readable ingest report."""
    lines = ["# Ingest Report\n"]
    lines.append(f"## Documents Found: {len(docs)}/5\n")
    for canonical, description in FRAMEWORK_DOCS:
        status = "✓" if canonical in docs else "✗"
        lines.append(f"- {status} {description}")
    lines.append("")

    if extracted:
        lines.append("## Extracted Data Summary\n")
        cp = extracted.get("client_profile", {})
        lines.append(f"- **Client Name**: {cp.get('name', '(not set)')}")
        lines.append(f"- **Mission**: {(cp.get('mission') or '')[:100]}")
        lines.append(f"- **Service Area**: {cp.get('service_area', '(not set)')}")
        lines.append(f"- **Classes**: {len(extracted.get('content', {}).get('classes', []))}")
        lines.append(f"- **Lead Magnets**: {len(extracted.get('content', {}).get('lead_magnets', []))}")
        lines.append(f"- **Email Sequences**: {len(extracted.get('automation', {}).get('email_sequences', []))}")
        lines.append(f"- **Post-Purchase Flows**: {len(extracted.get('automation', {}).get('post_purchase_flows', []))}")
        lines.append("")

    if missing:
        lines.append("## Missing Required Fields\n")
        for m in missing:
            lines.append(f"- {m}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


# ── Library API ──────────────────────────────────────────────

def run_ingest(
    docs_dir: str | Path,
    output_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Library API: parse 5 docs and return structured data.

    Args:
        docs_dir: Path to the directory containing the 5 Website Dev docs
        output_dir: Where to write the output files. Defaults to <docs_dir>/output/<slug>/
        dry_run: If True, don't write any files

    Returns:
        dict with keys:
            - client_profile: dict
            - content_brief: dict
            - report_path: Path or None
            - paths: dict of written file paths
            - missing_fields: list of strings (empty if all required fields present)
            - status: "ok" | "partial" | "error"
            - error: string (if status == "error")
    """
    docs_dir = Path(docs_dir).resolve()
    if not docs_dir.is_dir():
        return {"status": "error", "error": f"{docs_dir} is not a directory"}

    docs = find_5_docs(docs_dir)
    if not docs:
        return {"status": "error", "error": "no docs found matching the 5 framework patterns"}

    extracted = extract_with_agy(docs)
    if not extracted:
        return {"status": "error", "error": "AGY extraction failed"}

    client_name = extracted.get("client_profile", {}).get("name", "") or docs_dir.name
    slug = slugify(client_name)

    if output_dir is None:
        output_dir = docs_dir / "output" / slug
    output_dir = Path(output_dir)
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    final = {**CLIENT_PROFILE_SCHEMA, **extracted}
    profile_path = output_dir / "client_profile.json"
    brief_path = output_dir / "content_brief.json"
    report_path = output_dir / "ingest_report.md"

    missing = []
    required = [
        ("client_profile.name", final.get("client_profile", {}).get("name")),
        ("client_profile.mission", final.get("client_profile", {}).get("mission")),
        ("content.classes", final.get("content", {}).get("classes")),
        ("automation.email_sequences", final.get("automation", {}).get("email_sequences")),
    ]
    for path, val in required:
        if not val or (isinstance(val, list) and len(val) == 0):
            missing.append(f"`{path}` is empty — follow up with client")

    if not dry_run:
        profile_path.write_text(json.dumps(final, indent=2), encoding="utf-8")
        brief_path.write_text(json.dumps(final.get("content", {}), indent=2), encoding="utf-8")
        write_ingest_report(report_path, docs, final, missing)

    return {
        "status": "ok" if not missing else "partial",
        "client_profile": final.get("client_profile", {}),
        "content_brief": final.get("content", {}),
        "report_path": str(report_path) if not dry_run else None,
        "paths": {
            "profile": str(profile_path) if not dry_run else None,
            "brief": str(brief_path) if not dry_run else None,
            "report": str(report_path) if not dry_run else None,
        },
        "missing_fields": missing,
        "docs_found": list(docs.keys()),
    }


# ── CLI ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PWP Ingest - parse 5 Website Dev docs into structured JSON")
    parser.add_argument("docs_dir", help="Path to the directory containing the 5 Website Dev docs")
    parser.add_argument("--out", default=None, help="Output directory (default: <docs_dir>/output/<slug>/)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write any output files")
    args = parser.parse_args()

    result = run_ingest(args.docs_dir, output_dir=args.out, dry_run=args.dry_run)

    if result["status"] == "error":
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(f"\nClient: {result['client_profile'].get('name')}")
    print(f"Slug: {result['paths'].get('profile', '').rsplit('/', 2)[-2] if result['paths'].get('profile') else 'N/A'}")
    print(f"Status: {result['status']}")
    if result.get("missing_fields"):
        print(f"\nMissing required fields:")
        for m in result["missing_fields"]:
            print(f"  - {m}")

    if result.get("paths", {}).get("profile"):
        print(f"\nWrote:")
        for k, v in result["paths"].items():
            if v:
                print(f"  {v}")

    if result["status"] == "partial":
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
