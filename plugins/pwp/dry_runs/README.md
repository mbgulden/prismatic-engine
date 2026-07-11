# PWP Phase 9.5 fixture dry-run

This directory holds the fixture-only end-to-end dry-run for [GRO-3740](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3740).

The dry-run models the master-plan path:

```text
fixture intake -> route plan -> module plan -> Linear tree artifact -> theme scaffold -> verification report
```

Safety boundaries:

- no production deploy;
- no Linear mutation;
- no `dispatch:ready` labels;
- output goes to a caller-provided artifact directory;
- the generated scaffold is a fixture theme package, not a live client site.

Run it:

```bash
python3 plugins/pwp/dry_runs/e2e_theme_dry_run.py \
  --fixture plugins/pwp/dry_runs/fixtures/sentinel_itad_fixture.json \
  --output-root /tmp/pwp-theme-dry-run \
  --run-id gro-3740-sentinel-fixture \
  --json
```

Verify it:

```bash
python3 -m pytest plugins/pwp/tests/test_e2e_theme_dry_run.py -q
python3 plugins/pwp/dry_runs/e2e_theme_dry_run.py --run-id gro-3740-sentinel-fixture --json
python3 -m json.tool /tmp/pwp-theme-dry-run/gro-3740-sentinel-fixture/verification-report.json
```
