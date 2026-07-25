# Stable CLI entrypoints

These console scripts are the public entrypoints declared in `pyproject.toml`. Keep them stable across patch releases. Deprecate before removal.

| Entrypoint | Purpose | Stability |
|---|---|---:|
| `prismatic` | Main operator CLI for status, doctor, init, serve, task, journal, visual verification, worktrees, crons, and canonical AGY workflow | stable |
| `plugin-load-gate` | Verify shipped plugins load against the current core version | stable |
| `prismatic-engine` | Dispatcher runtime entrypoint | stable for internal/runtime use |
| `prismatic-engine-skills` | Skill management CLI | stable for runtime use |
| `prismatic-lock` | Lock management CLI | stable for runtime use |
| `prismatic-admin` | Admin/database CLI | stable for operator use |
| `prismatic-journal` | Journal CLI | stable |
| `prismatic-journal-snapshot` | Journal snapshot helper | stable |
| `prismatic-linear-import` | Linear import helper | stable but requires configured Linear credentials |
| `prismatic-second-witness` | Second-witness journal helper | stable |
| `prismatic-gateway` | FastAPI Gateway/dashboard server | stable |
| `prismatic-api` | Legacy/API server entrypoint | maintained for compatibility |

## Smoke commands

Credential-free commands:

```bash
prismatic --help
prismatic agy contract
prismatic agy customizations validate
plugin-load-gate
prismatic-gateway --help
python scripts/release_smoke.py
```

Commands that may require local config or credentials:

```bash
prismatic-engine serve
prismatic-linear-import --help
```

## Deprecation policy

Before removing or renaming a public CLI entrypoint:

1. add a deprecation note to `CHANGELOG.md`
2. document the replacement here
3. keep the old entrypoint as an alias for at least one minor release when practical
4. add an upgrade note to `docs/migrations.md`

## Version output

The package version is declared in `pyproject.toml` and mirrored in `prismatic.__version__`. Release readiness checks fail if they diverge.
