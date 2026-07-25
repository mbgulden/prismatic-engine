# Changelog

All notable public-facing changes will be tracked here.

The project is currently alpha. Dates use UTC.

## Unreleased

### Release engineering

- Public release engineering layer: release process docs, release checklist, migration notes, upgrade guide, stable CLI entrypoint reference, sample config, bootstrap script, Dockerfile, devcontainer, release smoke, and release readiness check.
- Added stable public documentation entrypoints for launch, plugin developer quickstart, security, and contributing, and made the public launch smoke assert they exist.
- Added canonical North Star, dashboard-primary touchpoint, and OKF evidence-map documentation so public launch, plugin lifecycle, Telegram/headless workflow, and dashboard-first product direction stay aligned.

### Dashboard UX

- Public-polished plugin dashboard layer: first-run empty state, onboarding hints, copyable CLI commands, docs links, error explanations, health cards, detail drawer, job timeline, artifact inventory, approval controls, and static visual QA.
- Added a normalized cross-plugin audit event stream (`GET /api/plugins/audit-events`) plus dashboard visibility for job and artifact lifecycle events.

### PWP reference plugin

- PWP is now the canonical full-lifecycle reference plugin with a credential-free lifecycle demo covering connect, durable job, artifact/provenance registration, approval-before-publish enforcement, export history, dashboard lifecycle history, and safe disconnect that preserves artifacts.

### Changed

- Added the portable Antigravity/AGY workspace customization bundle, explicit installer/status/uninstaller/audit CLI, packaged wheel resources, and task/plan/result templates while preserving canonical Prismatic admission and review gates.
- Hardened customization management with whole-plan preflight, atomic capture-and-verify mutation, no-replace creation, rollback-safe transactions, exact current-bundle manifest trust, no-follow collision-proof backups, non-regular-file rejection, and descriptor-anchored structural audits that reject symlink roots and never return raw frontmatter values.
- CI now runs a supported Python matrix and release smoke/readiness checks.
- Publish workflow now verifies tags, checks distributions, uploads artifacts, and creates build provenance before PyPI publication.

## [0.2.0] - 2026-07-13

### Added

- Public README quickstart and first-user path.
- `.env.example` with safe local defaults.
- Public onboarding guide.
- Public architecture overview.
- Plugin developer guide.
- Hello plugin tutorial.
- Troubleshooting guide.
- Dashboard screenshot documentation and illustrative dashboard asset.
- Contribution guide.
- Security policy.
- One-command public launch smoke test at `scripts/public_launch_smoke.py`.
- Public security readiness audit at `scripts/public_security_readiness_audit.py` and `docs/public-security-readiness.md`.

### Current plugin foundation

- Durable plugin jobs and audit events.
- Universal artifact/provenance registry.
- Generic plugin policy decisions and enforcement.
- Dashboard/API visibility for plugin governance, jobs, artifacts, and policy decisions.

### Migration notes

- See `docs/migrations.md` for the `0.1.x to 0.2.0` migration path.
