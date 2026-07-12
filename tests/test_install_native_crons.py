from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
_loaded_prismatic = sys.modules.get("prismatic")
if _loaded_prismatic is not None:
    module_file = getattr(_loaded_prismatic, "__file__", "") or ""
    if not module_file.startswith(str(_REPO_ROOT)):
        for name in list(sys.modules):
            if name == "prismatic" or name.startswith("prismatic."):
                sys.modules.pop(name, None)

_INSTALLER_PATH = _REPO_ROOT / "scripts" / "install_native_crons.py"
_SPEC = importlib.util.spec_from_file_location("install_native_crons", _INSTALLER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
installer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(installer)

BEGIN = installer.BEGIN
END = installer.END
render_block = installer.render_block
replace_managed_block = installer.replace_managed_block


def test_render_block_contains_active_native_seo_crons() -> None:
    block = render_block()

    assert BEGIN in block
    assert END in block
    assert "scripts/pwp credentials refresh ubersuggest" in block
    assert "scripts/seo/aot_kpi_tracker.py" in block
    assert "scripts/seo/competitor_velocity.py" in block
    assert "scripts/seo/seo_full_sweep.py" not in block


def test_replace_managed_block_appends_when_missing() -> None:
    existing = "MAILTO=ops@example.com\n"
    block = f"{BEGIN}\n* * * * * echo hi\n{END}\n"
    result = replace_managed_block(existing, block)

    assert result.startswith("MAILTO=ops@example.com")
    assert result.count(BEGIN) == 1
    assert "echo hi" in result


def test_replace_managed_block_replaces_existing_block() -> None:
    existing = f"MAILTO=ops@example.com\n{BEGIN}\nold line\n{END}\n# tail\n"
    block = f"{BEGIN}\nnew line\n{END}\n"
    result = replace_managed_block(existing, block)

    assert "old line" not in result
    assert "new line" in result
    assert result.count(BEGIN) == 1
    assert result.rstrip().endswith("# tail")
