from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SITE_DIR_CANDIDATES = [
    REPO_ROOT / "site",
    Path("/home/ubuntu/work/active-oahu-tours-mirror/site"),
    Path("/home/ubuntu/work/active-oahu-tours-mirror-1251/site"),
]
AOT_ORIGIN = "https://activeoahutours.com"
AOT_GSC_PROPERTY = "sc-domain:activeoahutours.com"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def state_dir(*parts: str) -> Path:
    base = Path(os.environ.get("PRISMATIC_STATE_DIR", REPO_ROOT / "prismatic_state")).expanduser()
    path = base.joinpath("seo", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path


def resolve_site_dir() -> Path:
    override = os.environ.get("AOT_SITE_DIR")
    candidates = [Path(override).expanduser()] if override else []
    candidates.extend(DEFAULT_SITE_DIR_CANDIDATES)
    for candidate in candidates:
        if candidate and (candidate / "index.html").exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "Could not locate AOT static site. Set AOT_SITE_DIR or provide a site/ directory."
    )


def list_html_files(site_dir: Path | None = None) -> list[Path]:
    root = site_dir or resolve_site_dir()
    return sorted(
        p for p in root.rglob("*.html")
        if "wp-content" not in p.parts and "fonts" not in p.parts and p.is_file()
    )


def route_for_file(path: Path, site_dir: Path | None = None) -> str:
    root = site_dir or resolve_site_dir()
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[:-len("index.html")]
    return "/" + rel


def route_to_file(route: str, site_dir: Path | None = None) -> Path | None:
    root = site_dir or resolve_site_dir()
    parsed = urlparse(route)
    path = unquote(parsed.path or "/")
    if path == "/":
        candidates = [root / "index.html"]
    elif path.endswith("/"):
        candidates = [root / path.strip("/") / "index.html"]
    else:
        stripped = path.strip("/")
        candidates = [root / stripped, root / f"{stripped}.html", root / stripped / "index.html"]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.h_stack: list[str] = []
        self.in_title = False
        self.in_h1 = False
        self.meta_description = ""
        self.canonical = ""
        self.hrefs: list[str] = []
        self.jsonld_blocks: list[str] = []
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "h1":
            self.in_h1 = True
        elif tag == "meta" and attr.get("name", "").lower() == "description":
            self.meta_description = attr.get("content", "").strip()
        elif tag == "link" and attr.get("rel", "").lower() == "canonical":
            self.canonical = attr.get("href", "").strip()
        elif tag == "a" and attr.get("href"):
            self.hrefs.append(attr["href"].strip())
        elif tag == "script" and "application/ld+json" in attr.get("type", "").lower():
            self._in_jsonld = True
            self._jsonld_buf = []

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.in_h1:
            self.h1_parts.append(data)
        if self._in_jsonld:
            self._jsonld_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        elif tag == "h1":
            self.in_h1 = False
        elif tag == "script" and self._in_jsonld:
            self.jsonld_blocks.append("".join(self._jsonld_buf).strip())
            self._in_jsonld = False
            self._jsonld_buf = []

    @property
    def title(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.title_parts)).strip()

    @property
    def h1(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.h1_parts)).strip()


def parse_html_file(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    return parser


def normalize_internal_href(href: str, source_route: str = "/", origin: str = AOT_ORIGIN) -> str | None:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    absolute = urljoin(origin + source_route, href)
    parsed = urlparse(absolute)
    origin_host = urlparse(origin).netloc.lower()
    if parsed.netloc.lower() not in {origin_host, "www." + origin_host.removeprefix("www.")}:
        return None
    path = parsed.path or "/"
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    if not path.startswith("/"):
        path = "/" + path
    return path


def get_adc_token(scopes: list[str] | None = None) -> tuple[str, str | None]:
    scopes = scopes or ["https://www.googleapis.com/auth/webmasters.readonly"]
    try:
        import google.auth  # type: ignore
        import google.auth.transport.requests  # type: ignore
    except Exception as exc:  # pragma: no cover - exercised in environments without google libs
        raise RuntimeError("Google auth libraries not installed; install google-auth or configure gcloud ADC") from exc

    creds, project = google.auth.default(scopes=scopes)
    req = google.auth.transport.requests.Request()
    if not creds.valid:
        creds.refresh(req)
    token = getattr(creds, "token", None)
    if not token:
        raise RuntimeError("ADC did not yield an access token")
    quota_project = getattr(creds, "quota_project_id", None) or project
    return token, quota_project


def gsc_request(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    token, quota_project = get_adc_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if quota_project:
        headers["x-goog-user-project"] = quota_project
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(f"https://www.googleapis.com/webmasters/v3{path}", data=data, headers=headers, method=method)
    with urlopen(req, timeout=60) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def gsc_search_analytics(start_date: str, end_date: str, dimensions: list[str], row_limit: int = 25000) -> dict[str, Any]:
    site = quote(AOT_GSC_PROPERTY, safe="")
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "dataState": "final",
    }
    return gsc_request(f"/sites/{site}/searchAnalytics/query", method="POST", body=body)


def gsc_sites() -> dict[str, Any]:
    return gsc_request("/sites")


def gsc_sitemaps() -> dict[str, Any]:
    site = quote(AOT_GSC_PROPERTY, safe="")
    return gsc_request(f"/sites/{site}/sitemaps")


def run_command(command: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd or REPO_ROOT, text=True, capture_output=True, timeout=timeout, check=False)


def date_window(days: int = 90, lag_days: int = 3) -> tuple[str, str]:
    end = date.today() - timedelta(days=lag_days)
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()
