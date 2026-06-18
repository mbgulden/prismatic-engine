# Journal Continuity Audit — Prismatic Engine Spec

## Purpose

The Journal Continuity Audit is the Prismatic Engine continuity layer for catching unfinished work, dropped decisions, strategic gaps, and agent follow-through failures that are visible in journals/sessions but not yet enforced in Linear, cron, GitHub, or active workflows.

This exists because observation is not enough. A journal entry that says "we should do X" must either become an explicit tracked action, be intentionally rejected, or be marked as informational context. The Engine should close the loop.

## Engine Role

The audit is a serial, evidence-first workflow managed by the orchestrator and executed with AGY research/audit support.

- **Fred:** owns synthesis, sequencing, backlog creation, and governance.
- **AGY:** performs crack audits against journals/sessions/reports in read-only mode.
- **Linear:** stores sequenced work and gates downstream steps with labels.
- **Cron:** schedules monthly continuity checks and may create a fresh audit control issue.
- **Autobot:** receives machine/system reports. Fred's Telegram feed receives only human-relevant decisions or critical alerts.

## Source Inventory

Canonical source classes:

- Daily/inbox/weekly journals.
- Hermes session logs.
- Existing Linear project/issue/comment state.
- GitHub PRs and deployment history when relevant.
- Cron outputs, watchdog logs, and agent dispatch logs.
- Project registries and venture source-of-truth files.

The initial implementation lives in the orchestrator profile and Hermes-Research repo:

- Plan: `/home/ubuntu/work/Hermes-Research/docs/journal-continuity-audit/README.md`
- Sequence manifest: `/home/ubuntu/work/Hermes-Research/docs/journal-continuity-audit/workflow-sequence.json`
- Initial inventory: `/home/ubuntu/work/Hermes-Research/reports/journal-continuity-audit/initial/`
- Monthly cron script: `/home/ubuntu/.hermes/profiles/orchestrator/scripts/monthly_journal_continuity_audit.py`

This spec defines the portable Prismatic Engine contract so the pattern can be reused outside Michael's current Hermes profile.

## Required Workflow Shape

Every continuity audit must use a manifest compatible with `specs/sequenced-agent-workflow.schema.json`.

Minimum phases:

1. **Control gate** — one parent/control issue owns scope, month/window, source bounds, and activation state.
2. **Inventory** — enumerate source files/sessions/time ranges before analysis.
3. **Crack audit** — inspect source material for unresolved commitments, stale strategic threads, false-Done work, and missing enforcement loops.
4. **Synthesis** — Fred classifies findings into: act now, backlog, delegate, reject/close, or informational memory.
5. **Backlog creation** — create/update Linear issues with clear acceptance criteria and agent labels.
6. **Recurrence verification** — confirm the monthly cron or equivalent scheduler exists, is active, and routes output correctly.

Only one agent-executable phase should be active at a time. Downstream issues stay unlabeled until the upstream gate passes. This prevents agents from processing the workflow out of order.

## Finding Classification

Each finding must include:

- `source_ref`: file/session/issue/comment path or URL.
- `quote_or_evidence`: enough context to verify the finding.
- `venture_or_system`: e.g. `active-oahu-tours`, `active-oahu`, `darius-star`, `hd-engine`, `prismatic-engine`, `hermes-infra`.
- `identity_keys`: explicit identifiers for domain, repo, deployment, Linear project, and owner when known.
- `status`: `untracked`, `tracked_stale`, `false_done`, `blocked`, `rejected`, `informational`, or `complete`.
- `recommended_action`: concrete next action or explicit no-action rationale.

## Identity & Domain Integrity Rule

Do not infer that similarly named brands, domains, repos, deployments, or Linear projects are the same entity.

The continuity audit must carry explicit identity keys through every finding and task:

- Public domain(s).
- GitHub repo(s).
- Local checkout path(s).
- Cloudflare Pages project or deployment target.
- Linear project ID/name.
- Agent lane/owner.

Example distinction:

- `activeoahu.com` and `activeoahutours.com` are different properties.
- A repo named `active-oahu-tours-mirror` may cover the Tours mirror but must not be assumed to cover `activeoahu.com` unless DNS/deployment evidence proves it.
- Monitoring, PR review, link checks, and SEO/content tasks must name the exact domain/property being audited.

If identity evidence is missing, the finding status should be `blocked` with `recommended_action: verify identity mapping` rather than silently merging properties.

## File Reference Resolution Rule (linkable Hermes artifacts)

Any local file path Fred (or any other agent) mentions in conversation must be reachable by Michael as a clickable, Access-protected Hermes URL — never a raw disk path and never a Telegram attachment waiting for follow-up.

Standard path:

- Sub-hostname: `https://files.growthwebdev.com`
- Ingress: `files.growthwebdev.com -> http://127.0.0.1:9120` on tunnel `4a6097ff-dfcb-45f2-a856-3d967a9c798b`.
- Service: a profile-safe FastAPI artifact publisher (NOT the pipx-managed Hermes dashboard static dir).
- Access: self-hosted app, 720h session, allow authenticated users.
- Workspaces (allowlist, no arbitrary FS reads): `published/`, `hermes-research-reports/`, `prismatic-engine/`, `agentic-swarm-ops/`.
- Safety: blocklist on names (`.env`, `.key`, `.pem`, `.db`, `.sqlite`, `credentials`, `id_rsa*`, `state.json`, `auth.json`).
- CLI: `hermes-publish <source> [--workspace LABEL] [--rel REL]` copies the file/dir into the right workspace and prints the Cloudflare URL.
- Post-processor: `hermes-reply` (in `~/.local/bin/`, symlinked into every profile) scans reply text and rewrites `/home/...` paths to clickable URLs.
- Portable skill: `portable-skills/hermes-artifact-publisher/SKILL.md` is the canonical contract every agent can `skill_view` for context.

When any agent mentions a local path in a Telegram reply, they must:

1. Run `hermes-publish <path>` (or `hermes-reply` to auto-rewrite a multi-path draft).
2. Replace the local path in the reply with the resulting `https://files.growthwebdev.com/raw/<workspace>/<rel>` link.
3. If publish fails, say so explicitly and attach the file as a fallback.
4. Refuse to bypass the safety blocklist without explicit user confirmation.

This replaces the old workaround of copying files into the Hermes dashboard's pipx-installed static directory, which is wiped on Hermes updates. The full reference implementation, verification gates, and pitfalls live in `specs/file-reference-resolution.md`.

## Routing Rules

- Machine-generated reports, recurring monitor outputs, and agent boilerplate go to Autobot or local logs.
- Fred's Telegram feed receives only strategic summaries, direct decisions, revenue-impacting alerts, or critical failures requiring Michael's attention.
- LLM-driven cron jobs should not be `deliver: local` unless another job consumes the output.
- Script-only jobs should usually be `deliver: local` and write their own logs/state files.

## Verification Gates

Before marking a continuity audit complete:

1. Source inventory file exists and includes counts/time bounds.
2. Crack-audit output exists with evidence references.
3. Synthesis output exists with classification decisions.
4. Any accepted actions have Linear issues or direct implementation artifacts.
5. Rejected/no-action findings include rationale.
6. Monthly recurrence exists and is active, or an explicit reason says recurrence is not desired.
7. Identity-sensitive findings include domain/repo/deployment separation.

## Non-Goals

- Do not dump raw journals into Linear.
- Do not create busywork from every idea.
- Do not route repetitive system noise to Michael.
- Do not allow an LLM to claim cleanup side effects happened without deterministic script/tool verification.

## Portable Engine Integration Notes

The current orchestrator implementation is profile-specific. To port into the public Prismatic Engine package later:

- Extract the inventory builder into a config-driven module.
- Keep Hermes paths, Telegram tokens, and Linear project IDs out of engine core.
- Represent source roots, domain identity maps, and routing targets as config.
- Keep cron registration outside core; engine code should expose runnable commands/functions.
