# Migration notes

This file records state, configuration, CLI, and plugin-contract migrations for public Prismatic Engine releases.

## 0.1.x to 0.2.0

### State files

Prismatic Engine 0.2.0 introduces durable plugin jobs and universal artifact/provenance records. Default state paths are:

```text
$PRISMATIC_STATE_DIR/plugin_jobs.json
$PRISMATIC_STATE_DIR/plugin_artifacts.json
```

If `PRISMATIC_STATE_DIR` is not set, local development uses:

```text
./prismatic_state/plugin_jobs.json
./prismatic_state/plugin_artifacts.json
```

### Environment

Recommended local defaults are in `.env.example`:

```text
PRISMATIC_STATE_DIR=./prismatic_state
PRISMATIC_PLUGIN_JOBS_STATE=./prismatic_state/plugin_jobs.json
PRISMATIC_PLUGIN_ARTIFACTS_STATE=./prismatic_state/plugin_artifacts.json
PRISMATIC_CORS_ORIGINS=http://127.0.0.1:9000,http://localhost:9000
```

Remote deployments should set explicit state paths and HTTPS dashboard origins.

### Config

A public sample config now lives at:

```text
config/prismatic.sample.yaml
```

Copy it to a private local config path before editing:

```bash
cp config/prismatic.sample.yaml config.local.yaml
```

### Plugin manifests

Plugin manifests should target the 0.2 series unless they intentionally require a future core:

```yaml
core_version_constraint: ">=0.2.0, <2.0.0"
```

### API behavior

0.2.0 adds or stabilizes plugin governance, jobs, artifacts, and policy endpoints. Destructive operations such as publish/export/deploy/delete/write require policy evaluation and may require approval.

### CORS behavior

Gateway browser CORS is local-only by default in public builds. If you previously depended on wildcard CORS, set explicit origins instead:

```text
PRISMATIC_CORS_ORIGINS=https://dashboard.example.com
```

Wildcard CORS is rejected while credentials are enabled.

### Release checks

After upgrading, run:

```bash
python scripts/release_smoke.py
python scripts/public_security_readiness_audit.py
```

Expected markers:

```text
RELEASE_SMOKE_OK
PUBLIC_SECURITY_READINESS_OK
```

## Future migration note template

```markdown
## X.Y.Z to A.B.C

### State changes

### Config changes

### CLI changes

### Plugin contract changes

### Operator action required

### Rollback notes
```
