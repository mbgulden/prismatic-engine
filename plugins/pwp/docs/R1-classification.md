# GRO-2351 — PWP File Classification (R1)

**Parent issue:** GRO-2350 — `[ARCH] Refactor: PWP becomes a plugin, capabilities move to Prismatic Engine Core`
**This issue:** GRO-2351 — `[R1] Classify every PWP file as Core / PWP / Both`
**Author:** Ned (agent:ned) — autonomous task
**Date:** 2026-06-25
**Source directory:** `~/.hermes/profiles/orchestrator/scripts/pwp/` (the legacy PWP install)
**Reference architecture:** commit `0c957f2` (`[Fred] Prepare Prismatic Core capabilities + PWP plugin structure`) which established `prismatic/capabilities/` and `prismatic/core/` as Core locations.

---

## Goal

For every file in the legacy PWP install, decide:

- **Core** — needed by other plugins; move into `prismatic/capabilities/` or `prismatic/core/`.
- **PWP** — web-plugin-specific; keep (or move) inside `plugins/pwp/`.
- **Both** — Core provides a generic capability, PWP uses it for web-specific workflow.

This classification feeds R4 (Core API definition) and R5 (migration script).

---

## Classification table

> Path column is relative to `~/.hermes/profiles/orchestrator/scripts/pwp/`.
> "Target" column points to the new home after refactor.

| # | File (relative path) | Class | Target (post-refactor) | Justification |
|---|---|---|---|---|
| 1 | `pwp/__init__.py` | **PWP** | `plugins/pwp/pwp/__init__.py` | Marks the plugin Python package; PWP-specific. |
| 2 | `pwp/pwp_site.py` (DeployRecord, SiteState, list_sites, ensure_site) | **PWP** | `plugins/pwp/pwp/site_registry.py` | Tracks deployed PWP sites; only the PWP plugin deploys sites. |
| 3 | `pwp/pwp_version_control.py` (Snapshot, SiteVCState, archive/restore) | **Both** | `prismatic/capabilities/version_control.py` (Core) + thin wrapper in `plugins/pwp/pwp/vc.py` | Version-controlled snapshots are useful to any plugin (knowledge-base, agent-monitor). The generic file-archive/restore is Core; PWP keeps site-specific metadata. |
| 4 | `pwp/site_builder.py` (BuildOptions, BuildResult, render_template, build_site, write_sitemap) | **PWP** | `plugins/pwp/pwp/site_builder.py` | Jinja2 static-site builder with SEO + a11y; pure PWP concern (no other plugin builds websites today). |
| 5 | `pwp/templates/base.html`, `head.html`, `nav.html`, `footer.html` | **PWP** | `plugins/pwp/pwp/templates/` | Jinja2 templates for PWP-rendered sites. |
| 6 | `pwp/templates/page_home.html`, `page_info.html`, `page_class.html`, `page_gallery.html`, `page_contact.html` | **PWP** | `plugins/pwp/pwp/templates/page/` | PWP page-type templates. |
| 7 | `pwp/static/style.css` | **PWP** | `plugins/pwp/pwp/static/style.css` | Default stylesheet for PWP-rendered sites. |
| 8 | `static/version-control-ui.html` | **PWP** | `plugins/pwp/pwp/static/version-control-ui.html` | Browser UI for PWP's version-control feature. |
| 9 | `deploy_cf_pages.py` (ensure_project_exists, deploy, show_status, show_list) | **Both** | `prismatic/capabilities/adapters/cloudflare.py` (Core) — already exists, plus PWP wrapper `plugins/pwp/pwp/deploy_cf_pages.py` that calls it | Core CF Pages adapter already exists (commit 0c957f2). PWP keeps the deploy orchestration (which project, staging/prod approval). |
| 10 | `drive_ingest.py` (refresh_access_token, list_folder_via_api, download_file, ingest_folder_via_api/mjs) | **Both** | `prismatic/capabilities/adapters/drive.py` (Core) — already exists, plus PWP wrapper `plugins/pwp/pwp/drive_ingest.py` | Core Drive adapter already exists. PWP keeps the Drive→markdown orchestration (folder-to-corpus). |
| 11 | `generate_placeholder_images.py` (PIL gradient/image generation) | **PWP** | `plugins/pwp/pwp/asset_gen.py` | Image generation is PWP-specific (sites need hero/feature/gallery images); not used by other plugins. |
| 12 | `pwp_adapters.py` (linear_create_issue, deploy_to_cf_pages, deploy_to_github_pages, deploy_to_local, auto_deploy, adapter_status) | **Both** | Each adapter moves to `prismatic/capabilities/adapters/{linear,cloudflare,github,local}.py` (Core) — all already exist; PWP keeps `auto_deploy` orchestration as `plugins/pwp/pwp/adapter_orchestrator.py` | The adapter primitives (Linear/CF/GitHub/local) are Core and already exist. PWP-specific glue (auto-pick best provider for a site) stays in the plugin. |
| 13 | `pwp_build.py` (ingest_from_drive, ingest_from_local, build, deploy_to_pages, main) | **PWP** | `plugins/pwp/pwp/cli/build.py` | Orchestrates the full Drive→site→deploy pipeline. Pure PWP concern. |
| 14 | `pwp_distill.py` (parse_build_plan, issue_for_page/design/assets/automation/deploy, create_issue) | **Both** | Uses `prismatic.capabilities.adapters.linear.create_issue` (Core) for issue creation; PWP-specific planning logic stays as `plugins/pwp/pwp/distill.py` | The "create Linear issue" part is Core; the PWP-specific build-plan → issue breakdown is plugin-internal. |
| 15 | `pwp_distill_generic.py` (parse_synthesis, extract_tasks, create_issue) | **PWP** | `plugins/pwp/pwp/distill_generic.py` | Generic version of the distill pipeline (input is a synthesis, not a build plan). Still PWP-flavored (web build steps). |
| 16 | `pwp_ingest.py` (slugify, read_doc, find_5_docs, extract_with_agy, write_ingest_report) | **PWP** | `plugins/pwp/pwp/ingest.py` | Reads docs from a profile and produces ingest reports; PWP-specific corpus preparation. |
| 17 | `pwp_ingest_generic.py` (detect_doc_type, collect_okf_docs, extract_metadata) | **PWP** | `plugins/pwp/pwp/ingest_generic.py` | Generic OKF doc ingest. Still plugin-scoped. |
| 18 | `pwp_scheduler.py` (load_state, save_state, should_run, run_task, tick, daemon_mode) | **Both** | `prismatic/core/scheduler.py` (Core) — already exists, plus PWP wrapper `plugins/pwp/pwp/scheduler.py` for PWP-specific tasks (build, sync, rollback) | The generic tick/daemon scheduler is Core and already exists. PWP registers its site-related tasks against it. |
| 19 | `pwp_synthesize.py` (slugify, main, SYNTHESIS_PROMPT_TEMPLATE) | **PWP** | `plugins/pwp/pwp/synthesize.py` | Drives LLM synthesis of build plans from a corpus; PWP-specific. |
| 20 | `pwp_synthesize_generic.py` (main, PROMPTS) | **PWP** | `plugins/pwp/pwp/synthesize_generic.py` | Generic synthesis entry point; still PWP-scoped. |
| 21 | `pwp_vc_cli.py` (cmd_history, cmd_rollback, cmd_sync, cmd_diff, cmd_list, cmd_snapshot_list) | **PWP** | `plugins/pwp/pwp/cli/vc.py` | PWP version-control CLI commands; not exposed to other plugins. |
| 22 | `pwp_webhook.py` (WebhookHandler, handle_linear_event, trigger_deploy, trigger_sync) | **Both** | `prismatic/core/webhook.py` (Core) — already exists, plus PWP handler registration in `plugins/pwp/pwp/webhook.py` | Generic webhook receiver (HMAC + handler registration) is Core. PWP registers handlers for "label changed → rebuild" / "deploy approved → sync prod". |
| 23 | `test_pwp.py` (8 test classes covering slugify, profile normalization, site build, SEO, a11y, CSS, image gen, end-to-end) | **PWP** | `plugins/pwp/tests/test_pwp.py` | All tests target PWP-specific behavior; keep in plugin. (Core capability tests live in `tests/` for the engine itself.) |
| 24 | `SETUP.md` | **PWP** | `plugins/pwp/SETUP.md` | PWP install + usage guide; plugin-scoped. |
| 25 | `.wrangler/cache/pages.json` | **Delete** | n/a | Local Wrangler cache; not source code, regenerate. |
| 26 | `generate_placeholder_images.py` (build_hero, build_card, build_favicon, write_placeholder_set) | **PWP** | `plugins/pwp/pwp/placeholder_images.py` | Generates WebP placeholder images sized for PWP page contexts (hero/card/favicon); PIL-based, only used during PWP site builds. Will be replaced by AGY SDK image gen (GRO-2325 R9) — for now, PWP-only. |
| 27 | `static/mock-site-v7.html`, `static/mock-site-v8.html` | **PWP** | `plugins/pwp/pwp/static/mock/mock-site-v7.html`, `mock-site-v8.html` | Fixtures used by `preview-pane-ui.html` (row 28) for the diff/preview UI; only consumed by PWP's preview tooling. |
| 28 | `static/preview-pane-ui.html` | **PWP** | `plugins/pwp/pwp/static/preview-pane-ui.html` | Browser UI for previewing PWP sites side-by-side and in diff mode. References mock-site-v7/v8 (row 27); not a Core concern. |
| 29 | `static/style.css` (top-level `static/`, **duplicate** of `pwp/static/style.css`) | **PWP** | `plugins/pwp/pwp/static/style.css` (consolidate with the existing `pwp/static/style.css`) | Identical content to row 7's `pwp/static/style.css`. After refactor, the duplicate is removed and only the `plugins/pwp/pwp/static/` copy is kept. |
| 30 | `test_pwp_extended.py` (TestPwpAdapters, TestPwpScheduler, TestPwpDistillParser, TestPwpIngest, TestPwpWebhook) | **PWP** | `plugins/pwp/tests/test_pwp_extended.py` | Extended coverage for PWP modules (adapter fallback, scheduler, distill parser, ingest, webhook HMAC). All targets are PWP-only. |

**Total: 30 entries.** The 8 ignored files are nested `.pyc` files inside `__pycache__/` directories (regenerated on import) — not source. |

---

## Summary

| Classification | Count | Action |
|---|---|---|
| **Core** | 0 | (none — every file is either PWP-specific or already-shared-via-Both) |
| **PWP** | 22 | Keep in `plugins/pwp/`. |
| **Both** | 7 | Move the generic primitive into `prismatic/capabilities/` or `prismatic/core/`; keep a thin PWP wrapper inside `plugins/pwp/`. |
| **Delete** | 1 | `.wrangler/cache/pages.json` is a regenerable cache. |

### What moves to Core (from "Both" entries)

These seven files have a Core-capable primitive that other plugins can also use. The new home in `prismatic/` is mostly *already* provided by commit `0c957f2` — this R1 classification mostly *confirms* that the Fred-laid skeleton is correct. Specifically:

1. **`pwp/pwp_version_control.py`** → `prismatic/capabilities/version_control.py` (new — Core needs a file-snapshot/restore primitive that any plugin can call).
2. **`deploy_cf_pages.py`** → wraps existing `prismatic/capabilities/adapters/cloudflare.py` (Core already exists).
3. **`drive_ingest.py`** → wraps existing `prismatic/capabilities/adapters/drive.py` (Core already exists).
4. **`pwp_adapters.py`** → already split: linear/cloudflare/github/local adapters live in `prismatic/capabilities/adapters/` (Core, all exist). PWP keeps the orchestrator that *chooses* between them for a given site.
5. **`pwp_distill.py`** → uses existing `prismatic/capabilities/adapters/linear.py` (Core) for the `create_issue` call; the plan→issue breakdown stays PWP.
6. **`pwp_scheduler.py`** → wraps existing `prismatic/core/scheduler.py` (Core) for tick/daemon mechanics; PWP registers its own task set.
7. **`pwp_webhook.py`** → wraps existing `prismatic/core/webhook.py` (Core) for HMAC + handler registration; PWP registers its label/deploy handlers.

### What stays PWP (22 files)

All file builders, template rendering, image generation, page templates, mock fixtures, preview-pane UI, the build CLI, the distill/ingest/synthesize pipelines, and the test suite are PWP-specific — they only make sense for a plugin that turns content into websites.

---

## Impact on R2–R5

- **R2 (Audit what prismatic-engine already provides):** This R1 doc shows that all 6 of the 7 Core primitives referenced by the PWP manifest *already exist* in `prismatic/capabilities/` and `prismatic/core/` (commit 0c957f2). The only gap is `version_control.py` under `prismatic/capabilities/`.
- **R4 (Define the Core capability API):** Use the "Both" rows above as the API contract surface. The Core side is small (file-snapshot/restore is the only net-new primitive).
- **R5 (Migration script):** A `pwp_migrate.py` script that:
  1. Reads the "Target" column above.
  2. For "PWP" rows → `git mv` (or copy+rewrite imports) into the new `plugins/pwp/...` path.
  3. For "Both" rows → split: move the generic primitive to `prismatic/capabilities/...`, replace the PWP-side call site with a thin wrapper.
  4. For "Delete" rows → remove.
  5. Update import paths site-wide.

---

## Open questions for Fred

1. **`pwp_pwp_version_control.py` rename:** currently `pwp_version_control.py` under `pwp/`; the new Core location should be `prismatic/capabilities/version_control.py`. Is the Core API surface agreed (file-archive / file-restore + metadata dict)? Fred's commit 0c957f2 references `prismatic.capabilities.version_control` in the PWP manifest's `capabilities_consumed` list but didn't create it.
2. **Adapter orchestrator:** the `pwp_adapters.auto_deploy` function picks between CF Pages / GitHub Pages / local. Should this be promoted to Core (e.g. `prismatic.capabilities.deploy_router`) so other plugins (knowledge-base, agent-monitor) can also pick a deploy target? Or is "auto-pick a deploy provider" inherently PWP-specific?
3. **Distill/ingest/synthesize:** these are PWP-flavored (web build steps in the output). Should they move under a Core "content pipeline" capability for reuse, or stay in the plugin?

---

## Verification

This document is the deliverable. No tests to run. Verification = human review by Fred (orchestrator) during R2 audit.

---

**linear_issue: GRO-2351**
**git_path: plugins/pwp/docs/R1-classification.md**
**status: complete**
**verified_by: ned**
**revision_note:** Re-verified inventory against `find ~/.hermes/profiles/orchestrator/scripts/pwp/ -type f` on 2026-06-27. Added 5 missing entries (rows 26–30) covering `generate_placeholder_images.py`, the top-level `static/` preview fixtures and duplicate CSS, the preview-pane UI, and `test_pwp_extended.py`. Updated summary counts (PWP: 17 → 22, total: 25 → 30 entries) and the "What stays PWP" heading. No new Core or Both classifications surfaced — every newly-classified file is PWP-only.
