"""Ported from stale PR #250 (plugins/pwp/tests/test_site_crawler.py).

Fresh port onto current main: imports rewritten to the real package path
``prismatic.shipped_plugins.pwp`` (post-#376 convention); repo-root discovery
follows the current pwp-tests convention (pyproject.toml walk).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = next(
    (p for p in _THIS_DIR.parents if (p / "pyproject.toml").exists()),
    _THIS_DIR.parents[2],
)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from prismatic.shipped_plugins.pwp.site_crawler import crawl_site  # noqa: E402


def _write_page(
    root: Path,
    route: str,
    *,
    title: str,
    canonical: str,
    body: str = "",
    json_ld: str | None = None,
) -> None:
    path = (
        root / route.strip("/") / "index.html" if route != "/" else root / "index.html"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = ""
    if json_ld is not None:
        schema = f'<script type="application/ld+json">{json_ld}</script>'
    path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                "<html><head>",
                f"<title>{title}</title>",
                f'<link rel="canonical" href="{canonical}">',
                schema,
                "</head><body>",
                body,
                "</body></html>",
            ]
        ),
        encoding="utf-8",
    )


def _write_sitemap(root: Path, locs: list[str]) -> None:
    root.joinpath("sitemap.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>\n"
        + "\n".join(f"  <url><loc>{loc}</loc></url>" for loc in locs)
        + "\n</urlset>\n",
        encoding="utf-8",
    )


def test_site_crawler_accepts_valid_generated_site(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    site_url = "https://example.test"
    json_ld = json.dumps(
        {"@context": "https://schema.org", "@type": "LocalBusiness", "name": "Example"}
    )
    _write_page(
        root,
        "/",
        title="Home | Example",
        canonical=f"{site_url}/",
        body='<main id="top"><a href="/about/#team">About</a><a href="/style.css">CSS</a></main>',
        json_ld=json_ld,
    )
    _write_page(
        root,
        "/about/",
        title="About | Example",
        canonical=f"{site_url}/about/",
        body='<section id="team"><a href="/">Home</a></section>',
        json_ld=json_ld,
    )
    (root / "style.css").write_text("body{}", encoding="utf-8")
    _write_sitemap(root, [f"{site_url}/", f"{site_url}/about/"])

    result = crawl_site(root, site_url=site_url)

    assert result.ok, result.errors
    assert sorted(page.route for page in result.pages) == ["/", "/about/"]


def test_site_crawler_reports_broken_links_duplicate_titles_bad_canonical_sitemap_and_schema(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    site_url = "https://example.test"
    _write_page(
        root,
        "/",
        title="Duplicate",
        canonical="/relative",
        body='<a href="/missing/">Missing</a><a href="/about/#ghost">Bad anchor</a>',
        json_ld='{"@context":"https://schema.org",',
    )
    _write_page(
        root,
        "/about/",
        title="Duplicate",
        canonical=f"{site_url}/wrong/",
        body='<section id="team"></section>',
        json_ld='{"@context":"https://schema.org","name":"No type"}',
    )
    _write_sitemap(root, [f"{site_url}/", "https://other.test/about/"])

    result = crawl_site(root, site_url=site_url)

    assert not result.ok
    joined = "\n".join(result.errors)
    assert "canonical URL is not absolute" in joined
    assert "canonical URL https://example.test/wrong/ does not match expected" in joined
    assert "broken link target /missing/" in joined
    assert "broken anchor /about/#ghost" in joined
    assert "Duplicate title 'Duplicate'" in joined
    assert "JSON-LD block 1 is invalid JSON" in joined
    assert "JSON-LD block 1 missing @context/@type" in joined
    assert "sitemap.xml loc host does not match site URL" in joined
    assert "sitemap.xml missing page route: /about/" in joined


def test_repo_local_pwp_site_crawl_command_emits_json(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    site_url = "https://example.test"
    _write_page(root, "/", title="Home", canonical=f"{site_url}/")
    _write_sitemap(root, [f"{site_url}/"])

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/pwp",
            "site",
            "crawl",
            str(root),
            "--site-url",
            site_url,
            "--json",
        ],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["pages"] == ["/"]
