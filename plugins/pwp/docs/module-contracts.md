# PWP module contracts and fixture renderer

PWP modules now have machine-readable contracts under `plugins/pwp/modules/`.
Each contract declares:

- the Astro component name and supported slots;
- a JSON-Schema-shaped prop contract;
- editable fields exposed to EmDash/content agents;
- allowed variants;
- accessibility and content-safety rules;
- at least one fixture that must validate against the prop contract.

The shared contract schema lives at `plugins/pwp/schemas/pwp-module.schema.json`.
It intentionally uses ordinary JSON Schema vocabulary so the module library can
be consumed by Astro, TypeScript/Zod, Playwright, or Python verification without
private conventions.

## Fixture renderer

`plugins.pwp.module_fixture_renderer.render_all_fixtures()` is a deterministic
contract harness. It validates every fixture against the module prop schema and
renders stable semantic HTML with `data-pwp-module`, `data-component`, and
`data-variant` attributes. The harness is not a substitute for Astro build or
Playwright; it is the fast preflight that catches invented props, unknown
variants, missing editable field definitions, and unsafe fixture drift before
heavier visual/a11y gates run.

## Initial module set

The initial contracts cover the Phase 2 module library from the master plan:
`BaseLayout`, `SiteHeader`, `SiteFooter`, `Hero`, `WarningStrip`, `CardGrid`,
`TrustPanel`, `ProcessTimeline`, `SplitSection`, `LeadCapture`, `FAQ`,
`LocalSEOBlock`, `TestimonialGrid`, `PricingOrPackages`, and `RichTextPage`.

Agents assembling pages should select modules by contract id and variant instead
of inventing markup. New modules must add a contract, fixture, and test coverage
in the same change.
