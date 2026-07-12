from __future__ import annotations

from scripts.install_native_crons import BEGIN, END, render_block, replace_managed_block


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
