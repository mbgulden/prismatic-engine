"""step register_in_registry — add the domain to a local sites.json appendix.

`config/seo_sites.json` is owned by another lane (per Ned's lane
discipline) and the PWP plugin can't write to it. For Phase 1 we
write to a separate `<publish_root>/provisioning/sites.json` appendix
that the canonical registry loader merges at read-time.

Phase 2 will wire the registry loader to read the appendix. For now,
the appendix file just exists so `migrate_kpi` has somewhere to find
the new site.

The appendix is a JSON object keyed by domain; each entry has the
minimal fields the v1→v2 adapter needs:

  {
    "<domain>": {
      "slug": "<slug>",
      "name": "<human-readable>",
      "owner": "<owner-email>",
      "ga4_measurement_env": "<SITE>_GA4_MEASUREMENT_ID",
      "pwp_kpi_override": {"enabled": true},
      "expected_data_layer_events": [],
      "registered_at": "<iso8601>"
    }
  }
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict


def slug_from_domain(domain: str) -> str:
    """Convert a domain to a slug: 'example.com' -> 'example-com'.

    Strip the TLD-ish suffix and use the resulting token as the
    registry slug. Falls back to the full domain if the result is
    empty (e.g. just '.com').
    """
    head = domain.split(".", 1)[0]
    head = head.lower().replace("_", "-")
    return head or domain


def appendix_path(publish_root: Path) -> Path:
    """Where the sites.json appendix lives."""
    return publish_root / "sites.json"


def add_site_to_registry(
    domain: str, owner: str, slug: str, publish_root: Path
) -> Path:
    """Add `domain` to the sites.json appendix. Idempotent."""
    path = appendix_path(publish_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    env_var = f"{slug.upper().replace('-', '_')}_GA4_MEASUREMENT_ID"
    data[domain] = {
        "slug": slug,
        "name": slug,
        "owner": owner,
        "domain": domain,
        "ga4_measurement_env": env_var,
        "expected_data_layer_events": [],
        "expected_ga4_recommended_events": [],
        "pwp_kpi_override": {"enabled": True},
        "registered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return path


def list_sites_in_appendix(publish_root: Path) -> Dict[str, Any]:
    """Read all sites from the appendix. Empty dict if none."""
    path = appendix_path(publish_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
