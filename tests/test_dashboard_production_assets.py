from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway.server import app


ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEAD = (
    ROOT / "prismatic" / "gateway" / "dashboard_src" / "shell" / "00_document_open.html"
)
GENERATED_DASHBOARD = ROOT / "prismatic" / "gateway" / "templates" / "dashboard.html"
BUILT_CSS = ROOT / "prismatic" / "gateway" / "static" / "dashboard.css"


def test_dashboard_uses_built_css_instead_of_tailwind_runtime():
    source = SOURCE_HEAD.read_text()
    generated = GENERATED_DASHBOARD.read_text()

    assert 'href="/static/dashboard.css"' in source
    assert 'href="/static/dashboard.css"' in generated
    assert "cdn.tailwindcss.com" not in source
    assert "cdn.tailwindcss.com" not in generated


def test_built_dashboard_css_contains_expected_utility_output():
    css = BUILT_CSS.read_text()

    assert len(css) > 30_000
    assert ".flex{" in css
    assert ".grid{" in css
    assert ".hidden{" in css
    assert "@tailwind" not in css


def test_gateway_serves_cacheable_built_dashboard_css():
    client = TestClient(app)

    response = client.get("/static/dashboard.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.content == BUILT_CSS.read_bytes()


def test_dashboard_root_references_local_asset_only():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/static/dashboard.css"' in response.text
    assert "cdn.tailwindcss.com" not in response.text
