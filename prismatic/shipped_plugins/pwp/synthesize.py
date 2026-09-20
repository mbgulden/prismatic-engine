"""
PWP Synthesize - turns the client_profile.json into a website_build_plan.md via AGY.

Library API:
    from prismatic_web_publisher.synthesize import run_synthesize
    result = run_synthesize(profile_path, output_dir=None, skip_agy=False, dry_run=False)

CLI:
    python -m prismatic_web_publisher.synthesize <profile.json> [--out DIR] [--no-agy] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SYNTHESIS_PROMPT_TEMPLATE = """You are a website architect creating a comprehensive build plan.

Given the client profile below, produce a detailed `website_build_plan.md` that follows this exact structure:

1. **Site Architecture** — full page list with URL patterns, navigation structure, SEO meta strategies, redirect rules
2. **Per-Page Content Briefs** — for each page: page type, purpose, content sections, layout notes, CTAs, conversion considerations
3. **Design System Specifications** — colors (with hex codes), typography (font families + sizes), spacing system, component library, brand mood, reference sites
4. **Asset Plan** — logo requirements, hero images, instructor portraits, icons, source strategy (stock vs. AI generation vs. custom)
5. **Technical Requirements** — recommended platform (Astro/Next/WordPress), hosting, analytics, form handling, conversion tracking
6. **Automation Workflows** — email sequences, post-purchase flows, lead magnet delivery
7. **Success Metrics** — KPIs for launch (visits, conversions, bookings, lead captures)

Make it ACTIONABLE — a developer should be able to read this and start building without further questions.
Be specific with numbers, hex codes, and concrete recommendations.
Aim for 2500-4000 words.

CLIENT PROFILE:
{client_profile}
"""


def slugify(s: str) -> str:
    """Convert a string to a URL-safe slug."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _call_agy(prompt: str, model: str = "Gemini 3.1 Pro (High)", timeout: int = 300) -> str:
    """Call AGY with the given prompt, return the response text."""
    result = subprocess.run(
        [
            "/home/ubuntu/.local/bin/agy",
            "--model", model,
            "--prompt", prompt,
            "--print-timeout", f"{timeout}s",
            "--dangerously-skip-permissions",
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 30,
    )

    if result.returncode != 0:
        raise RuntimeError(f"AGY error (exit {result.returncode}): {result.stderr[:300]}")

    output = result.stdout.strip()
    if not output:
        raise RuntimeError("AGY returned empty output")

    # Strip any leading/trailing markdown code fences
    output = re.sub(r"^```markdown\s*\n", "", output, flags=re.IGNORECASE)
    output = re.sub(r"^```\s*\n", "", output)
    output = re.sub(r"\n```\s*$", "", output)

    return output


def synthesize_stub(profile: dict) -> str:
    """Generate a minimal build plan without calling AGY (for testing)."""
    name = profile.get("client_profile", {}).get("name", "Client")
    return f"""# {name}: Website Build Plan (stub - no AGY)

## 1. Site Architecture
(Stub - run without --no-agy for full build plan)

## 2. Per-Page Content Briefs
(Stub)

## 3. Design System Specifications
(Stub)

## 4. Asset Plan
(Stub)

## 5. Technical Requirements
(Stub)

## 6. Automation Workflows
(Stub)

## 7. Success Metrics
(Stub)
"""


# ── Library API ──────────────────────────────────────────────

def run_synthesize(
    profile_path: str | Path,
    output_dir: str | Path | None = None,
    skip_agy: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Library API: synthesize a build plan from a client profile.

    Args:
        profile_path: Path to the client_profile.json
        output_dir: Where to write website_build_plan.md. Defaults to same dir as profile.
        skip_agy: If True, use a stub (no AGY call). Useful for testing.
        dry_run: If True, don't write the output file

    Returns:
        dict with keys:
            - build_plan: str (the markdown content)
            - path: str (path to the written file, or None if dry_run)
            - word_count: int
            - status: "ok" | "error"
            - error: str (if status == "error")
    """
    profile_path = Path(profile_path).resolve()
    if not profile_path.is_file():
        return {"status": "error", "error": f"{profile_path} is not a file"}

    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"Invalid JSON in {profile_path}: {e}"}

    if skip_agy:
        build_plan = synthesize_stub(profile)
    else:
        try:
            prompt = SYNTHESIS_PROMPT_TEMPLATE.replace(
                "{client_profile}", json.dumps(profile, indent=2)
            )
            build_plan = _call_agy(prompt)
        except Exception as e:
            return {"status": "error", "error": str(e)}

    word_count = len(build_plan.split())

    client_name = profile.get("client_profile", {}).get("name", "") or profile_path.parent.name
    if output_dir is None:
        output_dir = profile_path.parent
    output_dir = Path(output_dir)
    build_plan_path = output_dir / "website_build_plan.md"

    if not dry_run:
        build_plan_path.write_text(build_plan, encoding="utf-8")

    return {
        "status": "ok",
        "build_plan": build_plan,
        "path": str(build_plan_path) if not dry_run else None,
        "word_count": word_count,
        "client_name": client_name,
    }


# ── CLI ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PWP Synthesize - generate website_build_plan.md from client profile")
    parser.add_argument("profile_path", help="Path to client_profile.json")
    parser.add_argument("--out", default=None, help="Output directory (default: same as profile)")
    parser.add_argument("--no-agy", action="store_true", help="Skip AGY call (use stub for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write the output file")
    args = parser.parse_args()

    result = run_synthesize(
        args.profile_path, output_dir=args.out, skip_agy=args.no_agy, dry_run=args.dry_run
    )

    if result["status"] == "error":
        print(f"Error: {result.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(f"\nClient: {result['client_name']}")
    print(f"Word count: {result['word_count']} (target: >= 2000)")
    if result["word_count"] < 2000:
        print("WARNING: build plan is short — may need higher quality model or larger context")
    if result["path"]:
        print(f"\nWrote: {result['path']}")

    sys.exit(0)


if __name__ == "__main__":
    main()
