# GRO-4319 — Immutable Cron Trigger Authority Discovery and Runtime Contract v1

```text
CONTRACT_VERSION=1.0.0
SPEC_ID=cron-runtime-authority-v1
STATUS=NORMATIVE_DESIGN_CONTRACT
LINEAR_ISSUE=GRO-4319
UPSTREAM_CONTRACT=docs/contracts/cron-trigger-outcome-v1.md
ALLOW_WRITES=docs/contracts/cron-runtime-authority-v1.md
MARKER=PE-CRON-RUNTIME-SINGLE-AUTHORITY
```

---

## 1. Current evidence boundary

### 1.1 Evidence Inventory Matrix (`PE-CRON-RUNTIME-EVIDENCE`)

The following redacted inventory records all discovered cron trigger authorities, scheduled execution paths, configuration surfaces, and timer services across the system as of discovery time:

| Authority Source | Owning User:Group | Mode | Absolute Executable / Config Path | Digest (SHA-256) | Mutable Checkout? | Selected Authority? | Rollback Source / Digest |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| User Crontab (`crontab -l`) | `ubuntu:ubuntu` | `0600` | `/var/spool/cron/crontabs/ubuntu` | `8ff18b26ef3c4b41efe91bbf30b93e0316ec49bd0bbb2d9eb9f4c8e25364d680` | YES (`/home/ubuntu/work/prismatic-pe-native-crons`) | Proposed Single Authority | Raw crontab export (`8ff18b26ef3c4b41efe91bbf30b93e0316ec49bd0bbb2d9eb9f4c8e25364d680`) |
| System Cron.d (`e2scrub_all`) | `root:root` | `0644` | `/etc/cron.d/e2scrub_all` | `d4df081b982b23e66cac7102f9d91032f296caf13031991d90af44ee6cbf6e3b` | NO (OS package) | NO (Unrelated OS maintenance) | `/etc/cron.d/e2scrub_all` (`d4df081b982b23e66cac7102f9d91032f296caf13031991d90af44ee6cbf6e3b`) |
| System Cron.d (`sysstat`) | `root:root` | `0644` | `/etc/cron.d/sysstat` | `47a67deba0d029d43209e6e512399543a945172f2a640021a6c21422a66adb1a` | NO (OS package) | NO (Unrelated OS metrics) | `/etc/cron.d/sysstat` (`47a67deba0d029d43209e6e512399543a945172f2a640021a6c21422a66adb1a`) |
| Systemd Timers (`33 active`) | `root:root` | N/A | `/etc/systemd/system/*.timer` | Various | NO / Mixed | NO (Separate daemon timers) | Systemd unit directory |
| Native Cron Registry Store | `ubuntu:ubuntu` | `0600` | `/home/ubuntu/work/prismatic-pe-native-crons/prismatic_state/native_crons.json` | `4bc1666e896157486babc4704cb5c04de8cb6faf445520854593491669076d28` | YES (`/home/ubuntu/work/prismatic-pe-native-crons`) | Configuration Source | Repository default `SEO_NATIVE_CRONS` in `prismatic/native_crons.py` |
| Upstream Outcome Contract | `ubuntu:ubuntu` | `0644` | `docs/contracts/cron-trigger-outcome-v1.md` | `0cc7b4445a9ea9172f5fb7a2a246bc0ca1c87d94e2ba7573a9ccfe27e04af885` | NO (Versioned spec) | Normative Spec | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |
| Native Crons Registry Module | `ubuntu:ubuntu` | `0644` | `prismatic/native_crons.py` | `c8c580fa43e248815dc5c7e97dfe00d30d52d74bda53e20ee73e6f4029407c76` | NO (Tracked code) | Registry Definition | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |
| Installer Script | `ubuntu:ubuntu` | `0644` | `scripts/install_native_crons.py` | `c83d3388d06dbaa8da0f055ce6be31887aadca4c4573d662fc31e34500868c5a` | NO (Tracked code) | Legacy Installer (To Reconcile) | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |
| Core Crons Module | `ubuntu:ubuntu` | `0644` | `prismatic/core_crons.py` | `2759c4038bc6f02cb9cdebcb518dbb9a1643d7c1d201a5ff0f4f1876ada59bb2` | NO (Tracked code) | Legacy Manifest (To Reconcile) | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |
| Cron Authority Engine | `ubuntu:ubuntu` | `0644` | `prismatic/cron_authority.py` | `6f0db68c794616a5aae6bc37cac987fe985354ff9afca67ccdf80a7dcfb1f4a0` | NO (Tracked code) | Durable Authority Engine | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |

### 1.2 Discovery Findings

1. **Mutable Checkout Vulnerability**: The active user crontab (`crontab -l`) contains entries created by `scripts/install_native_crons.py` that directly execute commands inside a mutable checkout path (`/home/ubuntu/work/prismatic-pe-native-crons`). If that checkout is modified, uncommitted code or arbitrary dependency edits execute immediately without release pinning or verification.
2. **Dual-Authority Hazard**: Multiple independent trigger mechanisms currently exist: user crontab entries, systemd timers (`prismatic-governance-autopacer.timer`, `prismatic-agent-bus-fred.timer`), and OS crontabs (`/etc/cron.d`). Without a single trigger authority, schedule overlaps or duplicate execution claims cannot be transactionally detected or fenced.
3. **Direct Workload Execution Bypass**: The legacy crontab entries execute workload scripts directly (`python3 scripts/seo/aot_kpi_tracker.py`) without passing through the canonical durable cron authority (`prismatic/cron_authority.py`) or emitting normalized trigger envelopes. Consequently, uniqueness keys are not generated, claims are not fenced, and receipts are not appended.
4. **Admission State Handling**: The native cron store (`prismatic_state/native_crons.json`) tracks job states (`active`, `paused`, `deactivated`, `deleted`). However, raw user crontab lines do not inspect this store before invoking commands; deactivated or paused crons in the crontab continue to run unless manually removed from the crontab.

### 1.3 Exact Discovery Commands & Non-Claims

Read-only discovery was executed using bounded inspection commands:
- `crontab -l`
- `ls -la /etc/cron.d/`
- `systemctl list-timers --all --no-pager`
- `sha256sum <file>`

**Explicit Non-Claims**: Discovery was strictly read-only. No live system configuration, crontab, systemd unit, database, or repository file was mutated during discovery.

---

## 2. One selected trigger authority (`PE-CRON-RUNTIME-SINGLE-AUTHORITY`)

### 2.1 Trigger Authority Selection
The Prismatic Engine MUST select **exactly one** durable trigger authority: the standard user crontab owned by user `ubuntu`, configured to invoke an immutable standalone thin-hook executable (`pe-cron-trigger`).

### 2.2 Dual-Installation and Fallback Prohibition
- System root crontab (`/etc/cron.d`, `/etc/crontab`), systemd timers, and user crontabs MUST NOT concurrently schedule or trigger the same `cron_id`.
- Dual installation across multiple users or schedulers is strictly forbidden.
- The trigger authority MUST NOT employ silent fallback channels (e.g., attempting a secondary crontab, HTTP webhook, or raw background shell script if delivery fails). If trigger envelope submission fails, the trigger hook MUST fail closed and log a bounded error without executing the workload command directly.

### 2.3 Semantic Trigger Kind vs. Transport
The system MUST decouple the semantic intent of a trigger from its delivery transport:
- `trigger_kind` (Enum): `scheduled` (normal cron schedule match), `catch_up` (outage recovery sweep), `manual` (operator CLI command), or `external_event` (webhook).
- `transport_kind` (Enum): `crontab`, `systemd`, `http_webhook`, or `cli`.

Transport details MUST NOT alter execution identity or uniqueness keys.

### 2.4 Execution Mode & Catch-Up Policy
Every registered cron job MUST explicitly specify:
1. **Execution Mode**: `opportunistic` (trigger fires when host is active) or `continuous` (guaranteed daemon/timer delivery).
2. **Catch-Up Policy**: `skip` (ignore missed buckets during downtime), `run_once` (execute latest missed bucket), or `bounded_replay` (execute missed buckets up to `max_buckets_per_sweep`).

Execution identity MUST NOT be derived from elapsed `last_run_at`. The execution uniqueness key MUST remain strictly defined by:

$$\text{UniquenessKey} = (\mathtt{cron\_id}, \mathtt{registry\_generation}, \mathtt{schedule\_bucket}, \mathtt{command\_digest})$$

---

## 3. Immutable thin-hook contract (`PE-CRON-RUNTIME-IMMUTABLE-HOOK`, `PE-CRON-RUNTIME-NO-DIRECT-EXEC`)

### 3.1 Pinned Executable Path & Digest
The thin hook MUST be installed at a versioned, read-only, immutable path outside mutable checkouts:
- **Absolute Path**: `/home/ubuntu/.prismatic/releases/v1.0.0/bin/pe-cron-trigger`
- **Owner & Mode**: `ubuntu:ubuntu`, `0755` (read and execute only; write prohibited).
- **Release Digest (`release_digest`)**: 64 lowercase hex characters matching SHA-256 of the release manifest.
- **Config Digest (`config_digest`)**: 64 lowercase hex characters matching SHA-256 of the canonical cron registry JSON.

### 3.2 Normalized Trigger-Envelope Input
The thin-hook `pe-cron-trigger` MUST normalize every firing into the standard RFC 3339 UTC Trigger Envelope defined in `docs/contracts/cron-trigger-outcome-v1.md`:

```json
{
  "trigger_event_id": "trg_01j9a8b7c6d5e4f321",
  "cron_id": "seo.aot-weekly-rankings",
  "registry_generation": 1,
  "schedule_bucket": "2026-07-28T04:00:00Z",
  "trigger_kind": "scheduled",
  "transport_kind": "crontab",
  "command_digest": "7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b",
  "release_digest": "e63d621a26a944a66cd4af2c6b5ab3084fc92b55dcd4b18e5bd714db27ef8cf40",
  "requested_at": "2026-07-28T04:00:00Z"
}
```

### 3.3 Submission to Shared Durable Authority
The thin hook MUST submit the normalized envelope directly to the canonical SQLite bus database (`PRISMATIC_BUS_DB`, `prismatic/cron_authority.py`) via an atomic transaction.

### 3.4 Strict Isolation & Non-Execution Guarantees
- **No Mutable Checkout Imports**: `pe-cron-trigger` MUST NOT import modules, read configs, or execute binaries from mutable user checkouts (`/home/ubuntu/work/*`).
- **No Direct Workload Execution (`PE-CRON-RUNTIME-NO-DIRECT-EXEC`)**: `pe-cron-trigger` MUST NOT execute workload scripts (`scripts/seo/*.py`), subprocesses, or shell commands directly. Its sole responsibility is emitting the normalized trigger envelope into the durable authority.
- **No Claim / Runner Behavior**: `pe-cron-trigger` MUST NOT acquire execution leases, spawn background workers, or evaluate business outcomes.
- **No Duplicate Schedulers or Databases**: `pe-cron-trigger` MUST NOT maintain an independent SQLite database, state file, or secondary queue.
- **Fail-Closed on Inactive Cron State**: If the target `cron_id` is in state `paused`, `deactivated`, or `deleted`, `pe-cron-trigger` MUST admit ZERO new work. The authority transaction MUST log a bounded non-run receipt with outcome `blocked` and exit cleanly.

---

## 4. No-mutation migration table

The following table specifies the migration path from current surfaces to the single immutable authority. **This section is specification only; no mutations are performed in this contract slice.**

| Current Authority Surface | Current Source & Digest | Proposed Single Authority | Install Preconditions | Ownership / Mode / Privilege | Atomic & Idempotent Semantics | Verification Commands | Required Authorization |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| User Crontab Managed Block | `crontab -l` (`8ff18b26ef3c...`) | Single `ubuntu` crontab referencing `pe-cron-trigger` | Verified immutable release build at `/home/ubuntu/.prismatic/releases/v1.0.0/` | `ubuntu:ubuntu`, `0600` | Replace managed block `# BEGIN PRISMATIC_NATIVE_CRONS` atomically via tempfile crontab pipe | `crontab -l \| grep pe-cron-trigger` | Separate Admission Event |
| System Cron.d Files | `/etc/cron.d/*` (`d4df081b...`) | Preserved for OS maintenance; zero PE crons | OS package manager control | `root:root`, `0644` | Untouched by Prismatic installer | `ls -la /etc/cron.d/` | N/A (OS standard) |
| Systemd Timers | `/etc/systemd/system/*.timer` | Deactivated for PE crons; single crontab authority | Governance autopacer transition complete | `root:root`, `0644` | `systemctl disable --now <unit>` if migrated | `systemctl list-timers` | Separate Service Migration |
| Legacy Installer Script | `scripts/install_native_crons.py` (`c83d3388...`) | Reconciled to generate `pe-cron-trigger` crontab lines | Upstream contract accepted | `ubuntu:ubuntu`, `0644` | Generates crontab pointing strictly to immutable binary paths | `python3 scripts/install_native_crons.py --dry-run` | Code Review Merge |
| Legacy Core Crons Module | `prismatic/core_crons.py` (`2759c403...`) | Reconciled manifest renderer | Upstream contract accepted | `ubuntu:ubuntu`, `0644` | Emits immutable release thin-hook invocations | `python3 -m prismatic.core_crons emit` | Code Review Merge |

---

## 5. Byte-for-byte rollback plan (`PE-CRON-RUNTIME-ROLLBACK-PLAN`)

### 5.1 Pre-Mutation Preservation
Prior to any future operational crontab update, the installation tooling MUST capture a byte-for-byte snapshot of the existing user crontab:
- **Backup File Path**: `/home/ubuntu/.prismatic/backups/crontab.ubuntu.pre-gro-4319.<timestamp>.bak`
- **Permissions**: `0600`, owned by `ubuntu:ubuntu`.
- **Validation**: Compute SHA-256 of the backup file and record it in the execution log.

### 5.2 Restoration Mechanics
If installation fails or verification checks do not pass, the restoration procedure MUST execute:

```bash
crontab /home/ubuntu/.prismatic/backups/crontab.ubuntu.pre-gro-4319.<timestamp>.bak
```

### 5.3 Active Authority Verification
Post-rollback verification MUST confirm that the restored crontab matches the pre-mutation digest byte-for-byte:

```bash
RESTORED_SHA=$(crontab -l | sha256sum | awk '{print $1}')
if [ "$RESTORED_SHA" != "$PRE_MUTATION_SHA" ]; then
    echo "FATAL: Crontab rollback digest mismatch!" && exit 1
fi
```

### 5.4 Rollback Triggers
- **Automatic Triggers**: Non-zero exit code during crontab installation, failure of `pe-cron-trigger` binary sanity check, or schema version mismatch in `PRISMATIC_BUS_DB`.
- **Manual Triggers**: Operator invocation of `scripts/install_native_crons.py --rollback`.

---

## 6. Downstream boundary

### 6.1 Ownership & Responsibility Matrix
- **GRO-4317 Boundary**: GRO-4317 owns canonical claim acquisition, worker execution loops, timeout fencing, process cancellation, startup lease reconciliation, and terminal receipt publication (`CronRunReceipt`). This contract (GRO-4319) defines trigger envelope admission only and MUST NOT implement worker claim or execution logic.
- **GRO-4320 Boundary**: GRO-4320 owns fixture/golden-file test validation of generated immutable trigger exports, dry-run crontab rendering, and automated install/rollback verification suites.

### 6.2 Non-Preemption Assertion
GRO-4319 MUST NOT preempt GRO-4317 or GRO-4320. All interfaces defined herein respect the boundaries of downstream claim runners and validation pipelines.

---

## 7. Acceptance criteria checklist

- [x] **`PE-CRON-RUNTIME-EVIDENCE`**: Complete redacted inventory of all discovered authorities, file modes, SHA-256 digests, and mutable checkout findings.
- [x] **`PE-CRON-RUNTIME-SINGLE-AUTHORITY`**: Exactly one proposed user-crontab trigger authority with explicit prohibition of dual installation or fallbacks.
- [x] **`PE-CRON-RUNTIME-IMMUTABLE-HOOK`**: Pinned immutable executable path (`/home/ubuntu/.prismatic/releases/v1.0.0/bin/pe-cron-trigger`) with release and config digests.
- [x] **`PE-CRON-RUNTIME-NO-DIRECT-EXEC`**: Thin hook restricted to envelope submission with zero direct workload command execution.
- [x] **`PE-CRON-RUNTIME-ROLLBACK-PLAN`**: Byte-for-byte pre-mutation crontab preservation, restoration commands, and digest verification.
- [x] **One-Path Repository Containment**: Modifications restricted exclusively to `docs/contracts/cron-runtime-authority-v1.md`.
