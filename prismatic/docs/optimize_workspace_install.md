# First-run workspace optimization

`prismatic-engine optimize-workspace <workspace>` prepares a fresh clone for low-overhead agent dispatch before the first worker run.

The command is run automatically by `install.sh` after package installation and `prismatic-engine init`:

```bash
prismatic-engine optimize-workspace "$PRISMATIC_HOME"
```

If `PRISMATIC_HOME` is unset during a source install, `install.sh` uses the cloned repository directory as the workspace root.

## What it writes

The optimizer creates or refreshes a managed Prismatic block in all three first-run context guard files:

- `.gitignore`
- `.geminiignore`
- `.antigravityignore`

The block excludes generated dependency trees, runtime state, caches, databases, logs, and large media outputs that are not needed for first-run routing. Re-running the command is idempotent: the managed block is replaced in place rather than duplicated.

## Plugin overhead guard

The optimizer also writes `.prismatic/disabled_plugins.json` with the high-overhead AGY plugins disabled for first run:

- `search-documents`
- `visualization-server`

When `~/.gemini/antigravity-cli/settings.json` exists, matching permission allow-list entries are stripped. When an `agy-bin` plugin manager is present, the optimizer attempts a best-effort `plugin disable` call, but missing AGY binaries do not fail installation.

## Verification

Focused coverage lives in `prismatic/test_workspace_optimizer.py` and checks:

1. the three ignore files are created;
2. the managed ignore block contains high-cardinality directory patterns;
3. the disabled-plugin manifest is present under `.prismatic/`;
4. AGY settings permissions are stripped when present;
5. repeated optimizer runs are idempotent.
