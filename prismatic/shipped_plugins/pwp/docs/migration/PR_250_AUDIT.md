# PR #250 audit against current `main`

**Issue:** GRO-4153
**Audited at:** 2026-07-23T05:08Z
**Scope:** source-material review only. This audit does not merge, close, or
cherry-pick PR #250, and it does not claim a Prismatic Engine cutover.

## Exact refs and comparison context

| Item | Value |
| --- | --- |
| Source repository | `mbgulden/prismatic-engine` |
| Extraction freeze requested by the parent plan | `7ba0716ce1027c0e9cd8741dd548cf733508ecf3` |
| Freeze tree | `9ff226bcf74aa03139b1fecf8bb0e81fc5da7371` |
| Current `origin/main` audited | `841a5157a1f6a308caed1eb11f4c45973673e11c` |
| PR | [#250](https://github.com/mbgulden/prismatic-engine/pull/250), open and conflicting |
| PR head | `af2c447c93c9643bcbdddb06baa084d381e4ab68` (`ned/GRO-3711-final`) |
| PR base branch | `deploy-fresh` |
| PR/current-main merge base | `60fb4f2ad0d399bfb0108096893ffbda68e1b250` |
| Related canonical repair | [#363](https://github.com/mbgulden/prismatic-engine/pull/363), merged as `2a4990088ff7ce25733704e1670fa538d0d67179` |
| Current PWP location | `prismatic/shipped_plugins/pwp/` (the old `plugins/pwp/` tree was relocated by `841a5157`) |

PR #250 has one commit (`af2c447`, dated 2026-07-14) and GitHub reports it
as `CONFLICTING`. Its branch introduces a repository-root `scripts/pwp` shim
as well as the PWP paths below. The shim is recorded separately because it is
outside this PWP-path inventory.

## Classification key

- **Superseded** — the exact PR blob is already present in current `main` at
  the relocated shipped-plugin path.
- **Later-feature candidate** — useful PWP-domain work, but must be ported
  and independently tested in the standalone repository; do not cherry-pick
  the stale branch.
- **Stale/unsafe** — branch-era documentation or coupling that is not safe to
  carry forward as-is.
- **Separate PE integration** — a PE-owned integration surface, not a
  standalone PWP-domain import.

## Every unique `plugins/pwp/` path from PR #250

| PR #250 path | Classification | Current-main evidence / disposition |
| --- | --- | --- |
| `plugins/pwp/docs/pwp-ai-theme-system-master-plan.md` | Stale/unsafe | No exact blob remains. Its PR-era `plugins/pwp` references predate relocation; use only as historical crawler-gate context, not as an extraction source. |
| `plugins/pwp/schemas/pwp-emdash-map.schema.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/schemas/pwp-emdash-map.schema.json`. |
| `plugins/pwp/schemas/pwp-module.schema.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/schemas/pwp-module.schema.json`. |
| `plugins/pwp/schemas/pwp-theme.schema.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/schemas/pwp-theme.schema.json`. |
| `plugins/pwp/schemas/pwp-token.schema.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/schemas/pwp-token.schema.json`. |
| `plugins/pwp/site_crawler.py` | Later-feature candidate | No current-main blob. It is stdlib-only generated-site validation and belongs in a later standalone PWP feature slice after package-namespace/resource tests. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/emdash/fields.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/emdash/fields.json`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/modules/hero.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/modules/hero.json`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/modules/lead-capture.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/modules/lead-capture.json`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/schemas/modules/hero.schema.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/schemas/modules/hero.schema.json`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/schemas/modules/lead-capture.schema.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/schemas/modules/lead-capture.schema.json`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/components/Hero.astro` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/components/Hero.astro`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/components/LeadCapture.astro` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/components/LeadCapture.astro`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/components/index.ts` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/components/index.ts`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/content.config.ts` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/content.config.ts`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/layouts/BaseLayout.astro` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/layouts/BaseLayout.astro`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/styles/theme.css` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/src/styles/theme.css`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/theme.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/theme.json`. |
| `plugins/pwp/tests/fixtures/pwp_theme/valid_theme/tokens/tokens.json` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/fixtures/pwp_theme/valid_theme/tokens/tokens.json`. |
| `plugins/pwp/tests/test_site_crawler.py` | Later-feature candidate | Tests the unported crawler only; port with `site_crawler.py` and adapt imports to the standalone namespace. |
| `plugins/pwp/tests/test_theme_diff.py` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/test_theme_diff.py`. |
| `plugins/pwp/tests/test_theme_validator.py` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/tests/test_theme_validator.py`. |
| `plugins/pwp/theme_diff.py` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/theme_diff.py`. |
| `plugins/pwp/theme_validator.py` | Superseded | Exact blob at `prismatic/shipped_plugins/pwp/theme_validator.py`. |

## Related non-PWP path and boundary

`script/pwp` (the actual PR path is `scripts/pwp`) is **requires separate PE
integration work**. It prepends the repository root to `sys.path` and imports
`plugins.pwp.*`, so it is an explicit monorepo CLI shim rather than portable
PWP domain code. A later PE adapter can target a released standalone PWP
package, but this extraction must not copy the shim into the standalone repo.

## Audit commands and result

```text
COMMAND=git fetch origin main refs/pull/250/head; git diff --name-status origin/main...origin/pr-250 -- plugins/pwp/; blob-to-current-main path comparison; gh pr view 250/363 readback
RESULT=PASS
SCOPE=PR #250 source-material audit against current main
AD_HOC_OR_CANONICAL=ad-hoc targeted audit
NOT_CLAIMING=No stale-PR merge/close/cherry-pick, PE Core integration, monorepo removal, deployment, or production cutover.
```

**Next extraction action:** Phase 1.6 should incorporate this audit into the
full provenance and source-file manifest, using the current shipped-plugin
location as the extraction input and treating the crawler as an explicit,
separately reviewed later-feature candidate.
