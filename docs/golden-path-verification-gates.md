# Golden Path Verification Gates

This document describes how the verification scripts (`verify-phase-dependencies.py`,
`audit-checksums.py`) plug into the Prismatic Engine's pipeline and CI gates.

## Scripts Overview

### `scripts/verify-phase-dependencies.py`

Validates that a phase has all its prerequisite phases completed before execution.

```bash
# From repo root — check if phase 14 can run
python3 scripts/verify-phase-dependencies.py 14

# With custom state directory
python3 scripts/verify-phase-dependencies.py 17 --state-dir prismatic_state
```

**Exit codes:**
- `0` — All prerequisites met (or manifest missing — non-critical skip)
- `1` — One or more prerequisites not yet completed

**How it works:**
1. Reads `phase_dependencies.json` — maps phase IDs to lists of prerequisite IDs
2. Reads `run_records.json` — collects all issue IDs with `status == "completed"`
3. Checks that every prerequisite phase ID appears in the set of completed IDs
4. Prints a clear ❌ violation message for each missing dependency

### `scripts/audit-checksums.py`

Verifies file integrity by comparing SHA256 hashes against a stored ledger.

```bash
# From repo root — audit all artifacts listed in the default manifest
python3 scripts/audit-checksums.py

# With custom manifest path
python3 scripts/audit-checksums.py --manifest prismatic_state/artifact_checksums.json
```

**Exit codes:**
- `0` — All checksums match (or manifest missing)
- `1` — One or more files had mismatched hashes or are missing

**How it works:**
1. Opens the checksum manifest (dict of `relative_path → expected_sha256`)
2. For each entry, calculates SHA256 using streaming 4KB chunks
3. Reports missing files and hash mismatches with clear messages

## Pre-Push Hook Integration

Both scripts are designed to be called from the pre-push hook at
`scripts/pre-push-hook.py`. Add these checks to the hook's validation pipeline:

```python
# In pre-push-hook.py — after existing lane/governor checks:
import subprocess

# Verify phase dependencies before allowing push
result = subprocess.run(
    [sys.executable, "scripts/verify-phase-dependencies.py", current_phase],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(result.stdout)
    sys.exit(1)

# Verify artifact integrity
result = subprocess.run(
    [sys.executable, "scripts/audit-checksums.py"],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(result.stdout)
    sys.exit(1)
```

## CI Gate Integration (GitHub Actions)

Add the following step to `.github/workflows/prismatic-ci.yml`:

```yaml
- name: Golden Path Verification
  run: |
    python3 scripts/verify-phase-dependencies.py 14 --state-dir prismatic_state
    python3 scripts/audit-checksums.py --manifest prismatic_state/artifact_checksums.json
```

The dependency verification gate ensures out-of-order phase executions are
blocked. The checksum gate ensures repository checkouts haven't been corrupted.

## State File Formats

### `prismatic_state/phase_dependencies.json`

```json
{
  "14": ["2", "7"],
  "17": ["14"],
  "35": ["17", "30"]
}
```

Keys are phase IDs (strings), values are lists of prerequisite phase IDs.

### `prismatic_state/run_records.json` (production format)

```json
[
  {
    "run_id": "uuid-123",
    "issue_id": "14",
    "agent_name": "jules",
    "status": "completed",
    ...
  }
]
```

The verification script supports both list-of-records and
`{"completed_phases": [...]}` formats.

### `prismatic_state/artifact_checksums.json`

```json
{
  "assets/render_001.mp4": "sha256hexdigest...",
  "assets/sprite_001.png": "sha256hexdigest..."
}
```

Keys are file paths relative to the repo root. Values are SHA256 hex digests.

## Generating the Checksum Ledger

To generate or update `artifact_checksums.json` for a set of files:

```bash
python3 -c "
import hashlib, json, sys
from pathlib import Path
manifest = {}
for path in sys.argv[1:]:
    p = Path(path)
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            h.update(chunk)
    manifest[str(p)] = h.hexdigest()
print(json.dumps(manifest, indent=2))
" assets/render_001.mp4 assets/sprite_001.png > prismatic_state/artifact_checksums.json
```
