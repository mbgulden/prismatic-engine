# PWP bundled resource loading

Static PWP assets that must survive standalone packaging — theme token defaults, token schema, and bundled HTML starter templates — load through `plugins.pwp.resources` using `importlib.resources`.

## Contract

- Read-only bundled assets live under the package and are resolved with `bundled_resource()`, `bundled_json()`, or `bundled_text()`.
- Runtime-writable tenant overrides are **not** treated as package resources; they still live under the local tenant directory until the standalone extraction moves them behind an explicit state path.
- Validators and template rendering must not assume a checked-out repository layout for bundled schemas/templates.

## Why

The standalone PWP extraction needs wheel/sdist installs to read schemas and starter templates after a non-editable install. Repository-relative `Path(__file__)` joins are acceptable for mutable local state, but not for bundled read-only assets that should travel with the installed package.

## Proof strategy

`plugins/pwp/tests/test_bundled_resources.py` patches the package-resource root to a zip-backed traversable and verifies that:

1. token defaults still validate,
2. bundled starter templates still render, and
3. theme validation still loads bundled schemas.
