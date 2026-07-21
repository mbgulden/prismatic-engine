# GRO-3461 — OKF Drift & Compliance Scans Audit

> **Historical evidence — not current authority.** As-of 2026-07-05 and dependent on an external workspace snapshot. Current precedence: `docs/index.md`; machine registry: `okf/index.yaml`.

**Auditor:** Ned
**Timestamp (UTC):** 2026-07-05T21:35:18Z
**Repository/branch:** `prismatic-engine` / `ned/GRO-3461`
**Scope:** OKF drift checker availability, OKF markdown/frontmatter compliance, integration registry health, and task-readiness scoring inputs.

## Executive summary

🟡 **OKF is present, but compliance is uneven.** I scanned `/home/ubuntu/work/growthwebdev-knowledge/okf` and found **275 markdown files**. **191/275** have YAML frontmatter; **84** do not. Against a minimal `title` + `type` + `status` rule, **32 frontmatter-bearing files are missing required fields**.

🟡 **Drive drift coverage exists in data but not on the current deploy branch.** The live OKF contains **82 Drive-linked markdown files** (`drive_file_id` or `plugin_doc_id`), but the `origin/deploy-fresh` worktree for this task has **no `scripts/*drift*` checker**. A `check-drive-drift.py` script exists in the shared checkout on another local branch/WIP, but it is not available on the branch this task must ship from. That means the deployable baseline currently cannot run the advertised OKF Drive drift check.

🟡 **Integration registry needs normalization.** `/okf/integrations/` has **10 docs**; the index has a malformed table tail on the Prismatic webhook chain row and one integration doc (`agent-profile-inventory.md`) lacks frontmatter entirely. Two integration docs with frontmatter are missing `status`.

🔴 **OKF repo working tree has pre-existing drift.** `git status --short` for `growthwebdev-knowledge` shows `D okf/vision/prismatic-north-star.md`. I did not modify or restore it; it is outside Ned's write lane for this audit and should be handled by the OKF owner.

## Evidence sources inspected

| Surface | Path / command | Result |
|---|---|---|
| OKF tree | `/home/ubuntu/work/growthwebdev-knowledge/okf` | 275 markdown files across 13 top-level sections. |
| OKF git status | `git status --short` in `growthwebdev-knowledge` | Pre-existing `D okf/vision/prismatic-north-star.md`. |
| Frontmatter scan | Python markdown/frontmatter parser over OKF | 191 with frontmatter; 84 without. |
| Minimal compliance rule | `title`, `type`, `status` required when frontmatter exists | 32 files missing at least one required field. |
| Drive-link scan | `drive_file_id` or `plugin_doc_id` in frontmatter | 82 linked files. |
| Current deploy branch scripts | `/tmp/prismatic-gro3461/scripts` | No drift checker present on `origin/deploy-fresh`. |
| Shared checkout WIP | `/home/ubuntu/work/prismatic-engine/scripts/check-drive-drift.py` | Exists outside this branch; not committed to this deliverable baseline. |
| Current task specs | `/tmp/issue-batches/GRO-3461.txt` | Requests `audits/ned/`; committed report is lane-governed under `scripts/reports/`. |

## Standard drift checker audit

### Current branch state

The isolated worktree for this task was created from `origin/deploy-fresh`. In that worktree:

- `scripts/gdocs-sync.py` exists and pushes local reports into Google Docs, but it is a one-way publisher, not a drift detector.
- `scripts/gdocs-auth.py` exists for auth support.
- No file matching `*drift*` exists under `scripts/`.
- No file matching `*check-drive-drift.py` exists on this branch.

Smoke attempted:

```text
python3 scripts/check-drive-drift.py
python3: can't open file '/tmp/prismatic-gro3461/scripts/check-drive-drift.py': [Errno 2] No such file or directory
exit=2
```

### Shared checkout observation

The shared `/home/ubuntu/work/prismatic-engine` checkout does contain `scripts/check-drive-drift.py`, which scans `/home/ubuntu/work/growthwebdev-knowledge/okf`, authenticates against Google Drive, and compares local frontmatter timestamps to Drive `modifiedTime`. That file is not available on `origin/deploy-fresh` and therefore cannot be treated as a deployable control yet.

**Risk:** the OKF can have 82 Drive-linked documents but no branch-stable drift checker in the deploy baseline. If the checker is intended to be canonical, it needs to be merged into `deploy-fresh` in Ned's `scripts/` lane with a smoke test and cron/job ownership documented.

## OKF compliance scan

### Corpus distribution

| OKF section | Markdown files |
|---|---:|
| `audits` | 114 |
| `plugins` | 90 |
| `standards` | 21 |
| `reports` | 13 |
| `integrations` | 10 |
| `projects` | 9 |
| `research` | 6 |
| `decisions` | 3 |
| `incidents` | 2 |
| `operations` | 2 |
| `sessions` | 2 |
| `playbooks` | 2 |
| `index.md` | 1 |

### Frontmatter health

| Check | Count |
|---|---:|
| Markdown files scanned | 275 |
| Files with YAML frontmatter | 191 |
| Files without YAML frontmatter | 84 |
| Files with frontmatter missing `title`, `type`, or `status` | 32 |
| Tiny/empty markdown files | 0 |
| Files with Drive linkage metadata | 82 |
| Files lacking any `last_verified` / `verified_at` / `timestamp` field | 29 |

Representative files without frontmatter:

- `okf/integrations/agent-profile-inventory.md`
- `okf/audits/ned-scan-triage-2026-06-27-r91.md`
- `okf/audits/ned-scan-triage-2026-06-29-r133.md`
- `okf/audits/ned-scan-triage-2026-06-27-r76.md`
- `okf/audits/ned-scan-triage-2026-06-27-r88.md`
- `okf/audits/ned-scan-triage-2026-06-29-r131.md`

Representative files missing required fields despite having frontmatter:

| File | Missing |
|---|---|
| `okf/integrations/linear-webhook-events.md` | `status` |
| `okf/integrations/cloudflare-tunnel-webhooks.md` | `status` |
| `okf/standards/cloudflare-access-okf-publisher.md` | `status` |
| `okf/standards/dispatch-production-grade.md` | `status` |
| `okf/audits/ned-scan-triage-2026-06-27-r4.md` | `title`, `type`, `status` |
| `okf/audits/ned-scan-triage-2026-06-27-r17.md` | `title`, `type`, `status` |

### Field usage

Most common frontmatter keys:

| Field | Files |
|---|---:|
| `title` | 168 |
| `type` | 166 |
| `description` | 166 |
| `resource` | 164 |
| `tags` | 164 |
| `timestamp` | 162 |
| `status` | 159 |
| `last_verified` | 156 |
| `verified_by` | 156 |
| `git_repo` | 154 |
| `git_path` | 153 |
| `linear_issue` | 151 |

This is close enough to standardize. The main cleanup is not inventing a schema; it is backfilling the missing fields on older audit/integration docs.

## Integration registry findings

Docs under `/okf/integrations/`:

- `okf/integrations/agent-profile-inventory.md`
- `okf/integrations/ubersuggest-mcp.md`
- `okf/integrations/linear-webhook-events.md`
- `okf/integrations/webhook-handler-test-pattern.md`
- `okf/integrations/api-key-locations.md`
- `okf/integrations/prismatic-webhook-chain-recovery-2026-06-23.md`
- `okf/integrations/cloudflare-tunnel-webhooks.md`
- `okf/integrations/cloudflare-account-activeoahu.md`
- `okf/integrations/jules-cli-capability-report.md`
- `okf/integrations/index.md`

Findings:

1. `agent-profile-inventory.md` lacks YAML frontmatter, despite being listed in the integration index.
2. `linear-webhook-events.md` and `cloudflare-tunnel-webhooks.md` lack `status` in frontmatter.
3. `index.md` has malformed Markdown in the final table row:
   - Current row ends with `| ned | ✅ Active |)`.
   - There is also no owner/status split for `linear-webhook-events.md`, `cloudflare-tunnel-webhooks.md`, or `agent-profile-inventory.md`; the owner cell is currently a prose description.

## Task-readiness scoring audit

The current scanner-produced task spec for [GRO-3461](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3461) includes:

- Definition of Done present: yes.
- Execution contract present: yes.
- Agent label: `agent:ned`.
- Dispatch label: `dispatch:ready`.
- Requested output path: `audits/ned/`.

Readiness score: **7/10**.

Deductions:

- `-1` because requested output path `audits/ned/` conflicts with Ned's writable repo lanes (`scripts/`, `prismatic/`, `plugins/`). I wrote the committed artifact to `scripts/reports/` and created a workspace convenience copy separately.
- `-1` because the issue asks for drift checking but the current deploy branch lacks the drift checker script.
- `-1` because the task is audit-only and has no canonical test/build command; it requires ad-hoc verification.

## Severity table

| Severity | Finding | Impact | Recommended fix |
|---|---|---|---|
| 🔴 High | `origin/deploy-fresh` has no OKF Drive drift checker under `scripts/`. | Drift detection is not a deployable control even though 82 OKF docs carry Drive IDs. | Merge or recreate the checker in Ned's `scripts/` lane; add a side-effect-free `--dry-run` / `--metadata-only` smoke. |
| 🔴 High | OKF repo currently shows `D okf/vision/prismatic-north-star.md`. | The North Star reference may be deleted locally or awaiting an owner decision. | OKF owner should verify whether this deletion is intentional before any sync/publish job runs. |
| 🟡 Medium | 84/275 OKF markdown files lack frontmatter. | Indexing, drift checks, and readiness scoring cannot classify them reliably. | Backfill frontmatter for old `okf/audits/` files or exclude historical audits explicitly from compliance scoring. |
| 🟡 Medium | 32 frontmatter-bearing files miss baseline required keys. | Compliance scoring returns noisy failures; agents cannot trust `status`/`type`. | Add a schema linter with an allowlist for legacy audit logs. |
| 🟡 Medium | Integration index table has malformed/ambiguous rows. | Humans and agents get unclear ownership/status for webhooks and profile inventory. | Normalize owner/status columns and remove the stray `)` on the Prismatic webhook chain row. |
| 🟡 Medium | Task specs can request out-of-lane `audits/ned/` output. | Ned branches fail pre-push if the request is followed literally. | Update the task generator to request `scripts/reports/` for Ned repo-committed audit artifacts; keep `/home/ubuntu/work/audits/ned/` as a workspace copy only. |

## Recommended next actions

1. **Ship a canonical OKF drift checker on `deploy-fresh`.** Either merge the existing shared-checkout `scripts/check-drive-drift.py` or recreate it with a safe dry-run path. It should report linked count, checked count, drift count, missing/trashed count, and credential/auth status.
2. **Add `scripts/okf_compliance_scan.py`.** Make it side-effect-free and produce JSON + markdown summary: frontmatter coverage, missing required keys, stale `last_verified`, malformed integration index rows, and OKF git dirty state.
3. **Normalize `/okf/integrations/index.md`.** Fix the malformed row and split owner/status cells cleanly.
4. **Backfill frontmatter in high-value docs first.** Start with `okf/integrations/agent-profile-inventory.md`, then standards docs missing `status`, then historical audit logs only if they remain part of active scoring.
5. **Fix task generator output paths for Ned audit tasks.** The repo-committed target should be `scripts/reports/<issue>-<topic>.md`; if a workspace artifact under `/home/ubuntu/work/audits/ned/` is still wanted, treat it as a copy, not a pushed repo path.

## Verification commands run

```bash
# Isolated worktree from deploy baseline
git worktree add -B ned/GRO-3461 /tmp/prismatic-gro3461 origin/deploy-fresh

# OKF corpus/frontmatter/readiness scan
python3 <inline scanner> > /tmp/gro3461/okf_audit.json

# Drift checker availability smoke on current branch
python3 scripts/check-drive-drift.py
# -> file absent on /tmp/prismatic-gro3461, exit 2

# OKF git status
git status --short  # in /home/ubuntu/work/growthwebdev-knowledge
# -> D okf/vision/prismatic-north-star.md
```

No OKF files, runtime databases, cron jobs, or credentials were modified by this audit.
