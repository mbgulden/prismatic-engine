from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "seo_sites.json"


@dataclass(frozen=True)
class ManagedSite:
    slug: str
    name: str
    domain: str
    origin: str
    gsc_property: str | None = None
    sitemap_url: str | None = None
    site_dir_candidates: tuple[str, ...] = ()
    ga4_property_id: str | None = None
    ga4_property_env: str | None = None
    booking_provider: str | None = None
    booking_revenue_notes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_ga4_property_id(self) -> str | None:
        if self.ga4_property_id:
            return str(self.ga4_property_id)
        if self.ga4_property_env:
            value = os.environ.get(self.ga4_property_env)
            return value.strip() if value and value.strip() else None
        return None

    @property
    def effective_sitemap_url(self) -> str:
        return self.sitemap_url or f"{self.origin.rstrip('/')}/sitemap.xml"

    def resolve_site_dir(self) -> Path | None:
        for raw in self.site_dir_candidates:
            candidate = Path(raw).expanduser()
            if (candidate / "index.html").exists():
                return candidate
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "domain": self.domain,
            "origin": self.origin,
            "gsc_property": self.gsc_property,
            "sitemap_url": self.effective_sitemap_url,
            "site_dir_candidates": list(self.site_dir_candidates),
            "resolved_site_dir": str(self.resolve_site_dir()) if self.resolve_site_dir() else None,
            "ga4_property_id": self.effective_ga4_property_id,
            "ga4_property_env": self.ga4_property_env,
            "booking_provider": self.booking_provider,
            "booking_revenue_notes": self.booking_revenue_notes,
            **self.extra,
        }


def config_path() -> Path:
    return Path(os.environ.get("PRISMATIC_SEO_SITES_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()


def load_managed_sites(path: Path | None = None) -> list[ManagedSite]:
    path = path or config_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    sites: list[ManagedSite] = []
    for item in raw.get("sites", []):
        known = {
            "slug", "name", "domain", "origin", "gsc_property", "sitemap_url",
            "site_dir_candidates", "ga4_property_id", "ga4_property_env",
            "booking_provider", "booking_revenue_notes",
        }
        extra = {k: v for k, v in item.items() if k not in known}
        sites.append(ManagedSite(
            slug=item["slug"],
            name=item.get("name") or item["slug"],
            domain=item["domain"],
            origin=item.get("origin") or f"https://{item['domain']}",
            gsc_property=item.get("gsc_property"),
            sitemap_url=item.get("sitemap_url"),
            site_dir_candidates=tuple(item.get("site_dir_candidates") or ()),
            ga4_property_id=str(item["ga4_property_id"]) if item.get("ga4_property_id") else None,
            ga4_property_env=item.get("ga4_property_env"),
            booking_provider=item.get("booking_provider"),
            booking_revenue_notes=item.get("booking_revenue_notes"),
            extra=extra,
        ))
    return sites


def get_managed_site(slug: str, path: Path | None = None) -> ManagedSite:
    for site in load_managed_sites(path):
        if site.slug == slug:
            return site
    raise KeyError(f"Managed SEO site not found: {slug}")


def scaffold_site(domain: str, slug: str | None = None, name: str | None = None) -> dict[str, Any]:
    clean_domain = domain.removeprefix("https://").removeprefix("http://").strip("/")
    slug = slug or clean_domain.replace("www.", "").replace(".", "-")
    return {
        "slug": slug,
        "name": name or clean_domain,
        "domain": clean_domain,
        "origin": f"https://{clean_domain}",
        "gsc_property": f"sc-domain:{clean_domain.replace('www.', '')}",
        "sitemap_url": f"https://{clean_domain}/sitemap.xml",
        "site_dir_candidates": [f"/home/ubuntu/work/{slug}/site"],
        "ga4_property_env": f"{slug.upper().replace('-', '_')}_GA4_PROPERTY_ID",
        "ga4_property_id": None,
        "booking_provider": None,
        "booking_revenue_notes": "Set GA4 ecommerce/key-event tracking for booking starts, booking completions, and booking revenue before relying on revenue metrics.",
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Manage PE SEO site registry entries")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sc = sub.add_parser("scaffold")
    sc.add_argument("domain")
    sc.add_argument("--slug")
    sc.add_argument("--name")
    args = parser.parse_args()
    if args.cmd == "list":
        print(json.dumps([site.to_dict() for site in load_managed_sites()], indent=2, sort_keys=True))
        return 0
    if args.cmd == "scaffold":
        print(json.dumps(scaffold_site(args.domain, args.slug, args.name), indent=2, sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
