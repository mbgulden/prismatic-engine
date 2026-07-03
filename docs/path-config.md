# Prismatic path configuration

Prismatic Engine resolves its workspace paths from environment variables first,
then from repository configuration where appropriate. This keeps the engine
usable outside the original `/home/ubuntu` host layout.

## Lock registry

The default lock registry is:

```text
${PRISMATIC_HOME:-~}/.antigravity/swarm_locks.json
```

A repository can override it in `PRISMATIC_ENGINE.yaml`:

```yaml
locks:
  file: "${PRISMATIC_HOME}/.antigravity/swarm_locks.json"
```

Both the `prismatic-lock` CLI and the Git pre-push hook honor that setting and
expand environment variables plus `~` before reading the lock file.

If `PRISMATIC_ENGINE.yaml` is missing, lock handling falls back to the default
`PRISMATIC_HOME` path. If the Git pre-push hook cannot parse the repository
config at all, it preserves the existing hook behavior: it reports that no
Prismatic configuration was found and exits without lane/lock enforcement.

## Operator notes

- Set `PRISMATIC_HOME` in systemd or shell environments for non-`/home/ubuntu`
  deployments.
- Keep `PRISMATIC_ENGINE.yaml` committed with portable `${PRISMATIC_HOME}` paths
  rather than host-specific absolute paths.
- The pre-push hook reads the same configured lock file as the lock CLI, so lane
  governance and manual locking stay pointed at one registry.
