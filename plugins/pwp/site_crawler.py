from __future__ import annotations

import argparse
import json
import posixpath
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

_SKIP_SCHEMES = {"mailto", "tel", "sms", "javascript", "data"}


@dataclass
class PageRecord:
    path: Path
    route: str
    title: str | None = None
    canonical: str | None = None
    links: list[str] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)
    json_ld: list[str] = field(default_factory=list)


@dataclass
class SiteCrawlResult:
    site_root: Path
    site_url: str | None
    pages: list[PageRecord]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "siteRoot": str(self.site_root),
            "siteUrl": self.site_url,
            "pages": [page.route for page in self.pages],
            "errors": self.errors,
            "warnings": self.warnings,
        }


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.canonical: str | None = None
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.json_ld: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value for key, value in attrs if value is not None}
        if "id" in attr:
            self.ids.add(attr["id"])
        if tag.lower() == "title":
            self._in_title = True
            self._title_parts = []
        elif tag.lower() == "a" and attr.get("href"):
            self.links.append(attr["href"])
        elif tag.lower() == "link" and attr.get("rel", "").lower() == "canonical":
            self.canonical = attr.get("href")
        elif tag.lower() == "script" and attr.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title" and self._in_title:
            title = "".join(self._title_parts).strip()
            self.title = title or None
            self._in_title = False
        elif tag.lower() == "script" and self._in_json_ld:
            self.json_ld.append("".join(self._json_ld_parts).strip())
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_json_ld:
            self._json_ld_parts.append(data)


def _route_for_html(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


def _expected_url(site_url: str, route: str) -> str:
    return site_url.rstrip("/") + (route if route.startswith("/") else f"/{route}")


def _load_pages(root: Path) -> tuple[list[PageRecord], dict[str, PageRecord]]:
    pages: list[PageRecord] = []
    by_route: dict[str, PageRecord] = {}
    for html_path in sorted(root.rglob("*.html")):
        parser = _PageParser()
        parser.feed(html_path.read_text(encoding="utf-8"))
        page = PageRecord(
            path=html_path,
            route=_route_for_html(root, html_path),
            title=parser.title,
            canonical=parser.canonical,
            links=parser.links,
            ids=parser.ids,
            json_ld=parser.json_ld,
        )
        pages.append(page)
        by_route[page.route] = page
    return pages, by_route


def _link_to_route(current_route: str, href: str, site_url: str | None) -> tuple[str | None, str | None]:
    parsed = urlparse(href)
    if parsed.scheme in _SKIP_SCHEMES:
        return None, None
    if parsed.scheme or parsed.netloc:
        if not site_url:
            return None, None
        base = urlparse(site_url)
        if parsed.netloc and parsed.netloc != base.netloc:
            return None, None
        path = parsed.path or "/"
        fragment = parsed.fragment or None
    else:
        joined = urlparse(urljoin(current_route, href))
        path = joined.path or "/"
        fragment = joined.fragment or None
    path = unquote(path)
    normalized = posixpath.normpath(path)
    if path.endswith("/") and not normalized.endswith("/"):
        normalized += "/"
    if normalized == ".":
        normalized = "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized, fragment


def _route_to_file(root: Path, route: str) -> Path:
    clean = route.lstrip("/")
    if not clean or route.endswith("/"):
        return root / clean / "index.html"
    candidate = root / clean
    if candidate.suffix:
        return candidate
    return candidate / "index.html"


def _has_jsonld_context_and_type(value: Any) -> bool:
    if isinstance(value, dict):
        if "@context" in value and "@type" in value:
            return True
        return any(_has_jsonld_context_and_type(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_jsonld_context_and_type(child) for child in value)
    return False


def _validate_json_ld(page: PageRecord, result: SiteCrawlResult) -> None:
    for index, raw in enumerate(page.json_ld, start=1):
        if not raw:
            result.errors.append(f"{page.route}: JSON-LD block {index} is empty")
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            result.errors.append(f"{page.route}: JSON-LD block {index} is invalid JSON: {exc.msg}")
            continue
        if not _has_jsonld_context_and_type(payload):
            result.errors.append(f"{page.route}: JSON-LD block {index} missing @context/@type")


def _validate_sitemap(root: Path, site_url: str | None, pages: list[PageRecord], result: SiteCrawlResult) -> None:
    sitemap = root / "sitemap.xml"
    if not sitemap.exists():
        result.errors.append("Missing sitemap.xml")
        return
    try:
        tree = ET.parse(sitemap)
    except ET.ParseError as exc:
        result.errors.append(f"sitemap.xml is invalid XML: {exc}")
        return
    locs = [node.text.strip() for node in tree.iter() if node.tag.endswith("loc") and node.text and node.text.strip()]
    if not locs:
        result.errors.append("sitemap.xml has no <loc> entries")
        return
    loc_routes: set[str] = set()
    for loc in locs:
        parsed = urlparse(loc)
        if not parsed.scheme or not parsed.netloc:
            result.errors.append(f"sitemap.xml loc is not absolute: {loc}")
            continue
        if site_url:
            base = urlparse(site_url)
            if parsed.netloc != base.netloc:
                result.errors.append(f"sitemap.xml loc host does not match site URL: {loc}")
        route = parsed.path or "/"
        if not route.endswith("/") and not Path(route).suffix:
            route += "/"
        loc_routes.add(route)
        target = _route_to_file(root, route)
        if not target.exists():
            result.errors.append(f"sitemap.xml loc target missing: {loc}")
    page_routes = {page.route for page in pages}
    for route in sorted(page_routes - loc_routes):
        result.errors.append(f"sitemap.xml missing page route: {route}")


def crawl_site(site_root: str | Path, *, site_url: str | None = None) -> SiteCrawlResult:
    root = Path(site_root).resolve()
    normalized_site_url = site_url.rstrip("/") if site_url else None
    result = SiteCrawlResult(site_root=root, site_url=normalized_site_url, pages=[])
    if not root.exists():
        result.errors.append(f"Site root does not exist: {root}")
        return result
    if not root.is_dir():
        result.errors.append(f"Site root is not a directory: {root}")
        return result
    pages, by_route = _load_pages(root)
    result.pages = pages
    if not pages:
        result.errors.append("No HTML pages found")
        return result

    titles: dict[str, list[str]] = {}
    for page in pages:
        if not page.title:
            result.errors.append(f"{page.route}: missing <title>")
        else:
            titles.setdefault(page.title, []).append(page.route)
        if not page.canonical:
            result.errors.append(f"{page.route}: missing canonical URL")
        else:
            parsed = urlparse(page.canonical)
            if not parsed.scheme or not parsed.netloc:
                result.errors.append(f"{page.route}: canonical URL is not absolute: {page.canonical}")
            if normalized_site_url:
                expected = _expected_url(normalized_site_url, page.route)
                if page.canonical.rstrip("/") != expected.rstrip("/"):
                    result.errors.append(
                        f"{page.route}: canonical URL {page.canonical} does not match expected {expected}"
                    )
        _validate_json_ld(page, result)
        for href in page.links:
            target_route, fragment = _link_to_route(page.route, href, normalized_site_url)
            if target_route is None:
                continue
            target_file = _route_to_file(root, target_route)
            if not target_file.exists():
                result.errors.append(f"{page.route}: broken link target {href}")
                continue
            target_page = by_route.get(_route_for_html(root, target_file)) if target_file.suffix == ".html" else None
            if fragment and target_page and fragment not in target_page.ids:
                result.errors.append(f"{page.route}: broken anchor {href}")
    for title, routes in sorted(titles.items()):
        if len(routes) > 1:
            result.errors.append(f"Duplicate title '{title}' on routes: {', '.join(routes)}")
    _validate_sitemap(root, normalized_site_url, pages, result)
    return result


def format_result(result: SiteCrawlResult) -> str:
    lines = [f"PWP site crawl: {result.site_root}"]
    lines.append("OK" if result.ok else "FAILED")
    lines.append(f"Pages: {len(result.pages)}")
    if result.errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in result.errors)
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Crawl a generated PWP static site for SEO/link/schema defects."
    )
    parser.add_argument("path", help="Generated static site directory")
    parser.add_argument("--site-url", help="Canonical production/preview base URL")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable crawl result")
    args = parser.parse_args(argv)

    result = crawl_site(args.path, site_url=args.site_url)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(format_result(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
