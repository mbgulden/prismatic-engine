# Bare-Metal Onboarding & Harness Compatibility Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make Prismatic Engine usable from a bare computer first, then progressively attach/detach harnesses, provider CLIs, local GPUs, and cloud platforms without losing engine state.

**Architecture:** Add explicit kernel-owned lifecycle surfaces: init/doctor/status, journal init, capability registry, harness adapter contract, scheduler abstraction, and import/export bundle. Harnesses remain optional adapters that install shims and scheduler jobs but never own business logic.

**Tech Stack:** Python stdlib + PyYAML, existing `prismatic/` package, existing CLI entry points in `pyproject.toml`, pytest.

---

## Task 1: Align README quickstart with actual bare-metal lifecycle

**Objective:** Replace the current optimistic quickstart with a progressive install story that works without Hermes.

**Files:**
- Modify: `README.md`
- Reference: `docs/bare-metal-onboarding-and-harness-compatibility.md`

**Steps:**
1. Add a "Bare computer quickstart" section.
2. Show `pipx install`/`pip install` and local clone fallback.
3. Show `prismatic doctor`, `prismatic init`, `prismatic journal init`, and `prismatic status` as target commands.
4. Explicitly mark missing commands as roadmap if not implemented yet.
5. Verify markdown links resolve.

**Verification:**
```bash
python3 - <<'PY'
from pathlib import Path
text = Path('README.md').read_text()
assert 'Bare computer quickstart' in text
assert 'prismatic journal init' in text
PY
```

---

## Task 2: Add `prismatic init` skeleton

**Objective:** Create a real first-run config initializer.

**Files:**
- Create/modify: `prismatic/cli.py` or existing appropriate CLI module
- Modify: `pyproject.toml`
- Test: `tests/test_init_cli.py`

**Behavior:**
- Creates config directory.
- Writes default `config.yaml` if missing.
- Creates journal root, run-record root, task-state root.
- Does not overwrite existing config unless `--force`.

**Verification:**
```bash
python3 -m pytest tests/test_init_cli.py -q
PRISMATIC_HOME=/tmp/prismatic-smoke prismatic init
```

---

## Task 3: Add `prismatic doctor` / `prismatic status`

**Objective:** Report core health and optional capability availability without failing when integrations are missing.

**Files:**
- Create: `prismatic/doctor.py`
- Modify: `pyproject.toml`
- Test: `tests/test_doctor.py`

**Checks:**
- Python version.
- Git availability.
- Config exists.
- Journal root exists.
- AGY command availability.
- Claude CLI availability.
- Codex CLI availability.
- Jules CLI (jules.google.com) command availability.
- `nvidia-smi` availability.
- `gcloud` availability.
- Hermes availability.

**Verification:**
```bash
python3 -m pytest tests/test_doctor.py -q
prismatic doctor --json
```

---

## Task 4: Add `prismatic journal init`

**Objective:** Make journal continuity useful before Linear or any harness exists.

**Files:**
- Modify: `prismatic/journal.py`
- Test: `tests/test_journal.py`

**Behavior:**
- Creates journal directories.
- Creates `.state/` and `.index/`.
- Writes a README explaining local-only mode.
- Does not require Linear.

**Verification:**
```bash
PRISMATIC_HOME=/tmp/prismatic-smoke prismatic-journal init
PRISMATIC_HOME=/tmp/prismatic-smoke prismatic-journal-snapshot --force
```

---

## Task 5: Add capability registry model

**Objective:** Represent providers/compute/cloud/harnesses as optional capabilities.

**Files:**
- Create: `prismatic/capabilities.py`
- Create: `tests/test_capabilities.py`

**Schema:**
```python
@dataclass
class Capability:
    id: str
    kind: str
    command: str | None
    modes: list[str]
    available: bool
    health: str
```

**Verification:**
```bash
python3 -m pytest tests/test_capabilities.py -q
```

---

## Task 6: Add `prismatic providers scan/attach/detach/doctor`

**Objective:** Detect and register AGY, Claude CLI, Codex CLI, Jules CLI (jules.google.com), local OpenAI-compatible endpoint, and cloud providers.

**Files:**
- Create: `prismatic/providers_cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_providers_cli.py`

**Rules:**
- Scan must be read-only.
- Attach writes config.
- Detach marks unavailable but does not delete run records.
- Doctor returns JSON and human text.

---

## Task 7: Add harness adapter contract

**Objective:** Define the boundary for Hermes/OpenClaw/future harness adapters.

**Files:**
- Create: `prismatic/harnesses/base.py`
- Create: `prismatic/harnesses/hermes.py`
- Test: `tests/test_harness_contract.py`

**Contract methods:**
- `detect()`
- `attach()`
- `detach()`
- `install_shims()`
- `export_scheduler()`
- `import_scheduler()`
- `sync_skills()`
- `doctor()`

---

## Task 8: Add `prismatic harness scan/attach/detach/doctor`

**Objective:** Make harness attachment user-facing.

**Files:**
- Create: `prismatic/harness_cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_harness_cli.py`

**First adapter:** Hermes.

**Future adapter placeholder:** OpenClaw.

**Verification:**
```bash
prismatic harness scan --json
prismatic harness attach hermes --dry-run
```

---

## Task 9: Add scheduler abstraction

**Objective:** Support systemd/crontab/Hermes cron/OpenClaw scheduler migration.

**Files:**
- Create: `prismatic/scheduler.py`
- Test: `tests/test_scheduler.py`

**Initial implementations:**
- systemd timer writer in dry-run mode
- crontab writer in dry-run mode
- Hermes cron export/import schema only

**Verification:**
```bash
prismatic scheduler list --json
prismatic scheduler enable journal-snapshot --every 1h --dry-run
```

---

## Task 10: Add export/import bundle format

**Objective:** Make detachment and migration safe.

**Files:**
- Create: `prismatic/bundle.py`
- Test: `tests/test_bundle.py`

**Bundle includes:**
- manifest
- config
- journals
- task state
- run records
- scheduler export
- harness metadata

**Verification:**
```bash
prismatic export bundle --output /tmp/prismatic-export
prismatic import bundle /tmp/prismatic-export --dry-run
```

---

## Task 11: UI capability cards

**Objective:** Pull progressive configuration into the Prismatic interface.

**Files:**
- TBD based on current dashboard/frontend ownership.

**Cards:**
- Core
- Journal
- Harnesses
- Providers
- Compute
- Cloud
- Scheduler
- Export/import

**Rule:** UI calls the same CLI/API contracts; no UI-only setup logic.

---

## Task 12: Documentation and examples

**Objective:** Make the website story match the product reality.

**Files:**
- Modify: `README.md`
- Create: `docs/getting-started-bare-metal.md`
- Create: `docs/harness-adapters.md`
- Create: `docs/capability-registry.md`

**Examples:**
- Bare computer, journal only.
- AGY-only, no harness.
- Independent first, Hermes later.
- Hermes to OpenClaw migration.
- Add local GPU.
- Add GCP.

---

## Acceptance Criteria

- A user can install/init without Hermes, Linear, AGY, or GPU.
- Journal local-only mode works before any provider or harness is attached.
- Missing capabilities are reported as unavailable, not fatal errors.
- Hermes attach installs only shims/scheduler plumbing, not duplicate business logic.
- Detach/export preserves journals and engine state.
- Provider/harness/scheduler adapters have tests and dry-run modes.
