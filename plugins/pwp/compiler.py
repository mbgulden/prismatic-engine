from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, Tuple

# Paths
PWP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PWP_DIR / "templates"
SCHEMA_PATH = TEMPLATES_DIR / "tokens.schema.json"
DEFAULT_TOKENS_PATH = TEMPLATES_DIR / "tokens.json"
TENANTS_DIR = PWP_DIR / "tenants"


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_dicts(base: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> dict:
    """Recursively merge overrides into base without mutating either input."""
    result = deepcopy(dict(base))
    if not overrides:
        return result

    for key, value in overrides.items():
        base_value = result.get(key)
        if isinstance(base_value, dict) and isinstance(value, Mapping):
            result[key] = merge_dicts(base_value, value)
        else:
            result[key] = deepcopy(value)
    return result


def _path_is_allowed(path: tuple[str, ...], allowed_paths: set[tuple[str, ...]]) -> bool:
    """Return whether an override path is under, or leads to, an allowed path."""
    return any(
        path[: len(allowed)] == allowed or allowed[: len(path)] == path
        for allowed in allowed_paths
    )


def _filter_page_overrides(
    overrides: Mapping[str, Any],
    allowed_paths: set[tuple[str, ...]],
    path: tuple[str, ...] = (),
) -> dict:
    """Copy only page override leaves under allowlisted token paths."""
    filtered: dict[str, Any] = {}
    for key, value in overrides.items():
        child_path = (*path, str(key))
        if not _path_is_allowed(child_path, allowed_paths):
            continue
        if isinstance(value, Mapping):
            child = _filter_page_overrides(value, allowed_paths, child_path)
            if child:
                filtered[key] = child
        else:
            filtered[key] = deepcopy(value)
    return filtered


def controlled_page_overrides(
    overrides: Mapping[str, Any] | None,
    allowed_paths: Sequence[str] | None,
) -> dict:
    """Return a non-mutating copy of page overrides limited to allowed dot paths.

    Page overrides are intentionally opt-in. A caller must provide dot-paths such
    as ``colors.primary`` or ``typography.font_sizes``; everything else is
    ignored so page-local changes cannot silently become a private theme fork.
    """
    if not overrides:
        return {}
    if not allowed_paths:
        raise ValueError("page overrides require at least one allowed token path")

    parsed_paths = {tuple(part for part in path.split(".") if part) for path in allowed_paths}
    parsed_paths.discard(())
    if not parsed_paths:
        raise ValueError("page override allowlist cannot be empty")
    return _filter_page_overrides(overrides, parsed_paths)


def merge_token_layers(
    pwp_defaults: Mapping[str, Any],
    theme_defaults: Mapping[str, Any] | None = None,
    tenant_overrides: Mapping[str, Any] | None = None,
    page_overrides: Mapping[str, Any] | None = None,
    allowed_page_override_paths: Sequence[str] | None = None,
) -> dict:
    """Merge token layers using PWP's deterministic precedence contract.

    Precedence, lowest to highest:
    1. PWP defaults
    2. theme family defaults
    3. tenant/client overrides
    4. controlled page-level overrides

    Every layer is deep-copied on write, so the caller's defaults and override
    fixtures remain reusable across renders, tests, deploy hashing, and rollback.
    """
    tokens = merge_dicts(pwp_defaults, theme_defaults)
    tokens = merge_dicts(tokens, tenant_overrides)
    page_layer = controlled_page_overrides(page_overrides, allowed_page_override_paths)
    return merge_dicts(tokens, page_layer)


def manual_validate(tokens: dict):
    """Fallback validator if jsonschema is not installed."""
    required_sections = [
        "colors",
        "typography",
        "spacing",
        "radii",
        "shadows",
        "animations",
    ]
    for sec in required_sections:
        if sec not in tokens:
            raise ValueError(f"Missing required section: '{sec}'")
        if not isinstance(tokens[sec], dict):
            raise TypeError(f"Section '{sec}' must be an object")

    # Validate colors
    colors = tokens["colors"]
    for c in ["primary", "secondary", "accent", "neutral", "semantic"]:
        if c not in colors:
            raise ValueError(f"Missing color: 'colors.{c}'")
    if not isinstance(colors["semantic"], dict):
        raise TypeError("colors.semantic must be an object")
    for s in ["success", "warning", "error", "info"]:
        if s not in colors["semantic"]:
            raise ValueError(f"Missing semantic color: 'colors.semantic.{s}'")

    # Validate typography
    typo = tokens["typography"]
    for t in ["font_families", "font_sizes", "font_weights", "line_heights"]:
        if t not in typo:
            raise ValueError(f"Missing typography section: 'typography.{t}'")
        if not isinstance(typo[t], dict):
            raise TypeError(f"typography.{t} must be an object")

    for f in ["body", "heading", "code"]:
        if f not in typo["font_families"]:
            raise ValueError(f"Missing font family: 'typography.font_families.{f}'")

    for fs in ["xs", "sm", "base", "lg", "xl", "2xl", "3xl", "4xl"]:
        if fs not in typo["font_sizes"]:
            raise ValueError(f"Missing font size: 'typography.font_sizes.{fs}'")

    for fw in ["light", "regular", "medium", "bold"]:
        if fw not in typo["font_weights"]:
            raise ValueError(f"Missing font weight: 'typography.font_weights.{fw}'")

    for lh in ["tight", "normal", "loose"]:
        if lh not in typo["line_heights"]:
            raise ValueError(f"Missing line height: 'typography.line_heights.{lh}'")

    # Validate spacing
    spacing = tokens["spacing"]
    for sp in ["none", "xs", "sm", "md", "lg", "xl", "2xl", "3xl"]:
        if sp not in spacing:
            raise ValueError(f"Missing spacing value: 'spacing.{sp}'")

    # Validate radii
    radii = tokens["radii"]
    for rd in ["none", "sm", "md", "lg", "full"]:
        if rd not in radii:
            raise ValueError(f"Missing radius value: 'radii.{rd}'")

    # Validate shadows
    shadows = tokens["shadows"]
    for sh in ["none", "sm", "md", "lg", "xl"]:
        if sh not in shadows:
            raise ValueError(f"Missing shadow value: 'shadows.{sh}'")

    # Validate animations
    anims = tokens["animations"]
    for an in ["fade", "spin", "slide"]:
        if an not in anims:
            raise ValueError(f"Missing animation value: 'animations.{an}'")


def validate_tokens(tokens: dict) -> None:
    """Validates the tokens structure against the schema."""
    try:
        import jsonschema

        schema = load_json(SCHEMA_PATH)
        jsonschema.validate(instance=tokens, schema=schema)
    except ImportError:
        manual_validate(tokens)


TOKEN_PREFIXES = {
    "colors": "color",
    "font_families": "font-family",
    "font_sizes": "font-size",
    "font_weights": "font-weight",
    "line_heights": "line-height",
    "spacing": "spacing",
    "radii": "radius",
    "shadows": "shadow",
    "animations": "animation",
}


def _iter_token_variables(prefix: str, value: Any) -> Iterable[Tuple[str, Any]]:
    """Yield flattened token-name/value pairs in a stable path order."""
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _iter_token_variables(f"{prefix}-{key}", value[key])
    else:
        yield prefix, value


def _compiled_token_pairs(tokens: dict) -> list[Tuple[str, Any]]:
    """Return PWP CSS variable pairs sorted by final custom-property name."""
    pairs: list[Tuple[str, Any]] = []

    colors = tokens.get("colors", {})
    for key in sorted(colors):
        yield_prefix = f"{TOKEN_PREFIXES['colors']}-{key}"
        pairs.extend(_iter_token_variables(yield_prefix, colors[key]))

    typography = tokens.get("typography", {})
    for section in ["font_families", "font_sizes", "font_weights", "line_heights"]:
        values = typography.get(section, {})
        for key in sorted(values):
            pairs.extend(
                _iter_token_variables(f"{TOKEN_PREFIXES[section]}-{key}", values[key])
            )

    for section in ["spacing", "radii", "shadows", "animations"]:
        values = tokens.get(section, {})
        for key in sorted(values):
            pairs.extend(
                _iter_token_variables(f"{TOKEN_PREFIXES[section]}-{key}", values[key])
            )

    return sorted((f"--pwp-{name}", value) for name, value in pairs)


def compile_tokens_to_css(tokens: dict) -> str:
    """Compile design tokens into stable, sorted PWP CSS custom properties."""
    validate_tokens(tokens)
    css_lines = [f"  {name}: {value};" for name, value in _compiled_token_pairs(tokens)]
    return ":root {\n" + "\n".join(css_lines) + "\n}"


def get_tokens_for_tenant(
    tenant_id: str | None = None,
    *,
    theme_defaults: Mapping[str, Any] | None = None,
    page_overrides: Mapping[str, Any] | None = None,
    allowed_page_override_paths: Sequence[str] | None = None,
) -> dict:
    """Loads and merges tokens for a tenant using PWP override precedence."""
    # Load default
    pwp_defaults = load_json(DEFAULT_TOKENS_PATH)
    tenant_overrides = None

    if tenant_id:
        tenant_path = TENANTS_DIR / tenant_id / "tokens.json"
        if tenant_path.exists():
            tenant_overrides = load_json(tenant_path)

    tokens = merge_token_layers(
        pwp_defaults,
        theme_defaults=theme_defaults,
        tenant_overrides=tenant_overrides,
        page_overrides=page_overrides,
        allowed_page_override_paths=allowed_page_override_paths,
    )
    validate_tokens(tokens)
    return tokens


def set_tenant_tokens(tenant_id: str, tokens: dict) -> None:
    """Saves override tokens for a given tenant."""
    # Ensure they validate first
    validate_tokens(tokens)
    tenant_path = TENANTS_DIR / tenant_id / "tokens.json"
    tenant_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tenant_path, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)


def render_template(template_name: str, tenant_id: str = None) -> str:
    """Renders the HTML for the specified template with the compiled CSS tokens."""
    template_html_path = TEMPLATES_DIR / template_name / "index.html"
    if not template_html_path.exists():
        raise FileNotFoundError(
            f"Template '{template_name}' not found at {template_html_path}"
        )

    # Get and validate compiled CSS variables
    tokens = get_tokens_for_tenant(tenant_id)
    css_vars = compile_tokens_to_css(tokens)

    # Load template HTML
    with open(template_html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Inject CSS vars
    placeholder = "/* PWP_TOKENS_PLACEHOLDER */"
    if placeholder in html:
        html = html.replace(placeholder, css_vars)
    else:
        # Fallback to appending/prepending style block if placeholder not found
        style_block = f'<style id="pwp-tokens">\n{css_vars}\n</style>'
        if "</head>" in html:
            html = html.replace("</head>", f"{style_block}\n</head>")
        else:
            html = style_block + "\n" + html

    return html
