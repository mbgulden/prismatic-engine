import json
from pathlib import Path
import sys


def _repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists() and (
            candidate / "scripts" / "pwp"
        ).exists():
            return candidate
    raise RuntimeError("Could not locate repo root for PWP tests")


_REPO_ROOT = _repo_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.compiler import compile_tokens_to_css  # noqa: E402


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
