from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_VIEWPORTS: tuple[dict[str, int | str], ...] = (
    {"name": "mobile", "width": 390, "height": 844},
    {"name": "tablet", "width": 768, "height": 1024},
    {"name": "desktop", "width": 1440, "height": 900},
)

MODULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Viewport:
    name: str
    width: int
    height: int

    def as_dict(self) -> dict[str, int | str]:
        return {"name": self.name, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class FixtureCase:
    id: str
    kind: str
    theme_id: str
    module_id: str | None
    variant: str | None
    viewport: Viewport
    html_file: str
    screenshot_file: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["viewport"] = self.viewport.as_dict()
        return payload


@dataclass(frozen=True)
class HarnessPlan:
    theme_path: str
    theme_id: str
    cases: tuple[FixtureCase, ...]
    manifest_file: str
    spec_file: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "themePath": self.theme_path,
            "themeId": self.theme_id,
            "manifestFile": self.manifest_file,
            "specFile": self.spec_file,
            "cases": [case.as_dict() for case in self.cases],
        }


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return slug or "fixture"


def _normalize_viewports(
    viewports: Iterable[dict[str, Any]] | None = None,
) -> tuple[Viewport, ...]:
    normalized: list[Viewport] = []
    for raw in viewports or DEFAULT_VIEWPORTS:
        name = raw.get("name")
        width = raw.get("width")
        height = raw.get("height")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("viewport name must be a non-empty string")
        if not isinstance(width, int) or width <= 0:
            raise ValueError(f"viewport {name} width must be a positive integer")
        if not isinstance(height, int) or height <= 0:
            raise ValueError(f"viewport {name} height must be a positive integer")
        normalized.append(Viewport(name=name, width=width, height=height))
    return tuple(normalized)


def _read_module_contracts(
    theme_root: Path, module_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    contracts: dict[str, dict[str, Any]] = {}
    for module_id in module_ids:
        if not isinstance(module_id, str) or not MODULE_ID_RE.match(module_id):
            raise ValueError(f"invalid module id in theme manifest: {module_id!r}")
        module_path = theme_root / "modules" / f"{module_id}.json"
        if not module_path.exists():
            raise FileNotFoundError(f"missing module contract: {module_path}")
        contract = _load_json(module_path)
        if contract.get("id") != module_id:
            raise ValueError(f"module contract id mismatch in {module_path}")
        contracts[module_id] = contract
    return contracts


def build_harness_plan(
    theme_path: str | Path,
    output_dir: str | Path,
    viewports: Iterable[dict[str, Any]] | None = None,
) -> HarnessPlan:
    """Build the deterministic render matrix for a PWP theme package.

    The plan intentionally contains one full-page fixture per viewport plus one
    module/variant fixture for every declared module contract and viewport.  A
    later Playwright pass can consume the same manifest for screenshots, a11y, or
    visual-diff baselines without re-discovering theme contracts.
    """

    theme_root = Path(theme_path).resolve()
    out = Path(output_dir).resolve()
    manifest = _load_json(theme_root / "theme.json")
    theme_id = manifest.get("id")
    if not isinstance(theme_id, str) or not theme_id:
        raise ValueError("theme.json must declare a non-empty id")

    module_ids = manifest.get("modules")
    if not isinstance(module_ids, list) or not module_ids:
        raise ValueError("theme.json modules must be a non-empty array")
    contracts = _read_module_contracts(theme_root, module_ids)
    normalized_viewports = _normalize_viewports(viewports)

    cases: list[FixtureCase] = []
    theme_slug = _slug(theme_id)
    for viewport in normalized_viewports:
        case_id = f"{theme_slug}__page__default__{viewport.name}"
        cases.append(
            FixtureCase(
                id=case_id,
                kind="page",
                theme_id=theme_id,
                module_id=None,
                variant=None,
                viewport=viewport,
                html_file=f"fixtures/{case_id}.html",
                screenshot_file=f"screenshots/{case_id}.png",
            )
        )

    for module_id in sorted(contracts):
        variants = contracts[module_id].get("variants") or ["default"]
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"module {module_id} variants must be a non-empty array")
        for variant in sorted(str(item) for item in variants):
            variant_slug = _slug(variant)
            for viewport in normalized_viewports:
                case_id = f"{theme_slug}__module__{module_id}__{variant_slug}__{viewport.name}"
                cases.append(
                    FixtureCase(
                        id=case_id,
                        kind="module",
                        theme_id=theme_id,
                        module_id=module_id,
                        variant=variant,
                        viewport=viewport,
                        html_file=f"fixtures/{case_id}.html",
                        screenshot_file=f"screenshots/{case_id}.png",
                    )
                )

    return HarnessPlan(
        theme_path=str(theme_root),
        theme_id=theme_id,
        cases=tuple(cases),
        manifest_file=str(out / "pwp-playwright-fixtures.json"),
        spec_file=str(out / "pwp-theme-fixtures.spec.js"),
    )


def _fixture_body(case: FixtureCase) -> str:
    if case.kind == "page":
        return """
    <header class="pwp-site-header"><a href="#main">Skip to main content</a></header>
    <main id="main">
      <section class="pwp-module pwp-module-hero" data-module="hero" data-variant="default">
        <p class="eyebrow">Fixture page</p>
        <h1>PWP theme fixture page</h1>
        <p>This deterministic page exercises the theme shell across the viewport matrix.</p>
      </section>
      <section class="pwp-module pwp-module-lead-capture" data-module="lead-capture" data-variant="form">
        <h2>Lead capture</h2>
        <form aria-label="Fixture lead capture"><label>Email <input type="email" /></label></form>
      </section>
    </main>
""".strip()
    title = f"{case.module_id} / {case.variant}"
    return f"""
    <main id="main">
      <section class="pwp-module pwp-module-{case.module_id}" data-module="{case.module_id}" data-variant="{case.variant}">
        <p class="eyebrow">Module fixture</p>
        <h1>{title}</h1>
        <p>Deterministic Playwright fixture for {case.module_id} variant {case.variant}.</p>
        <a class="button" href="#fixture-action">Fixture action</a>
      </section>
    </main>
""".strip()


def _render_fixture_html(case: FixtureCase) -> str:
    return f"""<!doctype html>
<html lang="en" data-theme-id="{case.theme_id}" data-fixture-id="{case.id}">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{case.id}</title>
  <style>
    :root {{ color-scheme: light; --pwp-color-background-page: #f8fafc; --pwp-color-text-body: #0f172a; --pwp-color-accent-primary: #2563eb; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; background: var(--pwp-color-background-page); color: var(--pwp-color-text-body); }}
    main {{ min-height: 100vh; display: grid; gap: 1rem; align-content: center; padding: clamp(1rem, 4vw, 4rem); box-sizing: border-box; }}
    .pwp-module {{ border: 1px solid rgba(15, 23, 42, 0.15); border-radius: 1rem; padding: clamp(1rem, 3vw, 3rem); background: white; box-shadow: 0 1rem 2.5rem rgba(15, 23, 42, 0.08); }}
    .eyebrow {{ margin: 0 0 .5rem; text-transform: uppercase; letter-spacing: .08em; color: var(--pwp-color-accent-primary); font-size: .8rem; }}
    h1 {{ margin: 0 0 1rem; max-width: 14ch; font-size: clamp(2rem, 8vw, 5rem); line-height: .95; }}
    p {{ max-width: 62ch; line-height: 1.6; }}
    .button {{ display: inline-block; margin-top: 1rem; padding: .8rem 1rem; border-radius: .75rem; background: var(--pwp-color-accent-primary); color: white; text-decoration: none; }}
  </style>
</head>
<body>
{_fixture_body(case)}
</body>
</html>
"""


def _playwright_spec() -> str:
    return r"""const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const manifestPath = process.env.PWP_FIXTURE_MANIFEST || path.join(__dirname, 'pwp-playwright-fixtures.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const root = path.dirname(manifestPath);

async function run() {
  const browser = await chromium.launch();
  const results = [];
  try {
    for (const fixture of manifest.cases) {
      const page = await browser.newPage({ viewport: { width: fixture.viewport.width, height: fixture.viewport.height } });
      const target = path.join(root, fixture.html_file);
      await page.goto(`file://${target}`);
      const fixtureId = await page.locator('[data-fixture-id]').getAttribute('data-fixture-id');
      if (fixtureId !== fixture.id) {
        throw new Error(`fixture id mismatch for ${fixture.id}: ${fixtureId}`);
      }
      if (fixture.kind === 'module') {
        const selector = `[data-module="${fixture.module_id}"][data-variant="${fixture.variant}"]`;
        const count = await page.locator(selector).count();
        if (count < 1) {
          throw new Error(`missing module fixture selector ${selector}`);
        }
      }
      const screenshotPath = path.join(root, fixture.screenshot_file);
      fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
      await page.screenshot({ path: screenshotPath, fullPage: true });
      await page.close();
      results.push({ id: fixture.id, screenshot: screenshotPath, ok: true });
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ ok: true, cases: results.length, results }, null, 2));
}

run().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def write_harness_files(plan: HarnessPlan, clean: bool = True) -> HarnessPlan:
    out = Path(plan.manifest_file).parent
    if clean and out.exists():
        shutil.rmtree(out)
    (out / "fixtures").mkdir(parents=True, exist_ok=True)
    (out / "screenshots").mkdir(parents=True, exist_ok=True)
    for case in plan.cases:
        (out / case.html_file).write_text(_render_fixture_html(case), encoding="utf-8")
    Path(plan.manifest_file).write_text(
        json.dumps(plan.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    Path(plan.spec_file).write_text(_playwright_spec(), encoding="utf-8")
    return plan


def run_playwright(plan: HarnessPlan) -> subprocess.CompletedProcess[str]:
    spec_path = Path(plan.spec_file)
    env = os.environ.copy()
    env["PWP_FIXTURE_MANIFEST"] = plan.manifest_file
    return subprocess.run(
        ["node", str(spec_path)],
        cwd=spec_path.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a Playwright fixture matrix for a PWP theme package."
    )
    parser.add_argument("theme_path", help="Theme package containing theme.json")
    parser.add_argument(
        "--out",
        default=".pwp-playwright-fixtures",
        help="Directory for generated HTML fixtures, manifest, spec, and screenshots",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the fixture manifest JSON"
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run the generated Playwright node harness after writing fixtures",
    )
    args = parser.parse_args(argv)

    plan = build_harness_plan(args.theme_path, args.out)
    write_harness_files(plan)
    if args.run:
        completed = run_playwright(plan)
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="")
        if completed.returncode != 0:
            return completed.returncode
    if args.json:
        print(json.dumps(plan.as_dict(), indent=2))
    else:
        print(
            f"Generated {len(plan.cases)} PWP Playwright fixture cases for {plan.theme_id}: {plan.manifest_file}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
