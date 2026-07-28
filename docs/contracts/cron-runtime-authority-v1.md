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
| Installed Prismatic systemd timers (`9` unit files; active and inactive) | Per-unit evidence in section 1.2 | Per-unit evidence in section 1.2 | Exact timer, service, executable, and configuration paths in section 1.2 | Exact per-file SHA-256 or `[REDACTED—SECRET-SCOPED]` in section 1.2 | Mixed; classified before migration | NONE selected by this discovery slice | Per-unit bytes plus exact enabled/active state in section 1.2 |
| Native Cron Registry Store | `ubuntu:ubuntu` | `0600` | `/home/ubuntu/work/prismatic-pe-native-crons/prismatic_state/native_crons.json` | `4bc1666e896157486babc4704cb5c04de8cb6faf445520854593491669076d28` | YES (`/home/ubuntu/work/prismatic-pe-native-crons`) | Configuration Source | Repository default `SEO_NATIVE_CRONS` in `prismatic/native_crons.py` |
| Upstream Outcome Contract | `ubuntu:ubuntu` | `0644` | `docs/contracts/cron-trigger-outcome-v1.md` | `0cc7b4445a9ea9172f5fb7a2a246bc0ca1c87d94e2ba7573a9ccfe27e04af885` | NO (Versioned spec) | Normative Spec | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |
| Native Crons Registry Module | `ubuntu:ubuntu` | `0644` | `prismatic/native_crons.py` | `c8c580fa43e248815dc5c7e97dfe00d30d52d74bda53e20ee73e6f4029407c76` | NO (Tracked code) | Registry Definition | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |
| Installer Script | `ubuntu:ubuntu` | `0644` | `scripts/install_native_crons.py` | `c83d3388d06dbaa8da0f055ce6be31887aadca4c4573d662fc31e34500868c5a` | NO (Tracked code) | Legacy Installer (To Reconcile) | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |
| Core Crons Module | `ubuntu:ubuntu` | `0644` | `prismatic/core_crons.py` | `2759c4038bc6f02cb9cdebcb518dbb9a1643d7c1d201a5ff0f4f1876ada59bb2` | NO (Tracked code) | Legacy Manifest (To Reconcile) | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |
| Cron Authority Engine | `ubuntu:ubuntu` | `0644` | `prismatic/cron_authority.py` | `6f0db68c794616a5aae6bc37cac987fe985354ff9afca67ccdf80a7dcfb1f4a0` | NO (Tracked code) | Durable Authority Engine | `BASE_COMMIT` `e63d621a26a944a66cd4af2c6b5ab3084fc92b55` |

### 1.2 Installed Prismatic systemd timer inventory

Read-only `systemctl list-unit-files --type=timer` discovery found the following installed Prismatic timer authorities. Disabled or inactive units remain inventoried because installed bytes can be re-enabled later. Secret-scoped configuration contents and digests are intentionally redacted; no secret bytes were read into this contract.

#### 1.2.1 `prismatic-agent-bus-fred.timer` → `prismatic-agent-bus-fred.service`
- **Timer state**: `enabled=enabled; active=active; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-agent-bus-fred.timer` — `root:root` mode `0644` SHA-256 `d14c44d66bb6a515a72e38d69f66fd4c068a9a9d67337d0d1234edcf2dcc50c2`
- **Service state**: `enabled=static; active=inactive; load=loaded`
- **Service unit**: `/etc/systemd/system/prismatic-agent-bus-fred.service` — `root:root` mode `0644` SHA-256 `d635067c1736d852e80508d81b1fc11bcc875b0d1b1e36d0544e945611d68989`
- **Execution identity**: `user=ubuntu; group=ubuntu; cwd=/home/ubuntu/prismatic-agent-bus`
- **Referenced executable/configuration evidence**:
  - `/usr/bin/python3` — `root:root` mode `0755` SHA-256 `1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`
  - `/home/ubuntu/prismatic-agent-bus/bin/prismatic_agent_bus.py` — `ubuntu:ubuntu` mode `0711` SHA-256 `db2e3dcf36fd766f1801a167b458ec4dd421db2b5d8a0903ce88f40c72b19299`
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

#### 1.2.2 `prismatic-agent-bus-george-audit.timer` → `prismatic-agent-bus-george-audit.service`
- **Timer state**: `enabled=enabled; active=active; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-agent-bus-george-audit.timer` — `root:root` mode `0644` SHA-256 `12b8878c34056f2d7c089cfac5d6e2a80d004d80477f020961e9ba9094b770ab`
- **Service state**: `enabled=static; active=inactive; load=loaded`
- **Service unit**: `/etc/systemd/system/prismatic-agent-bus-george-audit.service` — `root:root` mode `0644` SHA-256 `c76b21c72f1177480ff8ca6ee7cd3f9b73564a31a75641f3d431a3bedc4710e0`
- **Execution identity**: `user=ubuntu; group=ubuntu; cwd=/home/ubuntu/prismatic-agent-bus`
- **Referenced executable/configuration evidence**:
  - `/usr/bin/python3` — `root:root` mode `0755` SHA-256 `1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`
  - `/home/ubuntu/prismatic-agent-bus/bin/prismatic_agent_bus.py` — `ubuntu:ubuntu` mode `0711` SHA-256 `db2e3dcf36fd766f1801a167b458ec4dd421db2b5d8a0903ce88f40c72b19299`
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

#### 1.2.3 `prismatic-agent-bus-kai.timer` → `prismatic-agent-bus-kai.service`
- **Timer state**: `enabled=enabled; active=active; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-agent-bus-kai.timer` — `root:root` mode `0644` SHA-256 `257218cd322f4155871b2226b3feaaa7720d25e417994be942ecc2d99c714d71`
- **Service state**: `enabled=static; active=inactive; load=loaded`
- **Service unit**: `/etc/systemd/system/prismatic-agent-bus-kai.service` — `root:root` mode `0644` SHA-256 `94008a0efc246a865b378f8aad8affc2b34cee58a67dcd541c3cf442ff2612e0`
- **Execution identity**: `user=ubuntu; group=ubuntu; cwd=/home/ubuntu/prismatic-agent-bus`
- **Referenced executable/configuration evidence**:
  - `/usr/bin/python3` — `root:root` mode `0755` SHA-256 `1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`
  - `/home/ubuntu/prismatic-agent-bus/bin/prismatic_agent_bus.py` — `ubuntu:ubuntu` mode `0711` SHA-256 `db2e3dcf36fd766f1801a167b458ec4dd421db2b5d8a0903ce88f40c72b19299`
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

#### 1.2.4 `prismatic-curator-digest.timer` → `prismatic-curator-digest.service`
- **Timer state**: `enabled=enabled; active=active; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-curator-digest.timer` — `root:root` mode `0644` SHA-256 `1c33dcc36ba666d67adc1c375be759354bb2f6c9148a6721dcef17bf15e3aedf`
- **Service state**: `enabled=disabled; active=inactive; load=loaded`
- **Service unit**: `/etc/systemd/system/prismatic-curator-digest.service` — `root:root` mode `0644` SHA-256 `6dc5161d980967e52bee285cf6c46cb97fceda8398c8db11c77d85f5c747ec71`
- **Execution identity**: `user=ubuntu; group=root(default); cwd=/home/ubuntu/.prismatic/runtime/prismatic-engine`
- **Referenced executable/configuration evidence**:
  - `/usr/bin/env` — `root:root` mode `0755` SHA-256 `0aefff8f912fb75716c5d4de3b6acde93edbe8fa280fc8ee895c1226d3e373ef`
  - `/home/ubuntu/.prismatic/venv_stable/bin/python3` — `root:root` mode `0755` SHA-256 `1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`
  - `/home/ubuntu/.prismatic/env.d/linear_oauth.env` — `ubuntu:ubuntu` mode `0600` SHA-256 `[REDACTED—SECRET-SCOPED]`
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

#### 1.2.5 `prismatic-fleet-watchdog.timer` → `prismatic-fleet-watchdog.service`
- **Timer state**: `enabled=disabled; active=inactive; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-fleet-watchdog.timer` — `root:root` mode `0644` SHA-256 `057451dfd40caf5e5bfdc4e578e11d6ba2a59811dea20bb53cb40f756cd149e8`
- **Service state**: `enabled=masked; active=inactive; load=masked`
- **Service unit**: `/etc/systemd/system/prismatic-fleet-watchdog.service` — masked symlink `root:root` mode `0777` → `/dev/null`; link-text SHA-256 `fd5d32feb2d3562582258990ecfca9b88376957e512d5caac72ad89fc78d2df4`
- **Execution identity**: `user=root(default); group=root(default); cwd=(unset)`
- **Referenced executable/configuration evidence**:
  - None declared by `ExecStart` or `EnvironmentFiles`.
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

#### 1.2.6 `prismatic-governance-autopacer.timer` → `prismatic-governance-autopacer.service`
- **Timer state**: `enabled=enabled; active=active; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-governance-autopacer.timer` — `root:root` mode `0644` SHA-256 `5504cd5ce9c3d54664659e406c6e61e86e785ea0bccdcb88103ed996bb8b9d23`
- **Service state**: `enabled=static; active=inactive; load=loaded`
- **Service unit**: `/etc/systemd/system/prismatic-governance-autopacer.service` — `root:root` mode `0644` SHA-256 `ed9eb7c7ecc15f1d81cc1d5ec967ebcd50baa4f6019556cc9a2d012257d02cf2`
- **Execution identity**: `user=ubuntu; group=ubuntu; cwd=/home/ubuntu/prismatic-agent-bus`
- **Referenced executable/configuration evidence**:
  - `/usr/bin/python3` — `root:root` mode `0755` SHA-256 `1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`
  - `/home/ubuntu/prismatic-agent-bus/bin/prismatic_governance_autopacer.py` — `ubuntu:ubuntu` mode `0711` SHA-256 `62a7e0d7acc5b8ec9e6ff9eb39750ccaaef11c5250876438c8d9e80f1b19b776`
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

#### 1.2.7 `prismatic-soak-recorder.timer` → `prismatic-soak-recorder.service`
- **Timer state**: `enabled=enabled; active=active; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-soak-recorder.timer` — `root:root` mode `0644` SHA-256 `f50fa43bcefba58295e5f81f83d4428b2aaad2243fc2d087af7067c759ad7fd4`
- **Service state**: `enabled=disabled; active=inactive; load=loaded`
- **Service unit**: `/etc/systemd/system/prismatic-soak-recorder.service` — `root:root` mode `0644` SHA-256 `280fd4a4af090d9283a026f653933dec21da1ae0dbcf70735b05a8baca61cbb4`
- **Execution identity**: `user=ubuntu; group=root(default); cwd=/home/ubuntu`
- **Referenced executable/configuration evidence**:
  - `/home/ubuntu/.prismatic/venv_stable/bin/python3` — `root:root` mode `0755` SHA-256 `1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`
  - `/home/ubuntu/.hermes/profiles/orchestrator/scripts/soak_recorder.py` — `ubuntu:ubuntu` mode `0755` SHA-256 `de0908f9209bc64c25716dfea1041eca37618463ce194e9975d45e00822960c3`
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

#### 1.2.8 `prismatic-watchdog.timer` → `prismatic-watchdog.service`
- **Timer state**: `enabled=disabled; active=inactive; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-watchdog.timer` — `root:root` mode `0644` SHA-256 `5e9e6c34888e36ce6b3d2efa48580e77a61e3b110e48c1a1c31ab0ee332f64a5`
- **Service state**: `enabled=static; active=inactive; load=loaded`
- **Service unit**: `/etc/systemd/system/prismatic-watchdog.service` — `root:root` mode `0644` SHA-256 `2b49dd8dac98333e0c5b922e6bb7736ff079af3705b99a00fa55b35d28dc5bda`
- **Execution identity**: `user=root(default); group=root(default); cwd=/home/ubuntu/.prismatic/runtime/prismatic-engine`
- **Referenced executable/configuration evidence**:
  - `/home/ubuntu/.prismatic/runtime/prismatic-engine/scripts/watchdog.sh` — `ubuntu:ubuntu` mode `0755` SHA-256 `1598a548fd379b45dabb7a67cacac7b8be501598e98ea2c0ee9dbea118f5e8e7`
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

#### 1.2.9 `prismatic-webhook-drain.timer` → `prismatic-webhook-drain.service`
- **Timer state**: `enabled=enabled; active=inactive; load=loaded`
- **Timer unit**: `/etc/systemd/system/prismatic-webhook-drain.timer` — `root:root` mode `0644` SHA-256 `b114f0c2b150bbfa256ad124c3a273875b49961c4577dbde3e83dcca94f70314`
- **Service state**: `enabled=static; active=inactive; load=loaded`
- **Service unit**: `/etc/systemd/system/prismatic-webhook-drain.service` — `root:root` mode `0644` SHA-256 `7958ab9e63b7d46255a426d6019da7500a53868f4261909246df05e686892df1`
- **Execution identity**: `user=ubuntu; group=root(default); cwd=/home/ubuntu/.prismatic/runtime/prismatic-engine`
- **Referenced executable/configuration evidence**:
  - `/home/ubuntu/.prismatic/venv_stable/bin/python3` — `root:root` mode `0755` SHA-256 `1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`
  - `/home/ubuntu/.prismatic/runtime/prismatic-engine/scripts/drain_webhook_queue.py` — `ubuntu:ubuntu` mode `0755` SHA-256 `5e4c85bd0836a8af4d713b5d750b1b0a651d4a922333897619bef1f7f63e0cc4`
  - `/home/ubuntu/.prismatic/env.d/linear_oauth.env` — `ubuntu:ubuntu` mode `0600` SHA-256 `[REDACTED—SECRET-SCOPED]`
- **Rollback source**: preserve the timer-unit bytes and service-unit bytes at the SHA-256 values above plus the exact enabled/active states above before any separately authorized mutation; rollback MUST restore those bytes, run `systemctl daemon-reload`, and restore the recorded enabled/active states.

The inventory is evidence only. It does not assert that every timer schedules the same `cron_id`, and it does not authorize disabling, enabling, rewriting, or consolidating any unit. A later migration MUST classify each unit as PE cron authority, unrelated governance/control-plane authority, or preserved independent service before touching it.

`INSTALLED_PRISMATIC_TIMER_COUNT=9`

### 1.3 Discovery Findings

1. **Mutable Checkout Vulnerability**: The active user crontab (`crontab -l`) contains entries created by `scripts/install_native_crons.py` that directly execute commands inside a mutable checkout path (`/home/ubuntu/work/prismatic-pe-native-crons`). If that checkout is modified, uncommitted code or arbitrary dependency edits execute immediately without release pinning or verification.
2. **Dual-Authority Hazard**: Multiple independent trigger mechanisms currently exist: user crontab entries, all nine installed Prismatic timer units inventoried in section 1.2, and OS crontabs (`/etc/cron.d`). A later migration MUST first classify each timer as PE cron authority, unrelated governance/control-plane authority, or preserved independent service; installed does not imply duplicate scheduling. Any shared `cron_id` across authorities would lack transactional duplicate fencing.
3. **Direct Workload Execution Bypass**: The legacy crontab entries execute workload scripts directly (`python3 scripts/seo/aot_kpi_tracker.py`) without passing through the canonical durable cron authority (`prismatic/cron_authority.py`) or emitting normalized trigger envelopes. Consequently, uniqueness keys are not generated, claims are not fenced, and receipts are not appended.
4. **Admission State Handling**: The native cron store (`prismatic_state/native_crons.json`) tracks job states (`active`, `paused`, `deactivated`, `deleted`). However, raw user crontab lines do not inspect this store before invoking commands; deactivated or paused crons in the crontab continue to run unless manually removed from the crontab.

### 1.3 Exact Discovery Commands & Non-Claims

Read-only discovery was executed using bounded inspection commands:
- `crontab -l`
- `ls -la /etc/cron.d/`
- `systemctl list-timers --all --no-pager`
- `systemctl list-unit-files --type=timer --no-pager`
- bounded `systemctl show` for timer/service fragment, execution, identity, and state metadata
- `stat`, `lstat`, and `readlink` metadata inspection without reading secret-scoped configuration contents
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
Read-only discovery found **no current `pe-cron-trigger` artifact in any release**. Therefore this contract records no production path or production digest and authorizes no current cron invocation. A generator or installer MUST fail closed until a separately reviewed implementation release supplies the real artifact and all of the following concrete bindings.

- **Observed full-commit absence canary**: `/home/ubuntu/.prismatic/releases/e63d621a26a944a66cd4af2c6b5ab3084fc92b55/bin/pe-cron-trigger`. That exact file does not exist and therefore MUST produce zero generated cron lines. Every future eligible path MUST use this same prefix/suffix with the real manifest's full 40-lowercase-hex merge commit as the single intervening directory component; semantic-version aliases, `latest`, shortened aliases, symlinks, and mutable worktree paths are forbidden.
- **Owner & Mode**: the resolved artifact MUST be a regular non-symlink file owned by `ubuntu:ubuntu`, mode `0555` or stricter; every release-path parent MUST be non-group/world-writable.
- **Canonical release manifest**: the same immutable directory MUST contain an RFC 8785 canonical manifest with concrete `release_id`, full merge commit, source tree, executable artifact SHA-256, and dependency-lock SHA-256. `release_digest` MUST equal lowercase SHA-256 of those exact canonical bytes.
- **Canonical config binding**: the generated invocation MUST name an absolute immutable canonical cron-registry JSON path and a concrete `config_digest` equal to lowercase SHA-256 of its canonical bytes. Mutable state such as `prismatic_state/native_crons.json` cannot satisfy this binding.
- **Generation gate**: before emitting any line, the generator MUST resolve the path without following a symlink, recompute the executable, lock, manifest, and config digests, require exact equality with the binding record, and require that the full merge commit in the manifest equals the release-directory name. Missing files, placeholders, aliases, digest-shape-only values, or any mismatch MUST yield zero generated cron lines.

The concrete production path and digests necessarily originate in the future implementation release that creates the currently absent hook; inventing them in this design-only slice would be fabricated release proof. Their presence and recomputation are mandatory preconditions to the separately authorized installation event.

For validator coverage only, the non-production release fixture digest is `f77751f09584ebc9af451a58b8c6a472e9c5415c1c178bb074e7c7c0dbf09b70` and the canonical empty-registry fixture config digest is `46adc580c4ee48b1165a745999e3a2797d207614a8ebd8b5c30740c7ab4390f9`. Both are concrete valid lowercase SHA-256 values, and both MUST be rejected by active deployment policy because neither identifies a verified installed hook/config pair.

### 3.2 Normalized Trigger-Envelope Input
The thin-hook `pe-cron-trigger` MUST normalize every firing into the standard RFC 3339 UTC Trigger Envelope defined in `docs/contracts/cron-trigger-outcome-v1.md`:

The following is a **non-production schema fixture** only. Its valid 64-hex `release_digest` demonstrates envelope shape; active deployment policy MUST reject it because no matching verified release manifest or hook exists.

```json
{
  "trigger_event_id": "trg_01j9a8b7c6d5e4f321",
  "cron_id": "seo.aot-weekly-rankings",
  "registry_generation": 1,
  "schedule_bucket": "2026-07-28T04:00:00Z",
  "trigger_kind": "scheduled",
  "transport_kind": "crontab",
  "command_digest": "7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b",
  "release_digest": "f77751f09584ebc9af451a58b8c6a472e9c5415c1c178bb074e7c7c0dbf09b70",
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
| User Crontab Managed Block | `crontab -l` (`8ff18b26ef3c...`) | Single `ubuntu` crontab referencing the concrete commit-addressed `pe-cron-trigger` binding | Existing hook artifact plus manifest/config binding pass every section 3.1 recomputation check; generated bytes contain no alias, placeholder, or mutable path | `ubuntu:ubuntu`, `0600` | Replace managed block `# BEGIN PRISMATIC_NATIVE_CRONS` atomically via tempfile crontab pipe; byte-identical reruns are no-ops | Parse every managed invocation and recompute its path, executable, lock, release-manifest, and config binding before comparing installed bytes | Separate Admission Event |
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
- [x] **`PE-CRON-RUNTIME-IMMUTABLE-HOOK`**: Fail-closed full-commit path grammar plus mandatory concrete release-manifest/config recomputation; no production binding is claimed while the hook artifact is absent.
- [x] **`PE-CRON-RUNTIME-NO-DIRECT-EXEC`**: Thin hook restricted to envelope submission with zero direct workload command execution.
- [x] **`PE-CRON-RUNTIME-ROLLBACK-PLAN`**: Byte-for-byte pre-mutation crontab preservation, restoration commands, and digest verification.
- [x] **One-Path Repository Containment**: Modifications restricted exclusively to `docs/contracts/cron-runtime-authority-v1.md`.
