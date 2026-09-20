# PWP AI-First Astro Theme System — Master Plan

**Status:** master plan / implementation blueprint
**Owner lane:** Prismatic Web Plugin / theme pipeline
**Created:** 2026-07-09
**Author:** Ned
**Target:** Prismatic Web Plugin (PWP) long-term theme/template system

## Executive summary

Build PWP’s website generation layer as an **AI-first, human-facing Astro theme system**: agents work against typed schemas, design tokens, module contracts, verification gates, and migration tooling; humans see fast, coherent, accessible, editable websites.

The goal is not another pile of templates. The goal is a **theme operating system**:

1. ingest business/content/design requirements,
2. select or synthesize an appropriate theme package,
3. render pages through standard Astro modules,
4. expose safe editable fields through EmDash,
5. validate output through deterministic checks,
6. deploy via PWP with rollback/state tracking,
7. allow redesigns by changing tokens/components once instead of hand-patching every page.

This plan deliberately uses proven open standards and community patterns rather than inventing a private design religion in a closet.

## Non-negotiable principles

### 1. AI-first development, human-first frontend

- Agents need structured manifests, schemas, module contracts, and validators.
- Humans need polished, fast, accessible pages with clear copy and conversion paths.
- Do not optimize the public UI for agent convenience at the expense of human trust.

### 2. Theme packages, not page dumps

Every theme must ship as a package with:

- `theme.json` manifest
- design tokens
- Astro layouts/components
- content schema bindings
- EmDash editable-field map
- sample pages
- visual snapshots
- accessibility/performance checks
- migration notes

A theme without a manifest and tests is a costume, not a system.

### 3. Open standards first

Prefer established standards and community-compatible tooling:

| Need | Standard / proven strategy |
|---|---|
| Design token shape | W3C Design Tokens Community Group format, with PWP namespace mapping |
| Token transforms | Style Dictionary-compatible transforms, CSS custom properties |
| Component architecture | Astro components, slots, typed props, content collections |
| Editable content | EmDash structured content / Portable Text-style structured fields |
| Validation | JSON Schema, Zod, TypeScript types, Playwright/a11y/Lighthouse gates |
| CSS theming | CSS custom properties, cascade layers, optional Tailwind preset export |
| Package distribution | npm package shape plus PWP theme registry metadata |
| Visual regression | Playwright screenshots / Storybook or Astro pages as harness |
| Accessibility | WCAG 2.2 AA checks via axe-core / Playwright integration |

### 4. Redesigns update the system, not individual pages

Any redesign must flow through:

1. design tokens,
2. component variants,
3. theme manifest,
4. EmDash field contracts,
5. generated pages.

No random one-off page CSS. We have all seen that movie. It ends in `legacy-final-v3.css`.

## Existing PWP foundation to preserve

This plan builds on current PWP concepts already present in the engine:

- PWP pipeline hook contract: `on_pre_pipeline`, `on_post_pipeline`, `on_error`, `on_deploy`.
- Existing run-state / rollback direction in `prismatic/core/pwp_state.py`.
- Existing design-token test direction in `tests/test_pwp_design_tokens.py`.
- Existing Cloudflare deploy/idempotency tests in `tests/test_pwp_cloudflare_deploy.py`.
- Existing ingest/distill standards:
  - `okf/standards/pwp-ingest-spec.md`
  - `okf/standards/pwp-distill-spec.md`

The theme system should slot into that pipeline instead of bypassing it.

## Target architecture

```text
Client inputs / OKF / interview docs
  ↓
PWP ingest → structured client profile + content brief
  ↓
Theme classifier → choose base theme family + module set + required variants
  ↓
Theme package resolver
  ├─ tokens
  ├─ Astro components/layouts
  ├─ EmDash content schema + editable-field map
  ├─ sample pages + fixtures
  └─ verification profile
  ↓
Page synthesis
  ├─ content collections / EmDash records
  ├─ route manifest
  ├─ module composition plan
  └─ SEO/schema data
  ↓
Astro build / preview
  ↓
Verification gates
  ├─ schema validation
  ├─ typecheck/build
  ├─ accessibility
  ├─ visual regression
  ├─ performance budgets
  ├─ content safety/compliance lint
  └─ link/sitemap checks
  ↓
PWP deploy adapter → Cloudflare Pages / target host
  ↓
Run-state record + rollback metadata + theme provenance
```

## Core artifacts

### 1. Theme manifest

Each theme ships `theme.json`:

```json
{
  "$schema": "https://schemas.prismatic.dev/pwp/theme.schema.json",
  "id": "pwp.theme.sentinel-light",
  "name": "Sentinel Light",
  "version": "0.1.0",
  "engineCompatibility": ">=0.2.0",
  "framework": "astro",
  "editableWith": ["emdash"],
  "industryFit": ["professional-services", "local-services", "compliance", "b2b"],
  "styleTags": ["light", "trust", "security", "local", "conversion"],
  "entrypoints": {
    "tokens": "tokens/tokens.json",
    "css": "src/styles/theme.css",
    "layout": "src/layouts/BaseLayout.astro",
    "components": "src/components/index.ts",
    "contentSchema": "src/content.config.ts",
    "emdashMap": "emdash/fields.json"
  },
  "modules": [
    "hero",
    "warning-strip",
    "card-grid",
    "trust-panel",
    "process-timeline",
    "split-section",
    "lead-capture"
  ],
  "verification": {
    "commands": ["npm run check:theme", "npm run build"],
    "requiresVisual": true,
    "requiresA11y": true,
    "performanceBudget": "budgets/lighthouse.json"
  }
}
```

### 2. Design tokens

Use W3C-style token groups, then compile to PWP CSS variables.

Canonical token groups:

```text
color
  background
  surface
  text
  accent
  semantic
  dark-scope
font
space
size
radius
shadow
motion
breakpoint
zIndex
```

Compiled output:

```css
:root {
  --pwp-color-background-page: ...;
  --pwp-color-surface-card: ...;
  --pwp-font-family-body: ...;
}
```

Compiler contract for Phase 1:

- `compile_tokens_to_css(tokens)` validates the token object before emitting CSS.
- Output uses the `--pwp-*` namespace and is sorted by final CSS custom-property name, not JSON insertion order, so identical token content produces byte-stable CSS for deploy hashes and diffs.
- Nested token groups flatten by path (`color.semantic.success` → `--pwp-color-semantic-success`).
- Existing starter-token aliases remain compatibility mappings: `colors` → `color`, `font_families` → `font-family`, `font_sizes` → `font-size`, `font_weights` → `font-weight`, `line_heights` → `line-height`, `radii` → `radius`, `shadows` → `shadow`, and `animations` → `animation`.

Theme-specific aliases may exist, but the PWP namespace must always be present for compatibility.

### 3. Module manifest

Each module gets a machine-readable contract:

```json
{
  "id": "hero",
  "component": "Hero.astro",
  "slots": ["media", "actions"],
  "propsSchema": "schemas/modules/hero.schema.json",
  "editableFields": ["eyebrow", "title", "body", "ctas", "image"],
  "variants": ["default", "split-media", "centered", "minimal"],
  "a11yRules": ["single-h1-per-page", "image-alt-required"],
  "contentSafetyRules": ["no-unsupported-certification-claims"]
}
```

### 4. EmDash edit map

EmDash needs explicit field ownership so humans can edit safely:

```json
{
  "contentType": "landingPage",
  "route": "/msp-feeder-bin/",
  "blocks": [
    {
      "blockId": "hero",
      "component": "Hero",
      "fields": {
        "title": { "type": "string", "maxLength": 90, "editable": true },
        "body": { "type": "portableText", "editable": true },
        "ctas": { "type": "array", "editable": true }
      }
    }
  ],
  "lockedFields": ["complianceClaims", "legalName", "schemaOrgType"]
}
```

## Base module library

PWP should start with a small but complete module set. Better seven reliable modules than fifty hallucinated snowflakes.

| Module | Purpose | Variants |
|---|---|---|
| `BaseLayout` | document shell, metadata, global theme | default, article, landing |
| `SiteHeader` | nav, logo, CTA | simple, mega-lite, transparent-on-hero |
| `SiteFooter` | contact/legal/social/service area | simple, multi-column |
| `Hero` | primary page promise | split-media, centered, service-detail, local-SEO |
| `WarningStrip` | compliance/trust boundary | warning, info, proof-note |
| `CardGrid` | services/features/audiences | 3-up, 4-up, icon-card, linked-card |
| `TrustPanel` | dark/proof section | numbered, logo-strip, certificate-aware |
| `ProcessTimeline` | steps/workflow | horizontal, vertical, compact |
| `SplitSection` | compare/good-fit/caution | text-list, image-text, good-vs-restricted |
| `LeadCapture` | form/CTA/contact | form, mailto, booking embed, CRM handoff |
| `FAQ` | objections/SEO | accordion, static list |
| `LocalSEOBlock` | service areas + schema | mapless, city-grid |
| `TestimonialGrid` | social proof | quote cards, case-study teaser |
| `PricingOrPackages` | offers/tiers | card tiers, quote-based |
| `RichTextPage` | policy/about/resources | prose, doc-like |

## Theme families

Initial comprehensive in-ecosystem families:

| Family | Use case | Example style |
|---|---|---|
| `trust-light` | B2B, compliance, local professional services | Sentinel ITAD |
| `tourism-conversion` | tours, bookings, local experience pages | Active Oahu-style sites |
| `expert-authority` | consultants, clinics, coaches, professional authority | service + proof heavy |
| `saas-product` | software/tools, waitlists, demos | product-led modules |
| `portfolio-studio` | creative/project showcases | visual grids/case studies |
| `local-service` | trades, home services, logistics | service-area SEO |

Every family shares the same core module contracts. Family differences live in tokens, variants, content defaults, and module ordering — not incompatible component APIs.

## AI-first workflow

### Agent input contract

Agents should receive:

```json
{
  "clientProfile": {},
  "contentBrief": {},
  "designBrief": {},
  "constraints": {
    "themeFamily": "trust-light",
    "mustUseModules": ["Hero", "CardGrid", "LeadCapture"],
    "lockedClaims": ["certified data destruction", "R2v3 certified"]
  },
  "acceptanceCriteria": {
    "buildCommand": "npm run build",
    "themeCheck": "npm run check:theme",
    "a11y": "npm run test:a11y",
    "visual": "npm run test:visual"
  }
}
```

Agents should not be asked to “make a nice page.” That is how you get interpretive dance in CSS.

### Agent output contract

Agents must produce:

- changed content records,
- changed theme/module files if needed,
- updated manifest if theme behavior changed,
- updated docs if a module/variant was added,
- verification evidence.

## Verification architecture

### Required gates per theme package

| Gate | Tooling | Blocks merge? |
|---|---|---:|
| manifest schema | JSON Schema | yes |
| token schema | JSON Schema / Style Dictionary parser | yes |
| Astro typecheck/build | `astro check` / `astro build` | yes |
| module contract tests | Vitest or pytest fixture parser | yes |
| content schema | Zod / JSON Schema | yes |
| accessibility | Playwright + axe-core | yes for new modules |
| visual regression | Playwright screenshots / Storybook snapshots | yes for redesigns |
| performance | Lighthouse CI budgets | yes for production theme release |
| SEO/link/sitemap | crawler + XML parser | yes |
| compliance/safety lint | domain-specific copy rules | yes for regulated claims |

### Theme compatibility matrix

PWP should maintain a matrix:

```text
Theme family × Module × Variant × Content type × Deployment target
```

A theme is “PWP-compatible” only if it passes the matrix for all required cells.

## Phased delivery plan

### Phase 0 — Align on contracts and avoid more drift

**Goal:** freeze the target architecture and stop ad-hoc theme work.

Deliverables:

- `pwp-theme.schema.json`
- `pwp-module.schema.json`
- `pwp-token.schema.json`
- `pwp-emdash-map.schema.json`
- Theme package directory convention.
- Theme acceptance checklist.
- “No one-off page CSS” policy.

Verification:

- schema fixtures validate good/bad examples,
- docs reference existing PWP ingest/distill/deploy pipeline,
- no runtime behavior changed.

Exit criteria:

- Any agent can identify what a valid PWP theme package must contain.

### Phase 1 — Design token foundation

**Goal:** build the token compiler and tenant/theme override model on proven token patterns.

Deliverables:

- W3C-style token schema.
- Style Dictionary-compatible transform layer.
- `tokens.json` → CSS custom properties compiler.
- Tenant override merge semantics:
  1. PWP defaults,
  2. theme family defaults,
  3. tenant/client overrides,
  4. page-level controlled overrides.
- Token provenance metadata.
- Backward-compatible adapter for current `tests/test_pwp_design_tokens.py` assumptions.

Verification:

- existing PWP design token tests still pass,
- invalid tokens rejected,
- tenant overrides do not mutate defaults,
- generated CSS is deterministic.

Exit criteria:

- Any theme can swap color/type/spacing/radius without editing component source.

### Phase 2 — Astro module contract library

**Goal:** create stable modules with typed props and variants.

Deliverables:

- Core module library:
  - `BaseLayout`
  - `SiteHeader`
  - `SiteFooter`
  - `Hero`
  - `CardGrid`
  - `TrustPanel`
  - `ProcessTimeline`
  - `SplitSection`
  - `LeadCapture`
  - `FAQ`
  - `LocalSEOBlock`
- TypeScript prop interfaces.
- Module JSON schemas.
- Example fixtures per module.
- Component docs with allowed variants.

Verification:

- Astro build passes,
- fixtures render,
- axe-core baseline passes,
- screenshots generated for every module variant.

Exit criteria:

- Agents can assemble pages from module manifests without inventing markup.

### Phase 3 — EmDash content/editing integration

**Goal:** expose safe human-editable fields while protecting system/legal/compliance fields.

Deliverables:

- EmDash content-type definitions for:
  - landing page,
  - service page,
  - location page,
  - article/resource,
  - policy page,
  - offer/package page.
- EmDash field maps per module.
- Locked-field rules.
- Portable Text rendering adapter.
- Edit-preview loop for Cloudflare staging.

Verification:

- editable fields round-trip through EmDash/local SQLite,
- locked fields cannot be edited via UI/API fixtures,
- Astro pages render EmDash content consistently,
- content migrations are idempotent.

Exit criteria:

- Michael can edit specific copy/CTA/image fields without changing theme code.

### Phase 4 — Theme package registry

**Goal:** turn themes into distributable, versioned PWP ecosystem packages.

Deliverables:

- Local theme registry:

```text
plugins/pwp/themes/
  trust-light/
  tourism-conversion/
  expert-authority/
```

- Registry index:

```json
{
  "themes": [
    { "id": "pwp.theme.trust-light", "version": "0.1.0", "path": "themes/trust-light/theme.json" }
  ]
}
```

- Version compatibility resolver.
- Theme install command:

```bash
pwp theme install trust-light --tenant sentinelitad
```

- Theme diff command:

```bash
pwp theme diff --from trust-light@0.1.0 --to trust-light@0.2.0
```

Verification:

- install fixture creates expected Astro project structure,
- upgrade fixture preserves tenant overrides,
- invalid theme package rejected.

Exit criteria:

- PWP can install/upgrade a theme without hand-copying files.

### Phase 5 — Page synthesis pipeline

**Goal:** connect PWP ingest output to module-based Astro pages.

Deliverables:

- Theme classifier:
  - maps business type + design brief + conversion needs to theme family.
- Page planner:
  - maps content brief to route manifest and module sequence.
- Content synthesizer:
  - creates EmDash records / content JSON from client profile.
- SEO/schema generator:
  - Organization, LocalBusiness, Service, FAQ, Breadcrumb schemas.
- Safety linter:
  - rejects unsupported regulated claims by domain.

Verification:

- known fixtures generate stable route/module plans,
- malformed input produces actionable missing-field report,
- generated pages pass build/a11y/link checks.

Exit criteria:

- PWP can turn a completed intake package into a coherent Astro site scaffold.

### Phase 6 — Visual and accessibility regression system

**Goal:** make theme quality measurable.

Deliverables:

- Playwright test harness.
- Axe-core accessibility tests.
- Screenshot baselines per theme/module/variant.
- Lighthouse CI budgets:
  - Performance ≥ 90,
  - Accessibility ≥ 95,
  - Best Practices ≥ 95,
  - SEO ≥ 95 for production-ready themes.
- Mobile/tablet/desktop viewport matrix.

Verification:

- CI fails on visual drift unless approved,
- CI fails on color contrast regressions,
- CI fails on broken nav/sitemap links.

Exit criteria:

- Theme changes are safe enough for multiple agents to modify over time.

### Phase 7 — PWP deployment, rollback, and provenance

**Goal:** integrate themes into the existing PWP deploy/state model.

Deliverables:

- Theme provenance in deployment manifest:

```json
{
  "themeId": "pwp.theme.trust-light",
  "themeVersion": "0.1.0",
  "tokenHash": "sha256:...",
  "moduleHash": "sha256:...",
  "contentHash": "sha256:..."
}
```

- PWP run-state records include theme metadata.
- Rollback can restore:
  - prior artifact,
  - prior theme version,
  - prior token override set.
- Cloudflare deploy idempotency preserved.

Verification:

- rollback tests restore previous theme artifact,
- deploy skip works when commit/theme hashes match,
- audit log records theme version.

Exit criteria:

- A bad theme release can be reverted without archaeology.

### Phase 8 — Theme SDK and contributor model

**Goal:** open the ecosystem without letting random themes break PWP.

Deliverables:

- `create-pwp-theme` scaffold.
- Theme author guide.
- Module author guide.
- Compatibility test suite published as a package.
- Example themes:
  - `trust-light`,
  - `tourism-conversion`,
  - `saas-product`.
- Community submission checklist.

Verification:

- a fresh scaffold passes all checks,
- contributed theme fixtures can run without private infrastructure,
- no secret/environment dependency in base theme tests.

Exit criteria:

- External contributors can build compatible themes without internal tribal knowledge.

### Phase 9 — AI agent specialization and autopilot

**Goal:** route theme work to the right agents with clear responsibilities.

Agent lanes:

| Lane | Responsibility |
|---|---|
| content strategist | map client profile to page/module plan |
| design/theme agent | token selection, variants, visual consistency |
| Astro implementation agent | component/page implementation |
| accessibility verifier | a11y and semantic checks |
| SEO/schema agent | structured data, metadata, sitemap |
| deploy agent | Cloudflare deploy, DNS, rollback metadata |
| reviewer | diff review, theme contract enforcement |

Deliverables:

- task templates per lane,
- prompt packs referencing module/schema contracts,
- automatic Linear issue generation from phase outputs,
- verifier artifacts attached to each issue.

Verification:

- generated issues include exact files/contracts/tests,
- agents cannot mark done without verification output,
- peer review catches contract drift.

Exit criteria:

- PWP reliably produces consistent websites through swarm execution.

## Implementation sequence summary

| Phase | Name | Outcome |
|---:|---|---|
| 0 | Contracts | everyone knows what a PWP theme is |
| 1 | Tokens | theming is centralized and override-safe |
| 2 | Modules | pages are assembled from standard Astro blocks |
| 3 | EmDash | humans can edit safe fields |
| 4 | Registry | themes install/upgrade as packages |
| 5 | Synthesis | intake turns into module plans/content records |
| 6 | QA matrix | visual/a11y/perf drift is caught |
| 7 | Deploy/provenance | releases are traceable and rollback-safe |
| 8 | SDK/community | external themes can join the ecosystem |
| 9 | Agent autopilot | swarm can build with consistent results |

## First reference implementation

Use the Sentinel ITAD `sentinelitad.com` work as the first “trust-light” reference:

- light global theme,
- scoped dark trust panel,
- compliance-safe copy boundaries,
- Astro-ready modules,
- EmDash-gated content schema,
- theme verification script.

Then extract it into a generic `trust-light` PWP theme package by removing business-specific copy and replacing Sentinel facts with fixtures.

## Definition of bullet-proof

The system is “bullet-proof” when:

- fresh theme scaffold passes without manual patching,
- three different theme families pass the same compatibility suite,
- client-specific overrides survive theme upgrades,
- agents can add a page using only module/content contracts,
- EmDash edits cannot corrupt locked compliance/system fields,
- visual/a11y/performance regressions fail CI,
- deployment provenance records theme/content/token hashes,
- rollback restores prior known-good artifact and theme metadata,
- documentation is sufficient for a new agent or external contributor to build a compatible theme.

That is the bar. Anything less is just another template folder with better branding.

## Immediate next implementation tasks

1. Add `schemas/pwp-theme.schema.json`, `schemas/pwp-module.schema.json`, `schemas/pwp-token.schema.json`, and `schemas/pwp-emdash-map.schema.json`.
2. Extract Sentinel’s theme docs/components into a generic `trust-light` fixture package.
3. Add a `pwp theme validate <path>` command.
4. Add module fixture rendering tests for Astro components.
5. Add Playwright + axe-core visual/a11y checks for the fixture package.
6. Add theme provenance fields to PWP deployment/run-state records.
7. Extend PWP distiller to create design-system/theme tasks with explicit module contracts and verification commands.
