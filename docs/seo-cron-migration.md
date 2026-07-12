# SEO cron migration inventory

This document captures the SEO automation moved out of profile-local Hermes cron definitions and into the Prismatic Engine native cron registry.

## Migrated in this slice

| Native cron ID | Previous Hermes job/script | Schedule | PE command | Notes |
|---|---|---:|---|---|
| `seo.ubersuggest-token-refresh` | `4356ea55909b` / `pwp_ubersuggest_refresh.py` | `0 3 * * *` | `python3 scripts/pwp credentials refresh ubersuggest` | PWP credential provider is the canonical token rotation path. |
| `seo.aot-weekly-rankings` | `ce817dba90d3` / `kpi_tracker.py` | `0 4 * * 1` | `python3 scripts/seo/aot_kpi_tracker.py` | Pulls AOT + competitor keyword/ranking snapshots. Depends on token refresh. |
| `seo.aot-competitor-velocity` | `f9e8d6319b72` / `competitor_velocity.py` | `0 6 * * 0` | `python3 scripts/seo/competitor_velocity.py` | Monitors competitor top-page/content movement. Depends on token refresh. |
| `seo.aot-full-sweep` | profile-local `seo_full_sweep.py` | `manual` | `python3 scripts/seo/seo_full_sweep.py` | Saved as deactivated/manual by default for on-demand overnight competitive sweeps. |

## Still worth wiring next

These are SEO-adjacent capabilities in Kai's skills/references but not yet represented as PE-native scheduled jobs:

1. **Google Search Console own-site truth pull** — periodic query/page export for `sc-domain:activeoahutours.com` to pair with Ubersuggest competitor intel.
2. **GSC + Ubersuggest counter-content brief generator** — turns competitor territory alerts plus GSC own-site data into content briefs.
3. **Internal link graph / orphan page audit** — static crawl and link graph report, likely weekly or on deploy.
4. **Structured data / schema drift audit** — validates JSON-LD, FAQ/HowTo/Product/LocalBusiness coverage, and AI/GEO quick-answer blocks.
5. **Sitemap / Search Console submission verification** — should stay gated on Google API auth and may be manual or post-deploy instead of recurring.
6. **Lighthouse SEO/A11y monitor** — already conceptually AOT governance, but belongs as a PE-native monitor if it should outlive Hermes.

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
