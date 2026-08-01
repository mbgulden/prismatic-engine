"""
prismatic/billing/routes.py — Pluggable Flask routes for billing API.

Mount these on any Flask/FastAPI app to expose billing endpoints.
Designed to be imported by the dispatcher's Port 9000 server when
Phase 4.2 (Prometheus metrics endpoint) lands.

Usage:
    from prismatic.billing.routes import register_billing_routes
    register_billing_routes(app)
"""

from __future__ import annotations

import json
from typing import Any

from .cost_attribution import CostAttributionEngine


def register_billing_routes(app: Any) -> None:
    """Register billing API routes on a Flask application.

    Adds:
        GET  /api/billing/report       — billing summary
        GET  /api/billing/projection   — rolling 7-day cost projection
        POST /api/billing/attribution  — set issue → client/project mapping
    """
    try:
        from flask import jsonify, request
    except ImportError:
        # Flask not available — register as pass-through
        return

    engine = CostAttributionEngine()

    @app.route("/api/billing/report", methods=["GET"])
    def get_billing_report():
        """Generate aggregated billing report.

        Query params:
            client  — filter by client_id
            project — filter by project_id
            format  — json | csv (default: json)
        """
        client_id = request.args.get("client")
        project_id = request.args.get("project")
        output_format = request.args.get("format", "json")

        if output_format == "csv":
            csv_data = engine.generate_report_csv(
                client_id=client_id, project_id=project_id
            )
            return app.response_class(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": "attachment; filename=billing_report.csv"},
            )

        reports = engine.generate_report(
            client_id=client_id, project_id=project_id
        )
        result = []
        for report in reports:
            result.append({
                "client_id": report.client_id,
                "project_id": report.project_id,
                "total_cost_usd": round(report.total_cost_usd, 6),
                "agent_breakdown": {
                    k: {"cost_usd": round(v["cost_usd"], 6), "entries": v["entries"]}
                    for k, v in report.agent_breakdown.items()
                },
                "model_breakdown": {
                    k: {"cost_usd": round(v["cost_usd"], 6), "entries": v["entries"]}
                    for k, v in report.model_breakdown.items()
                },
                "period_start": report.period_start,
                "period_end": report.period_end,
            })
        return jsonify(result)

    @app.route("/api/billing/projection", methods=["GET"])
    def get_cost_projection():
        """Get rolling 7-day cost projection.

        Query params:
            client  — filter by client_id
            project — filter by project_id
        """
        client_id = request.args.get("client")
        project_id = request.args.get("project")

        proj = engine.project_costs(
            client_id=client_id, project_id=project_id
        )
        return jsonify({
            "client_id": client_id or "all",
            "project_id": project_id or "all",
            "average_daily_usd": proj.average_daily,
            "projected_monthly_usd": proj.projected_monthly,
            "trend": proj.trend,
            "confidence": proj.confidence,
            "days_of_data": len([c for c in proj.daily_costs if c > 0]),
            "total_days": len(proj.daily_costs),
            "daily_costs": [
                round(c, 6) for c in proj.daily_costs
            ],
        })

    @app.route("/api/billing/attribution", methods=["POST"])
    def set_attribution():
        """Set issue → client/project billing mapping.

        JSON body:
            {"issue_id": "GRO-1234", "client_id": "acme-corp", "project_id": "website"}
        """
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "JSON body required"}), 400

        issue_id = data.get("issue_id")
        client_id = data.get("client_id")
        project_id = data.get("project_id")

        if not all([issue_id, client_id, project_id]):
            return jsonify({"error": "issue_id, client_id, and project_id are required"}), 400

        engine.set_attribution(issue_id, client_id, project_id)
        return jsonify({
            "status": "ok",
            "issue_id": issue_id,
            "client_id": client_id,
            "project_id": project_id,
        })
