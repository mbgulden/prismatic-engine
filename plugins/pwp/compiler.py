import json
from pathlib import Path
from typing import Any, Iterable, Tuple

# Paths
PWP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PWP_DIR / "templates"
SCHEMA_PATH = TEMPLATES_DIR / "tokens.schema.json"
DEFAULT_TOKENS_PATH = TEMPLATES_DIR / "tokens.json"
TENANTS_DIR = PWP_DIR / "tenants"


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_dicts(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base dictionary."""
    result = base.copy()
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = merge_dicts(result[k], v)
        else:
            result[k] = v
    return result


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


def get_tokens_for_tenant(tenant_id: str = None) -> dict:
    """Loads and merges tokens for a given tenant."""
    # Load default
    tokens = load_json(DEFAULT_TOKENS_PATH)

    if tenant_id:
        tenant_path = TENANTS_DIR / tenant_id / "tokens.json"
        if tenant_path.exists():
            overrides = load_json(tenant_path)
            tokens = merge_dicts(tokens, overrides)

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


def _get_analytics_snippet(tenant_id: str = None) -> str:
    """Load analytics configuration and generate the appropriate HTML script snippet."""
    config = {}
    if tenant_id:
        config_path = TENANTS_DIR / tenant_id / "analytics.json"
        if config_path.exists():
            try:
                config = load_json(config_path)
            except Exception:
                pass

    # Zaraz takes precedence if present/enabled
    if config.get("zaraz"):
        return '<script src="/cdn-cgi/zaraz/i.js" referrerpolicy="origin"></script>'

    # GTAG (Google Analytics 4) if configured
    if "gtag_id" in config and config["gtag_id"]:
        gtag_id = config["gtag_id"]
        return (
            f'<script async src="https://www.googletagmanager.com/gtag/js?id={gtag_id}"></script>\n'
            f'<script>\n'
            f'  window.dataLayer = window.dataLayer || [];\n'
            f'  function gtag(){{dataLayer.push(arguments);}}\n'
            f"  gtag('js', new Date());\n"
            f"  gtag('config', '{gtag_id}');\n"
            f'</script>'
        )

    # Plausible (Default)
    domain = config.get("domain") or config.get("plausible_domain")
    if not domain:
        if tenant_id:
            domain = f"{tenant_id}.com"
        else:
            domain = "default.com"

    return f'<script defer data-domain="{domain}" src="https://plausible.io/js/script.js"></script>'


def render_template(template_name: str, tenant_id: str = None) -> str:
    """Renders the HTML for the specified template with the compiled CSS tokens and analytics."""
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

    # Inject analytics snippet right before </head>
    analytics_snippet = _get_analytics_snippet(tenant_id)
    if "</head>" in html:
        html = html.replace("</head>", f"{analytics_snippet}\n</head>")
    else:
        html = html + "\n" + analytics_snippet

    return html
