"""
prismatic/swarm/archetypes.py — Sovereign Archetype Engine
===========================================================

Defines the 4 canonical project archetypes and their manifestation contracts
for the Prismatic 7-Step Iterative Swarm Loop:

1. SOFTWARE      — FastAPI APIs, schemas, unit tests, migrations, wheel builds.
2. WEB_PROPERTY  — Astro site compilation, Cloudflare Pages, Stripe checkout, local SEO.
3. OPERATIONS    — LLC filings, commercial agreements, pricing matrices, webhook triggers.
4. PUBLICATION   — Research metabolisms, long-form guides, E-E-A-T articles, content calendars.

Grounded in the North Star (SOUL.md): An idea in the morning, business cards and
living reality by afternoon.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.swarm.archetypes")


class Archetype(str, Enum):
    """The four canonical project archetypes."""

    SOFTWARE = "software"
    WEB_PROPERTY = "web_property"
    OPERATIONS = "operations"
    PUBLICATION = "publication"


@dataclass
class ArchetypeContract:
    """Individual worker contract within a decomposed swarm execution plan."""

    thread_id: str
    role: str
    agent_id: str  # "fred", "kai", "ned", "george", "autobot", "agy"
    task_description: str
    allowed_paths: list[str] = field(default_factory=list)
    read_only_paths: list[str] = field(default_factory=list)
    deliverable_keys: list[str] = field(default_factory=list)
    required_reviewers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectDeliverable:
    """Compiled, verified tangible deliverable produced by the 7-step loop."""

    id: str
    project_slug: str
    title: str
    archetype: Archetype
    summary: str
    location: str | None = None
    target_domain: str | None = None
    preview_url: str | None = None
    status: str = "manifested"  # "draft", "manifested", "deployed"
    artifacts: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["archetype"] = self.archetype.value
        return data


class ArchetypeRegistry:
    """Central registry and decomposer for project archetypes."""

    @staticmethod
    def detect_archetype(prompt: str) -> Archetype:
        """Heuristically determine project archetype from prompt text."""
        p_lower = prompt.lower()

        # Web Properties / Storefronts / Portals
        web_keywords = [
            "website", "web property", "storefront", "landing page", "astro", "html",
            "checkout", "ecommerce", "portal", "web app", "frontend", "site",
        ]
        if any(kw in p_lower for kw in web_keywords):
            return Archetype.WEB_PROPERTY

        # Commercial Operations / Legal
        ops_keywords = [
            "llc", "operating agreement", "compliance", "legal", "incorporation",
            "pricing tier", "contract", "terms of service", "trademark", "tax",
        ]
        if any(kw in p_lower for kw in ops_keywords):
            return Archetype.OPERATIONS

        # Publications & Content
        pub_keywords = [
            "research report", "manuscript", "book", "article", "seo calendar",
            "content brief", "whitepaper", "guide", "newsletter", "publication",
        ]
        if any(kw in p_lower for kw in pub_keywords):
            return Archetype.PUBLICATION

        # Default to Software
        return Archetype.SOFTWARE

    @staticmethod
    def get_lead_agents(archetype: Archetype) -> list[str]:
        """Return canonical team of agents for an archetype."""
        if archetype == Archetype.WEB_PROPERTY:
            return ["fred", "kai", "ned", "autobot", "george"]
        elif archetype == Archetype.OPERATIONS:
            return ["fred", "kai", "autobot", "george"]
        elif archetype == Archetype.PUBLICATION:
            return ["kai", "george", "fred"]
        else:  # SOFTWARE
            return ["fred", "ned", "george", "autobot"]

    @classmethod
    def decompose(
        cls,
        prompt: str,
        archetype: Archetype | None = None,
        task_id: str = "GRO-4854",
        options: dict[str, Any] | None = None,
    ) -> list[ArchetypeContract]:
        """Decompose a high-level vision into typed, non-overlapping worker contracts."""
        arch = archetype or cls.detect_archetype(prompt)
        opts = options or {}
        contracts: list[ArchetypeContract] = []
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

        if arch == Archetype.WEB_PROPERTY:
            # Contract 1: Creative Brand Voice & Copywriting
            contracts.append(
                ArchetypeContract(
                    thread_id=f"{task_id}-kai-{ts}",
                    role="Creative Director & Content Architect",
                    agent_id="kai",
                    task_description=(
                        f"Author compelling, high-converting offer copy for '{prompt}'. "
                        "Draft Hero headline, value propositions, product/service tiers, and FAQ."
                    ),
                    allowed_paths=["src/content/", "src/copy/", "deliverables/content.json"],
                    deliverable_keys=["hero_copy", "product_catalog", "faq_items", "meta_description"],
                    required_reviewers=["george", "fred"],
                )
            )

            # Contract 2: Web Compiler & Integration
            contracts.append(
                ArchetypeContract(
                    thread_id=f"{task_id}-ned-{ts}",
                    role="PWP Web Compiler & Integration Engineer",
                    agent_id="ned",
                    task_description=(
                        f"Compile high-performance, mobile-first responsive HTML/UI templates for '{prompt}'. "
                        "Wire checkout/pricing hooks, responsive layouts, and accessible UI controls. "
                        "Enforce strict Tailwind contrast (>= 4.5:1) and zero layout shifts."
                    ),
                    allowed_paths=["src/pages/", "src/components/", "deliverables/site.html", "deliverables/checkout.json"],
                    deliverable_keys=["site_html", "checkout_config", "responsive_css"],
                    required_reviewers=["george"],
                )
            )

            # Contract 3: SEO & Edge Deployment
            contracts.append(
                ArchetypeContract(
                    thread_id=f"{task_id}-autobot-{ts}",
                    role="Deployment & SEO Automation Engineer",
                    agent_id="autobot",
                    task_description=(
                        f"Generate Schema.org structured data (Organization / WebSite / Product) for '{prompt}'. "
                        "Configure edge hosting headers, SSL termination, and run 375px mobile audit."
                    ),
                    allowed_paths=["public/schema.json", "deliverables/schema_ld.json", "wrangler.toml"],
                    deliverable_keys=["schema_ld", "edge_config", "mobile_audit_passed"],
                    required_reviewers=["george"],
                )
            )

            # Contract 4: George — Security & Anti-Weakening Reviewer
            contracts.append(
                ArchetypeContract(
                    thread_id=f"{task_id}-george-{ts}",
                    role="Audit & Anti-Weakening Sentinel",
                    agent_id="george",
                    task_description=(
                        "Audit generated deliverables against security gates, XSS boundaries, valid JSON schemas, "
                        "and verify mobile layout at 375px viewport."
                    ),
                    allowed_paths=["reports/audit.json"],
                    read_only_paths=["deliverables/"],
                    deliverable_keys=["audit_attestation", "security_passed"],
                    required_reviewers=["fred"],
                )
            )

        elif arch == Archetype.SOFTWARE:
            contracts.append(
                ArchetypeContract(
                    thread_id=f"{task_id}-ned-{ts}",
                    role="Backend Architecture & API Engineer",
                    agent_id="ned",
                    task_description=f"Implement core backend API endpoints, domain models, and schemas for: {prompt}",
                    allowed_paths=["src/", "api/", "tests/"],
                    deliverable_keys=["api_code", "schemas", "test_suite"],
                    required_reviewers=["george"],
                )
            )
            contracts.append(
                ArchetypeContract(
                    thread_id=f"{task_id}-george-{ts}",
                    role="Test Oracle & Anti-Weakening Reviewer",
                    agent_id="george",
                    task_description="Execute test suite, verify AST anti-weakening invariants, and prove code safety.",
                    allowed_paths=["tests/", "reports/"],
                    deliverable_keys=["test_results", "ast_proof"],
                    required_reviewers=["fred"],
                )
            )

        elif arch == Archetype.OPERATIONS:
            contracts.append(
                ArchetypeContract(
                    thread_id=f"{task_id}-fred-{ts}",
                    role="Executive Operations & Legal Architect",
                    agent_id="fred",
                    task_description=f"Draft commercial operating agreements, merchant terms, and compliance matrix for: {prompt}",
                    allowed_paths=["docs/legal/", "config/pricing/"],
                    deliverable_keys=["operating_agreement", "terms_of_service", "pricing_matrix"],
                    required_reviewers=["george"],
                )
            )

        elif arch == Archetype.PUBLICATION:
            contracts.append(
                ArchetypeContract(
                    thread_id=f"{task_id}-kai-{ts}",
                    role="Lead Research Metabolizer & Author",
                    agent_id="kai",
                    task_description=f"Produce comprehensive deep-dive publication and E-E-A-T knowledge synthesis for: {prompt}",
                    allowed_paths=["docs/publications/", "reports/"],
                    deliverable_keys=["manuscript_md", "executive_summary", "citation_ledger"],
                    required_reviewers=["george"],
                )
            )

        return contracts
