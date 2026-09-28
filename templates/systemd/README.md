# Portable systemd template

This directory holds the **portable** systemd unit template for the Prismatic Engine.
It is the starting point for any new machine: set `User=`, review `ExecStart` and any
`Environment=` lines for the target host, then install to `/etc/systemd/system/`.

## templates/ vs scripts/

- `templates/systemd/` — **portable.** No hardcoded usernames, home directories, or
  release paths. Use this on fresh installs.
- `scripts/*.service` — **this box's installed units** (webtop-hermes). They pin
  `/home/ubuntu/.prismatic/venv_stable` and the runtime checkout on purpose, guarded by
  `prismatic/review_factory/tests/test_systemd_unit_install_paths.py`. Copy from them as
  reference, but do not install them verbatim on another machine.

The public first-user path (`pip install -e ".[gateway]"` + smoke test) does not need
systemd at all; see README.md.
