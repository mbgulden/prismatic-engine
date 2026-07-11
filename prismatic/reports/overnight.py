"""Generate the Prismatic overnight factory briefing.

The generator is intentionally conservative: it reports metrics that can be
proven from local Prismatic state files and leaves unknown business-impact
fields explicit instead of inventing numbers.  It writes both machine-readable
JSON and a small HTML card that the dashboard can embed or link to.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prismatic.run_records import AgentRunRecordStore

DEFAULT_REPORT_DIR = Path("~/.prismatic/reports").expanduser()
DEFAULT_STATE_DIR = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/"))
DELIVERABLE_URL_RE = re.compile(r"https?://[^\s)>'\"]+")
PR_RE = re.compile(r"(?:pull/|PR\s*#|#)(\d+)", re.IGNORECASE)


@dataclass
class OvernightReport:
    generated_at: str
    factory_duration: str
    tasks_processed: int
    tasks_autonomous: int
    tasks_need_human: int
    cost_dollars: float
    tokens_saved: int
    needs_hand: list[dict[str, str]] = field(default_factory=list)
    deliverables: list[dict[str, str]] = field(default_factory=list)
    revenue_impact: str = "unknown — no revenue attribution source found"
    north_star_delta: str = "unknown — no north-star snapshot source found"
    horizon_activity: dict[str, int] = field(default_factory=lambda: {
        "coding": 0,
        "business": 0,
        "creative": 0,
        "knowledge": 0,
        "dream": 0,
    })
    cost_by_agent: dict[str, float] = field(default_factory=dict)
    data_quality: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_duration(delta: timedelta) -> str:
    total_minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m"


def _load_json(path: Path) -> Any | None:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _load_costs(report: OvernightReport, report_dir: Path) -> None:
    """Load optional cost attribution without guessing if absent."""
    candidates = [
        report_dir / "costs.json",
        Path("~/.prismatic/costs.json").expanduser(),
        Path("~/.prismatic/billing/costs.json").expanduser(),
    ]
    data = next((item for item in (_load_json(path) for path in candidates) if isinstance(item, dict)), None)
    if not data:
        report.data_quality.append("No cost attribution file found; cost fields set to 0.")
        return

    by_agent = data.get("cost_by_agent") or data.get("agents") or {}
    if isinstance(by_agent, dict):
        report.cost_by_agent = {
            str(agent): round(float(cost), 4)
            for agent, cost in by_agent.items()
            if isinstance(cost, (int, float)) or str(cost).replace(".", "", 1).isdigit()
        }
        report.cost_dollars = round(sum(report.cost_by_agent.values()), 4)
    elif isinstance(data.get("cost_dollars"), (int, float)):
        report.cost_dollars = round(float(data["cost_dollars"]), 4)


def _classify_deliverable(output_path: str | None) -> dict[str, str] | None:
    if not output_path:
        return None
    text = output_path
    path = Path(output_path).expanduser()
    if path.exists() and path.is_file():
        try:
            text = path.read_text(errors="ignore")[:20000]
        except OSError:
            text = output_path

    url_match = DELIVERABLE_URL_RE.search(text)
    pr_match = PR_RE.search(text)
    if url_match:
        url = url_match.group(0).rstrip(".,")
        dtype = "page_deployed" if "http" in url else "url"
        return {"type": dtype, "url": url, "grade": "ungraded"}
    if pr_match:
        return {"type": "pr_reference", "pr": f"#{pr_match.group(1)}", "repo": "unknown"}
    if output_path:
        return {"type": "artifact", "path": output_path}
    return None


def generate_report(hours: int = 12, report_dir: Path = DEFAULT_REPORT_DIR) -> OvernightReport:
    now = _utc_now()
    window_start = now - timedelta(hours=hours)
    store_path = os.environ.get("PRISMATIC_RUN_RECORDS")
    if not store_path:
        store_path = str(DEFAULT_STATE_DIR / "run_records.json")
    store = AgentRunRecordStore(store_path=store_path)
    records = []
    for record in store.all_records:
        started = _parse_datetime(record.started_at)
        completed = _parse_datetime(record.completed_at)
        event_time = completed or started
        if event_time and event_time >= window_start:
            records.append(record)

    report = OvernightReport(
        generated_at=now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        factory_duration=_format_duration(now - window_start),
        tasks_processed=len(records),
        tasks_autonomous=sum(1 for r in records if r.status == "completed"),
        tasks_need_human=sum(1 for r in records if r.status == "failed"),
        cost_dollars=0.0,
        tokens_saved=0,
    )

    for record in records:
        if record.status == "failed":
            report.needs_hand.append({
                "issue": record.issue_id,
                "reason": record.error_message or "agent run failed",
                "action": "Review run record and artifact output",
            })
        deliverable = _classify_deliverable(record.output_path)
        if deliverable:
            report.deliverables.append(deliverable)

    if not report.deliverables:
        report.data_quality.append("No deliverable URLs, PR references, or artifact paths found in run records.")

    agent_counts: dict[str, int] = {}
    for record in records:
        agent_counts[record.agent_name.lower()] = agent_counts.get(record.agent_name.lower(), 0) + 1
    report.horizon_activity["coding"] = sum(agent_counts.values())

    _load_costs(report, report_dir)
    return report


def render_html(report: OvernightReport) -> str:
    data = report.to_dict()
    needs = "".join(
        f"<li><strong>{html.escape(item['issue'])}</strong>: {html.escape(item['reason'])} — {html.escape(item['action'])}</li>"
        for item in report.needs_hand
    ) or "<li>No failed run records in the window.</li>"
    deliverables = "".join(
        f"<li>{html.escape(item.get('type', 'deliverable'))}: {html.escape(item.get('url') or item.get('pr') or item.get('path') or json.dumps(item))}</li>"
        for item in report.deliverables
    ) or "<li>No deliverables discovered in run records.</li>"
    quality = "".join(f"<li>{html.escape(msg)}</li>" for msg in report.data_quality) or "<li>All configured data sources loaded.</li>"
    return f"""<!doctype html>
<html lang=\"en\">
<head><meta charset=\"utf-8\"><title>Prismatic Overnight Briefing</title>
<style>body{{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;padding:2rem}}.card{{max-width:880px;margin:auto;border:1px solid #30363d;border-radius:14px;padding:1.5rem;background:#161b22}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem}}.metric{{border:1px solid #30363d;border-radius:10px;padding:1rem}}.metric strong{{display:block;font-size:1.7rem;color:#58a6ff}}code{{color:#7ee787}}</style></head>
<body><main class=\"card\"><h1>Overnight Factory Briefing</h1><p>Generated <code>{html.escape(report.generated_at)}</code> over <code>{html.escape(report.factory_duration)}</code>.</p>
<div class=\"grid\"><div class=\"metric\">Processed<strong>{data['tasks_processed']}</strong></div><div class=\"metric\">Autonomous<strong>{data['tasks_autonomous']}</strong></div><div class=\"metric\">Needs hand<strong>{data['tasks_need_human']}</strong></div><div class=\"metric\">Cost<strong>${data['cost_dollars']:.2f}</strong></div></div>
<h2>Needs hand</h2><ul>{needs}</ul><h2>Deliverables</h2><ul>{deliverables}</ul><h2>Data quality</h2><ul>{quality}</ul></main></body></html>"""


def write_report(report: OvernightReport, report_dir: Path = DEFAULT_REPORT_DIR) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "latest.json"
    html_path = report_dir / "latest.html"
    json_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    html_path.write_text(render_html(report))
    return json_path, html_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the overnight Prismatic factory briefing")
    parser.add_argument("--hours", type=int, default=12, help="Lookback window in hours")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="Output directory")
    parser.add_argument("--print-json", action="store_true", help="Print the report JSON after writing")
    args = parser.parse_args(argv)

    report = generate_report(hours=args.hours, report_dir=args.report_dir)
    json_path, html_path = write_report(report, args.report_dir)
    if args.print_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"Wrote {json_path} and {html_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
