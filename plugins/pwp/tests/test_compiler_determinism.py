import json
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.compiler import (  # noqa: E402
    controlled_page_overrides,
    compile_tokens_to_css,
    merge_dicts,
    merge_token_layers,
)


def _tokens() -> dict:
    return {
        "animations": {"slide": "c", "spin": "b", "fade": "a"},
        "shadows": {"xl": "4", "lg": "3", "md": "2", "sm": "1", "none": "0"},
        "radii": {"full": "4", "lg": "3", "md": "2", "sm": "1", "none": "0"},
        "spacing": {
            "3xl": "7",
            "2xl": "6",
            "xl": "5",
            "lg": "4",
            "md": "3",
            "sm": "2",
            "xs": "1",
            "none": "0",
        },
        "typography": {
            "line_heights": {"loose": "3", "normal": "2", "tight": "1"},
            "font_weights": {
                "bold": "400",
                "medium": "300",
                "regular": "200",
                "light": "100",
            },
            "font_sizes": {
                "4xl": "8px",
                "3xl": "7px",
                "2xl": "6px",
                "xl": "5px",
                "lg": "4px",
                "base": "3px",
                "sm": "2px",
                "xs": "1px",
            },
            "font_families": {
                "heading": "Georgia",
                "code": "Courier",
                "body": "Arial",
            },
        },
        "colors": {
            "semantic": {
                "warning": "#22bb22",
                "success": "#11aa11",
                "info": "#44dd44",
                "error": "#33cc33",
            },
            "secondary": "#222222",
            "primary": "#111111",
            "neutral": "#444444",
            "accent": "#333333",
        },
    }


def test_compile_tokens_to_css_is_stable_for_unsorted_tokens() -> None:
    tokens_a = _tokens()
    tokens_b = json.loads(json.dumps(tokens_a, sort_keys=True))

    css_a = compile_tokens_to_css(tokens_a)
    css_b = compile_tokens_to_css(tokens_b)

    assert css_a == css_b
    lines = [line.strip() for line in css_a.splitlines()[1:-1]]
    assert lines == sorted(lines)
    assert lines[0] == "--pwp-animation-fade: a;"
    assert lines[-1] == "--pwp-spacing-xs: 1;"
    assert "--pwp-color-semantic-success: #11aa11;" in lines


def test_merge_dicts_deep_copies_without_mutating_defaults() -> None:
    defaults = {"colors": {"primary": "pwp", "semantic": {"success": "green"}}}
    overrides = {"colors": {"semantic": {"warning": "gold"}}}

    merged = merge_dicts(defaults, overrides)
    merged["colors"]["semantic"]["success"] = "mutated"
    overrides["colors"]["semantic"]["warning"] = "mutated"

    assert defaults == {"colors": {"primary": "pwp", "semantic": {"success": "green"}}}
    assert merged["colors"]["primary"] == "pwp"
    assert merged["colors"]["semantic"]["warning"] == "gold"


def test_merge_token_layers_uses_pwp_theme_tenant_page_precedence() -> None:
    pwp_defaults = _tokens()
    theme_defaults = {
        "colors": {"primary": "theme", "semantic": {"success": "theme-success"}},
        "spacing": {"md": "theme-md"},
    }
    tenant_overrides = {
        "colors": {"primary": "tenant", "semantic": {"info": "tenant-info"}},
        "spacing": {"lg": "tenant-lg"},
    }
    page_overrides = {
        "colors": {"primary": "page", "accent": "blocked"},
        "spacing": {"lg": "blocked"},
    }
    original = json.loads(json.dumps(pwp_defaults, sort_keys=True))

    merged = merge_token_layers(
        pwp_defaults,
        theme_defaults=theme_defaults,
        tenant_overrides=tenant_overrides,
        page_overrides=page_overrides,
        allowed_page_override_paths=["colors.primary"],
    )

    assert merged["colors"]["primary"] == "page"
    assert merged["colors"]["accent"] == "#333333"
    assert merged["colors"]["semantic"]["success"] == "theme-success"
    assert merged["colors"]["semantic"]["info"] == "tenant-info"
    assert merged["spacing"]["md"] == "theme-md"
    assert merged["spacing"]["lg"] == "tenant-lg"
    assert pwp_defaults == original
    assert theme_defaults["colors"]["primary"] == "theme"
    assert tenant_overrides["colors"]["primary"] == "tenant"
    assert page_overrides["colors"]["primary"] == "page"


def test_page_overrides_require_explicit_allowlist() -> None:
    try:
        controlled_page_overrides({"colors": {"primary": "page"}}, allowed_paths=None)
    except ValueError as exc:
        assert "require" in str(exc)
    else:  # pragma: no cover - defensive; pytest.fail would add an import for one line
        raise AssertionError("page overrides without allowlist should fail")
