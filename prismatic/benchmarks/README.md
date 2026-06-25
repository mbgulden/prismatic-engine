# Prismatic Benchmarks — Lane: `prismatic/benchmarks/`

Owner: `agent:ned` (Prismatic Engine lane).

This package houses model-comparison benchmarks for the Prismatic Engine.
Each script follows the same shape:

- `--dry-run` → emit synthetic results without API calls (default-safe).
- Live mode → requires provider credentials in env (`OPENAI_API_KEY`,
  `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`).
- Output → JSON report + Markdown summary in `prismatic/benchmarks/reports/`.

## Available benchmarks

| Script | Issue | Compares | Status |
|--------|-------|----------|--------|
| `deepseek_v_claude.py` | GRO-573 | DeepSeek v4 vs Claude 4 Sonnet (orchestration axes) | scaffold (dry-run only) |

## Adding a new benchmark

1. Drop a new `<comparison>.py` in this directory.
2. Follow the `ModelResult` / `BenchmarkReport` dataclass pattern.
3. Render Markdown via `_render_markdown(...)` — keep style consistent.
4. Add a row to the table above.
5. Open a PR with branch prefix `ned/`.

## Caveats

- All numbers are placeholder until a live run is executed and committed.
- Treat any report under `reports/` as authoritative only after the
  commit message explicitly says "live run N=...".
