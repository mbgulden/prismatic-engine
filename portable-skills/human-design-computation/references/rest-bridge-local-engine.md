# REST Bridge Pattern — Local Engine Access Without MCP Transport

When the OpenHumanDesignMCP engine runs locally (same machine), the MCP transport layer (FastMCP SSE / JSON-RPC) adds unnecessary overhead. For production report generation and REST APIs, import the engine modules directly.

## Pattern

```python
import sys
import os

ENGINE_PATH = os.environ.get(
    "ENGINE_PATH",
    "/home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server/src"
)
sys.path.insert(0, ENGINE_PATH)

from cosmic_calculator import calculate_natal_chart
from synastry_engine import calculate_composite, calculate_penta
from matrix_mapper import GATE_NAMES, GATE_CENTER, CHANNELS
from ephemeris_engine import init_ephemeris

init_ephemeris()
```

## Key Functions & Signatures

### calculate_natal_chart
```python
chart = calculate_natal_chart(
    name="Michael Gulden",
    birth_dt=datetime(1986, 5, 15, 14, 30),  # datetime, NOT year/month/day
    lat=34.27,
    lon=-118.78,
    timezone="America/Los_Angeles",
)
```

**Critical**: `birth_dt` is a `datetime` object, not individual year/month/day parameters. Passing `year=1986` etc. will raise `TypeError: got unexpected keyword argument`.

### Chart output keys
```python
chart.keys() = [
    'name', 'hd_type', 'profile', 'authority', 'strategy', 'definition',
    'signature', 'not_self_theme',
    'incarnation_cross',        # dict with 'name', 'angle_type', 'gates'
    'defined_centers',          # list of str
    'undefined_centers',        # list of str
    'defined_channels',         # list of {gates: (int,int), name: str}
    'sun_gate', 'sun_line', 'earth_gate',
    'design_sun_gate', 'design_earth_gate',
    'personality_gates',        # list of dicts with 'gate', 'line', 'color', 'tone', 'base'
    'design_gates',             # same shape
    'all_active_gates',         # list of int gate numbers
    'variables',                # LIST (not dict): [digestion, environment, perspective, motivation, sense, cognition, trajectory]
    'astro_hd',                 # dict with personality_ascendant, design_descendant, etc.
    'personality_planets', 'design_planets',
]
```

### Gotchas
- `variables` is a **list** of 7 values, not a dict. Index `variables[0]` for digestion, `variables[1]` for environment, etc.
- `hd_type` not `type`
- `incarnation_cross` is a dict: `chart['incarnation_cross']['name']`
- Channels: `[{'gates': (12, 22), 'name': 'Openness (Individual)'}, ...]`
- Gates: `GATE_NAMES[gate_number]` returns the gate name string

## When to use

**Use direct import** when:
- Building REST APIs that need to compute charts quickly
- Generating PDF reports (deterministic output, no LLM needed)
- Running batch computations (multiple charts)
- Any production pipeline where MCP protocol overhead is undesirable

**Use MCP transport** when:
- The engine runs on a different machine
- You need the MCP tool interface (for Claude Desktop, etc.)
- You're in a constrained execution environment that can't import from disk

## REST Server Endpoint Design

Two endpoint tiers per the reference implementation at `/home/ubuntu/work/hd-platform/reports/server.py`:

| Endpoint | Auth | Returns | Use Case |
|---|---|---|---|
| `/api/compute-chart` | `X-API-Key` header | Chart JSON | Internal API, authenticated clients |
| `/api/public/compute-chart` | None | Chart JSON | Free widget, public demos |
| `/api/compute` | `X-API-Key` header | PDF path + chart summary | Payment webhook, email delivery |
| `/ping` | None | Service status | Health checks, monitoring |

**Public endpoint pattern**: Always provide a no-auth public variant alongside the auth-protected endpoint. This enables free widgets and embeddable demos without exposing paid endpoints.

## HTML/CSS to PDF Pipeline

The reference server uses wkhtmltopdf directly (no pandoc intermediary):

```python
import subprocess
from pathlib import Path

def html_to_pdf(html_content: str, output_path: Path) -> Path:
    html_path = output_path.with_suffix('.html')
    html_path.write_text(html_content)
    result = subprocess.run(
        ["wkhtmltopdf", "--quiet", "--enable-local-file-access",
         "--page-size", "Letter", "--margin-top", "0",
         "--margin-bottom", "0", "--margin-left", "0",
         "--margin-right", "0",
         str(html_path), str(output_path)],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"PDF generation failed: {result.stderr[:200]}")
    return output_path
```

**Template pattern**: Inline CSS in HTML with `@import` for Google Fonts (Inter + Playfair Display), gradient cover pages, stat cards, center grids, channel rows, gate badges. No external CSS files needed — fully self-contained HTML documents that render to beautiful PDFs.

## Payment Integration Flow

```
buy-report.html (Cloudflare Pages)
  → Stripe Checkout session (POST /create-checkout on :8000)
  → Stripe handles payment, returns to success URL
  → Stripe webhook fires (POST /webhook on :8000)
  → Payment server POSTs to bridge (POST /api/compute on :8081)
  → Engine computes chart → HTML rendered → PDF generated
  → PDF emailed to customer via SMTP
```

**Key**: The payment server on port 8000 is a thin proxy. All computation and rendering happens on the reports bridge (port 8081). This keeps the payment server simple (just Stripe + SMTP) and the bridge focused on computation.

## Widget Integration

Free embeddable widget at `/docs/widget.js` (14KB) — drop a `<div class="hde-chart-widget">` on any page. Widget calls `/api/public/compute-chart` (no auth), renders results client-side. Reference at `/home/ubuntu/work/hd-platform/docs/widget.js`.

## Reference Implementation

Full REST bridge + PDF report generator at `/home/ubuntu/work/hd-platform/reports/server.py` (~600 lines).
Payment server at `/home/ubuntu/work/hd-platform/payment/server.py` (~200 lines).
Free chart widget at `/home/ubuntu/work/hd-platform/docs/widget.js` + `/docs/widget-demo.html`.
