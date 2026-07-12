# SEO cron migration inventory

This document captures the SEO automation moved out of profile-local Hermes cron definitions and into the Prismatic Engine native cron registry.

## Migrated in this slice

| Native cron ID | Previous Hermes job/script | Schedule | PE command | Notes |
|---|---|---:|---|---|
| `seo.ubersuggest-token-refresh` | `4356ea55909b` / `pwp_ubersuggest_refresh.py` | `0 3 * * *` | `python3 scripts/pwp credentials refresh ubersuggest` | PWP credential provider is the canonical token rotation path. |
| `seo.aot-weekly-rankings` | `ce817dba90d3` / `kpi_tracker.py` | `0 4 * * 1` | `python3 scripts/seo/aot_kpi_tracker.py` | Pulls AOT + competitor keyword/ranking snapshots. Depends on token refresh. |
| `seo.aot-competitor-velocity` | `f9e8d6319b72` / `competitor_velocity.py` | `0 6 * * 0` | `python3 scripts/seo/competitor_velocity.py` | Monitors competitor top-page/content movement. Depends on token refresh. |
| `seo.aot-full-sweep` | profile-local `seo_full_sweep.py` | `manual` | `python3 scripts/seo/seo_full_sweep.py` | Saved as deactivated/manual by default for on-demand overnight competitive sweeps. |

## Extended native SEO crons

| Native cron ID | Schedule / trigger | PE command | Notes |
|---|---:|---|---|
| `seo.gsc-query-page-export` | `30 5 * * *` | `python3 scripts/seo/gsc_query_page_export.py` | Daily own-site GSC query/page source-of-truth export for `sc-domain:activeoahutours.com`. |
| `seo.aot-counter-content-briefs` | `30 7 * * 0` | `python3 scripts/seo/gsc_ubersuggest_countercontent.py` | Weekly after competitor velocity; pairs GSC opportunity rows with Ubersuggest competitor baseline pages. |
| `seo.aot-internal-link-orphan-audit` | `15 8 * * 1` | `python3 scripts/seo/internal_link_orphan_audit.py` | Weekly static site link graph, orphan pages, missing H1/meta/schema, broken internal links. |
| `seo.aot-structured-data-drift-audit` | `45 8 * * 1` | `python3 scripts/seo/structured_data_drift_audit.py` | Weekly JSON-LD parser/type inventory; exits non-zero only for parse errors. |
| `seo.aot-sitemap-gsc-verification` | `manual` / post-deploy gated | `python3 scripts/seo/sitemap_gsc_verification.py` | Deactivated by default because GSC sitemap verification is auth/post-deploy gated. |
| `seo.aot-lighthouse-seo-a11y-monitor` | `30 9 * * 1` | `python3 scripts/seo/lighthouse_seo_a11y_monitor.py` | Weekly rendered Lighthouse SEO/A11y/Best Practices monitor with static fallback artifact when Lighthouse/Chrome is unavailable. |

## Remaining future wiring

These are now intentionally follow-up enhancements rather than missing native cron definitions:

1. **Dashboard surfacing of report artifacts** — link latest GSC/link/schema/Lighthouse markdown artifacts directly from the Native Crons tab.
2. **Post-deploy trigger integration** — call `seo.aot-sitemap-gsc-verification` and Lighthouse monitor from the deployment pipeline after production deploys.
3. **Linear issue creation** — turn counter-content briefs and audit deltas into Linear drafts/issues when Linear API quota is available.
4. **Private business repo publishing** — mirror sensitive competitor reports into `active-oahu-business` instead of keeping only local PE state.

## Native cron lifecycle semantics

- **Pause**: cron remains in the active queue but does not run. Use for temporary holds.
- **Deactivate**: cron moves out of the queue and is saved for later. Use when a job should not participate until reactivated.
- **Delete**: cron is tombstoned and hidden from the default list. Dashboard requires a secondary modal with `Deactivate`, `Delete — I’m sure`, `Cancel`, and an X close affordance.

## Portable-state goal

PE-native cron state lives under `PRISMATIC_NATIVE_CRON_STORE`, defaulting to:

```text
$PRISMATIC_STATE_DIR/native_crons.json
```

The registry can export active queued jobs as system crontab lines:

```bash
python3 -m prismatic.native_crons export-crontab --include-header
```

Install/update the current user's managed PE-native cron block with:

```bash
python3 scripts/install_native_crons.py
```

Preview without writing crontab:

```bash
python3 scripts/install_native_crons.py --dry-run
```

That keeps the schedule definitions portable across hosts with or without Hermes/OpenClaw installed.
