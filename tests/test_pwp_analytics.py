import json
import os
import shutil
import sys
from pathlib import Path
import pytest

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.compiler import render_template, TENANTS_DIR


@pytest.fixture
def clean_tenants_dir():
    # Back up existing tenants dir if any (though typically it doesn't exist yet in clean checkout)
    backup_dir = None
    if TENANTS_DIR.exists():
        backup_dir = TENANTS_DIR.parent / f"_tenants_backup_{os.getpid()}"
        shutil.move(str(TENANTS_DIR), str(backup_dir))
    
    TENANTS_DIR.mkdir(parents=True, exist_ok=True)
    
    yield
    
    # Clean up
    if TENANTS_DIR.exists():
        shutil.rmtree(str(TENANTS_DIR))
    if backup_dir and backup_dir.exists():
        shutil.move(str(backup_dir), str(TENANTS_DIR))


def test_default_plausible_no_config(clean_tenants_dir):
    """If no tenant config exists, it defaults to Plausible with <tenant_id>.com domain."""
    html = render_template("corporate", tenant_id="tenant123")
    
    # Verify Plausible script is in <head>
    assert "<head>" in html
    assert "</head>" in html
    
    # Look for Plausible snippet
    expected_script = '<script defer data-domain="tenant123.com" src="https://plausible.io/js/script.js"></script>'
    assert expected_script in html
    
    # Ensure other scripts are not present
    assert "googletagmanager" not in html
    assert "zaraz" not in html


def test_plausible_with_config(clean_tenants_dir):
    """If tenant config specifies plausible domain, it uses it."""
    tenant_dir = TENANTS_DIR / "tenant_plausible"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    
    config = {
        "domain": "custom-plausible.org"
    }
    with open(tenant_dir / "analytics.json", "w") as f:
        json.dump(config, f)
        
    html = render_template("corporate", tenant_id="tenant_plausible")
    
    expected_script = '<script defer data-domain="custom-plausible.org" src="https://plausible.io/js/script.js"></script>'
    assert expected_script in html
    assert "googletagmanager" not in html
    assert "zaraz" not in html


def test_gtag_fallback_config(clean_tenants_dir):
    """If tenant config has gtag_id, it falls back to Google Analytics 4."""
    tenant_dir = TENANTS_DIR / "tenant_gtag"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    
    config = {
        "gtag_id": "G-12345ABCDE"
    }
    with open(tenant_dir / "analytics.json", "w") as f:
        json.dump(config, f)
        
    html = render_template("corporate", tenant_id="tenant_gtag")
    
    assert 'src="https://www.googletagmanager.com/gtag/js?id=G-12345ABCDE"' in html
    assert "G-12345ABCDE" in html
    assert "plausible.io/js/script.js" not in html
    assert "zaraz" not in html


def test_zaraz_injection(clean_tenants_dir):
    """If tenant config specifies zaraz is available, it uses Zaraz (no JS server-side)."""
    tenant_dir = TENANTS_DIR / "tenant_zaraz"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    
    config = {
        "zaraz": True,
        "domain": "zaraz-enabled.com"
    }
    with open(tenant_dir / "analytics.json", "w") as f:
        json.dump(config, f)
        
    html = render_template("corporate", tenant_id="tenant_zaraz")
    
    expected_script = '<script src="/cdn-cgi/zaraz/i.js" referrerpolicy="origin"></script>'
    assert expected_script in html
    assert "plausible.io/js/script.js" not in html
    assert "googletagmanager" not in html


def test_no_tenant_id_defaults():
    """If no tenant_id is provided, it defaults to Plausible with default.com."""
    html = render_template("corporate", tenant_id=None)
    expected_script = '<script defer data-domain="default.com" src="https://plausible.io/js/script.js"></script>'
    assert expected_script in html
