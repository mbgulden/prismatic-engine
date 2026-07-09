"""Deterministic fixture renderer for PWP module contracts.

This is not a replacement for Astro. It is a fast contract harness: every module
fixture must validate against its module prop schema and produce stable semantic
HTML that later Playwright/Astro checks can wrap. If this harness cannot render a
fixture, an agent probably invented a field outside the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from .module_contracts import ModuleContractError, load_module_contracts, validate_schema_subset


@dataclass(frozen=True)
class RenderedFixture:
    module_id: str
    fixture_name: str
    variant: str
    html: str


def _render_value(key: str, value: Any) -> str:
    label = escape(str(key).replace("_", "-"))
    if isinstance(value, str):
        return f'<p data-prop="{label}">{escape(value)}</p>'
    if isinstance(value, list):
        items = "".join(f"<li>{_render_inline(item)}</li>" for item in value)
        return f'<ul data-prop="{label}">{items}</ul>'
    if isinstance(value, dict):
        body = "".join(_render_value(child_key, child) for child_key, child in value.items())
        return f'<div data-prop="{label}">{body}</div>'
    if isinstance(value, bool):
        return f'<p data-prop="{label}">{str(value).lower()}</p>'
    if value is None:
        return f'<p data-prop="{label}"></p>'
    return f'<p data-prop="{label}">{escape(str(value))}</p>'


def _render_inline(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(escape(str(v)) for v in value.values())
    return escape(str(value))


def render_fixture(contract: dict[str, Any], fixture: dict[str, Any]) -> RenderedFixture:
    if fixture["variant"] not in contract["variants"]:
        raise ModuleContractError(f"{contract['id']}: unknown fixture variant {fixture['variant']}")
    validate_schema_subset(fixture["props"], contract["propsSchema"], f"{contract['id']}.{fixture['name']}.props")
    props = fixture["props"]
    heading = props.get("title") or props.get("heading") or props.get("logoText") or contract["name"]
    heading_tag = "h1" if contract["id"] == "hero" else "h2"
    body = [f'<{heading_tag}>{escape(str(heading))}</{heading_tag}>']
    for key in sorted(props):
        if key in {"title", "heading", "logoText"}:
            continue
        body.append(_render_value(key, props[key]))
    html = (
        f'<section data-pwp-module="{escape(contract["id"])}" '
        f'data-component="{escape(contract["component"])}" '
        f'data-variant="{escape(fixture["variant"])}">'
        + "".join(body)
        + "</section>"
    )
    return RenderedFixture(contract["id"], fixture["name"], fixture["variant"], html)


def render_all_fixtures() -> list[RenderedFixture]:
    rendered: list[RenderedFixture] = []
    for contract in load_module_contracts():
        for fixture in contract["fixtures"]:
            rendered.append(render_fixture(contract, fixture))
    return rendered
