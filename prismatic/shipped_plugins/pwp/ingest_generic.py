#!/usr/bin/env python3
"""
pwp_ingest_generic.py — Generic version of Step 1 that handles any project's OKF docs.

Instead of requiring the 5 "Website Dev" framework docs, this version:
- Accepts any directory of markdown docs
- Auto-detects the doc types (business profile, content, design, technical, automation, etc.)
- Produces a generic structured profile

This is the "forward compatibility" version — any project type, any docs.

Usage:
    python3 pwp_ingest_generic.py <path-to-project-okf-docs-dir>
"""
import os, sys, json, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

GENERIC_SCHEMA = {
    "project": {
        "name": "",
        "tagline": "",
        "description": "",
        "mission": "",
        "type": "",  # "website" / "game" / "bot" / "saas" / "knowledge-base" / "infrastructure" / etc.
        "status": "",
    },
    "content": {
        "primary_sections": [],   # list of {title, slug, description}
        "assets": [],             # list of {name, type, source}
        "testimonials_or_quotes": [],
    },
    "design": {
        "color_palette": [],
        "typography": "",
        "mood": "",
        "reference_sites": [],
    },
    "technical": {
        "stack": [],
        "deployment": "",
        "github_repo": "",
        "live_url": "",
    },
    "automation": {
        "workflows": [],
    },
    "okf_docs": [],  # list of {path, title, type, summary}
    "research": {
        "research_dirs": [],
        "audit_dirs": [],
        "decision_dirs": [],
    },
    "metadata": {
        "okf_root": "",
        "ingest_date": "",
        "doc_count": 0,
    },
}

def detect_doc_type(path: Path) -> str:
    """Classify a doc by its path and filename."""
    name = path.name.lower()
    parent = path.parent.name.lower()
    if "research" in parent or "research" in name:
        return "research"
    if "audit" in parent or "audit" in name:
        return "audit"
    if "decision" in parent or "decision" in name:
        return "decision"
    if "integration" in parent or "integration" in name:
        return "integration"
    if "inventory" in parent or "inventory" in name:
        return "inventory"
    if "report" in parent or "report" in name:
        return "report"
    if "storyline" in parent or "story" in name:
        return "storyline"
    if "index" in name:
        return "index"
    return "other"

def collect_okf_docs(okf_dir: Path) -> dict:
    """Walk the OKF dir and collect all .md files with metadata."""
    docs = []
    sections = []
    if not okf_dir.is_dir():
        return {"docs": docs, "sections": sections, "research": [], "audits": [], "decisions": [], "integrations": [], "inventory": []}

    for path in sorted(okf_dir.rglob("*.md")):
        rel = path.relative_to(okf_dir)
        doc_type = detect_doc_type(path)
        # Read first non-frontmatter line as title
        title = path.stem
        try:
            text = path.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    text = text[end + 3:].strip()
            # First heading
            for line in text.split("\n")[:30]:
                m = re.match(r"^#\s+(.+)", line)
                if m:
                    title = m.group(1).strip()
                    break
        except Exception:
            pass

        docs.append({
            "path": str(path),
            "relative": str(rel),
            "title": title,
            "type": doc_type,
            "parent_section": path.parent.name,
        })

        # Track section structure
        if doc_type != "other" and doc_type not in [d["type"] for d in docs if d["path"] != str(path)]:
            sections.append({"name": path.parent.name, "type": doc_type, "doc_count": 0})

        # Increment doc count for the section
        for s in sections:
            if s["name"] == path.parent.name:
                s["doc_count"] += 1

    return {
        "docs": docs,
        "sections": sections,
        "research": [d for d in docs if d["type"] == "research"],
        "audits": [d for d in docs if d["type"] == "audit"],
        "decisions": [d for d in docs if d["type"] == "decision"],
        "integrations": [d for d in docs if d["type"] == "integration"],
        "inventory": [d for d in docs if d["type"] == "inventory"],
    }

def extract_metadata(okf_dir: Path) -> dict:
    """Extract project name, type, github repo from the OKF index.md."""
    meta = {
        "name": okf_dir.parent.name,
        "type": "unknown",
        "github_repo": "",
        "description": "",
    }

    # Try index.md
    index_path = okf_dir / "index.md"
    if index_path.is_file():
        text = index_path.read_text(encoding="utf-8")
        # Get the title
        m = re.search(r"^#\s+(.+)", text, re.MULTILINE)
        if m:
            meta["name"] = m.group(1).strip()
        # Get the first paragraph
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.strip() and not line.startswith("#") and not line.startswith("---"):
                meta["description"] = line.strip()[:200]
                break
        # Get github repo
        m = re.search(r"\[`?mbgulden/([\w\-.]+)`?\]\(https://github\.com/mbgulden/([\w\-.]+)\)", text)
        if m:
            meta["github_repo"] = f"mbgulden/{m.group(2)}"
        # Detect project type from text
        text_lower = text.lower()
        if "website" in text_lower or "site" in text_lower or "tour" in text_lower:
            meta["type"] = "website"
        elif "game" in text_lower or "darius" in text_lower:
            meta["type"] = "game"
        elif "bot" in text_lower or "telegram" in text_lower:
            meta["type"] = "bot"
        elif "saas" in text_lower or "platform" in text_lower:
            meta["type"] = "saas"
        elif "knowledge" in text_lower or "okf" in text_lower:
            meta["type"] = "knowledge-base"
        elif "homelab" in text_lower or "infrastructure" in text_lower or "pve" in text_lower or "proxmox" in text_lower:
            meta["type"] = "infrastructure"

    # Try git remote
    try:
        result = subprocess.run(
            ["git", "-C", str(okf_dir.parent), "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            m = re.search(r"github\.com[/:]([\w\-.]+)/([\w\-.]+?)(?:\.git)?$", url)
            if m:
                meta["github_repo"] = f"{m.group(1)}/{m.group(2)}"
    except Exception:
        pass

    return meta

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("okf_dir", help="Path to OKF/docs dir")
    parser.add_argument("--no-agy", action="store_true", help="Skip AGY extraction (faster, no mission/sections)")
    args = parser.parse_args()

    okf_dir = Path(args.okf_dir).resolve()
    if not okf_dir.is_dir():
        # Try common alternatives (docs/, knowledge/, etc.)
        parent = okf_dir.parent
        candidates = ["docs", "knowledge", "documentation"]
        for c in candidates:
            alt = parent / c
            if alt.is_dir():
                print(f"'{okf_dir.name}' not a dir, but found '{c}/' at {alt} — using that")
                okf_dir = alt
                break
        if not okf_dir.is_dir():
            print(f"Error: {okf_dir} is not a directory and no docs/ knowledge/ documentation/ alt found", file=sys.stderr)
            sys.exit(1)

    project_name = okf_dir.parent.name
    print(f"Generic ingest from: {okf_dir}")
    print(f"Project: {project_name}")

    # Extract metadata from index.md + git
    meta = extract_metadata(okf_dir)
    print(f"  Detected type: {meta['type']}")
    print(f"  Detected GitHub: {meta['github_repo']}")

    # Collect all OKF docs
    collected = collect_okf_docs(okf_dir)
    print(f"  Total docs: {len(collected['docs'])}")
    print(f"  Sections: {len(collected['sections'])}")
    print(f"  Research: {len(collected['research'])}, Audits: {len(collected['audits'])}, Decisions: {len(collected['decisions'])}")

    # Build the structured output
    output = {
        **GENERIC_SCHEMA,
        "project": {
            "name": meta["name"],
            "tagline": meta["description"],
            "description": meta["description"],
            "mission": "",
            "type": meta["type"],
            "status": "active",
        },
        "okf_docs": collected["docs"],
        "primary_sections": collected["sections"],
        "research": {
            "research_dirs": [d["parent_section"] for d in collected["research"] if "research" in d["parent_section"].lower()],
            "audit_dirs": [d["parent_section"] for d in collected["audits"] if "audit" in d["parent_section"].lower()],
            "decision_dirs": [d["parent_section"] for d in collected["decisions"] if "decision" in d["parent_section"].lower()],
        },
        "technical": {
            "stack": [],
            "deployment": "",
            "github_repo": meta["github_repo"],
            "live_url": "",
        },
        "metadata": {
            "okf_root": str(okf_dir),
            "ingest_date": datetime.now(timezone.utc).isoformat(),
            "doc_count": len(collected["docs"]),
        },
    }

    # Use AGY to extract more structured info
    if args.no_agy:
        print("\nSkipping AGY extraction (--no-agy)")
    else:
        print("\nExtracting structured data with AGY...")
    prompt_parts = [
            "You are a project documentation analyst.",
        "Read the following project OKF docs and produce a JSON profile.",
        "Output ONLY valid JSON, no commentary.",
        "",
        f"Project name: {meta['name']}",
        f"Project type: {meta['type']}",
        f"Total OKF docs: {len(collected['docs'])}",
        "",
        "Produce JSON with this schema:",
        json.dumps({
            "mission": "1-2 sentence project mission",
            "primary_sections": [{"title": "section title", "slug": "kebab-case", "description": "what's in this section"}],
            "design": {"color_palette": ["hex1"], "typography": "font names", "mood": "3-5 adjectives", "reference_sites": ["url"]},
            "technical": {"stack": ["tech1", "tech2"], "deployment": "where deployed", "live_url": "url or empty"},
            "automation": {"workflows": [{"name": "workflow name", "trigger": "...", "description": "..."}]},
        }, indent=2),
        "",
        "OKF DOCS (truncated to fit context):",
        "",
    ]

    # Add a few representative docs
    for d in collected["docs"][:5]:
        try:
            text = Path(d["path"]).read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    text = text[end + 3:].strip()
            prompt_parts.append(f"=== {d['title']} ===")
            prompt_parts.append(text[:3000])
            prompt_parts.append("")
        except Exception:
            pass

    if not args.no_agy:
        prompt_parts.append("Output the JSON now (only JSON, nothing else):")
        prompt = "\n".join(prompt_parts)

        try:
            result = subprocess.run(
                [
                    "/home/ubuntu/.local/bin/agy",
                    "--model", "Gemini 3.5 Flash (High)",
                    "--prompt", prompt,
                    "--print-timeout", "2m0s",
                    "--dangerously-skip-permissions",
                ],
                capture_output=True, text=True, timeout=180
            )
            if result.returncode == 0:
                output_text = result.stdout.strip()
                json_match = re.search(r"\{[\s\S]*\}", output_text)
                if json_match:
                    try:
                        extracted = json.loads(json_match.group(0))
                        # Merge into output
                        if extracted.get("mission"):
                            output["project"]["mission"] = extracted["mission"]
                        if extracted.get("primary_sections"):
                            output["content"]["primary_sections"] = extracted["primary_sections"]
                        if extracted.get("design"):
                            output["design"] = {**output["design"], **extracted["design"]}
                        if extracted.get("technical"):
                            output["technical"] = {**output["technical"], **extracted["technical"]}
                        if extracted.get("automation"):
                            output["automation"] = extracted["automation"]
                        print("  ✓ AGY extraction succeeded")
                    except json.JSONDecodeError:
                        print("  ⚠ JSON parse failed, using schema defaults")
        except Exception as e:
            print(f"  ⚠ AGY call failed: {e}")

    # Write output
    out_dir = okf_dir / "output"
    out_dir.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9-]", "", meta["name"].lower().replace(" ", "-"))
    profile_path = out_dir / f"{slug}-profile.json"

    profile_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n✓ Wrote: {profile_path}")
    print(f"\nProfile summary:")
    print(f"  Name: {output['project']['name']}")
    print(f"  Type: {output['project']['type']}")
    print(f"  Mission: {output['project']['mission'][:120]}")
    print(f"  Sections: {len(output['content']['primary_sections'])}")
    print(f"  OKF docs cataloged: {output['metadata']['doc_count']}")
    print(f"  GitHub: {output['technical']['github_repo']}")

if __name__ == "__main__":
    main()