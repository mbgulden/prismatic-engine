# Prismatic Engine scripts

## Pipeline metrics dashboard

Run `python3 scripts/pipeline_dashboard.py` for the human-readable pipeline health view, `--summary` for a one-line status, or `--json` for machine-readable output.

The human dashboard panes are intentionally operator-facing. Each pane must explain:

- **Shows** — the data being summarized.
- **Why** — why that signal matters for pipeline health or governance.
- **Next** — the default action an operator should take when the pane is weak, empty, or red/yellow.

This keeps the dashboard from becoming a passive metrics dump. If a new pane is added, add the same context contract so the next operator knows what to do without reading the source.
