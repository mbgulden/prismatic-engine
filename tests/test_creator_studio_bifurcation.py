"""Sprint 1 Verification Test: Creator Studio View Bifurcation and Routes."""

from fastapi.testclient import TestClient
from pathlib import Path
from prismatic.gateway.server import app

def test_dashboard_template_contains_creator_studio_bifurcation():
    dashboard_path = Path(__file__).resolve().parent.parent / "prismatic" / "gateway" / "templates" / "dashboard.html"
    assert dashboard_path.exists(), "dashboard.html must exist"
    content = dashboard_path.read_text(encoding="utf-8")

    # 1. Mode Switcher & Segmented Controls
    assert 'data-proof-marker="dashboard-ui-mode-switcher"' in content
    assert 'id="mode-btn-creator"' in content
    assert 'id="mode-btn-engineering"' in content
    assert 'setUIMode(' in content

    # 2. Bifurcated Navigations
    assert 'data-proof-marker="creator-tabs-bar"' in content
    assert 'id="nav-creator-tabs"' in content
    assert 'id="nav-engineering-tabs"' in content
    assert 'id="tab-btn-studio"' in content
    assert 'id="tab-btn-assets"' in content
    assert 'id="tab-btn-pulse"' in content

    # 3. Creator Sections
    assert 'id="section-studio"' in content
    assert 'id="section-assets"' in content
    assert 'id="section-pulse"' in content

    # 4. Creator Studio Presets and Controls
    assert 'loadStudioPreset' in content
    assert 'executeStudioManifestation' in content
    assert 'setAssetPreviewViewport' in content
    assert 'loadStudioPreset(\'software\')' in content or 'loadStudioPreset("software")' in content

    # 5. Engineering tabs preserved (14 tabs)
    for eng_tab in ['telemetry', 'merge', 'review-factory', 'workspaces', 'skills', 'signals', 'swarmproof', 'pwp', 'plugins', 'crons', 'quota', 'foundation', 'settings']:
        assert f'id="tab-btn-{eng_tab}"' in content
        assert f'id="section-{eng_tab}"' in content


def test_creator_studio_routes():
    client = TestClient(app)

    # Root
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "PRISMATIC HUB" in res_root.text

    # Top-level creator routes
    res_studio = client.get("/studio")
    assert res_studio.status_code == 200
    assert "Creator Studio" in res_studio.text

    res_assets = client.get("/assets")
    assert res_assets.status_code == 200
    assert "Deployed Assets" in res_assets.text

    res_pulse = client.get("/pulse")
    assert res_pulse.status_code == 200
    assert "Fleet Pulse" in res_pulse.text

    # Engineering routes still 200
    res_dash = client.get("/dashboard")
    assert res_dash.status_code == 200
    res_rf = client.get("/review-factory")
    assert res_rf.status_code == 200
