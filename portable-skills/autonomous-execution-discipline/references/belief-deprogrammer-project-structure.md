# Belief Deprogrammer — Project Structure Reference

> **Last verified:** June 9, 2026
> **Repo:** `~/work/belief-deprogrammer/`
> **Git remote:** `github.com/mbgulden/belief-deprogrammer.git`
> **CF Pages:** `belief-deprogrammer.pages.dev` (auto-deploys on push to `master`)

## Directory Layout

```
~/work/belief-deprogrammer/
├── landing/                    # Static site deployed to CF Pages
│   ├── index.html              # Workbook generator (main app)
│   ├── philosophy.html         # Philosophy & guiding principles (201 lines)
│   ├── sources.html            # Sources & credits (626 lines, 35+ researchers)
│   └── .wrangler/              # CF Pages config
├── engine/                     # Python backend
│   ├── server.py               # API server
│   ├── generator.py            # Belief workbook generator
│   ├── gate_beliefs.py         # Gate-specific belief pairs
│   ├── gate_themes.py          # Gate theme mappings
│   └── context_injector.py     # Context injection for belief generation
├── michael-workbook-v9.md      # Michael's personal workbook (latest)
├── michael-workbook-v9.pdf
├── michael-workbook-v8.md
├── michael-workbook-v8.pdf
└── .gitignore
```

## Research Artifacts

Located at `~/work/research/`:

| File | Size | Builds On |
|---|---|---|
| `bradley-nelson-framework.md` | 593 lines, 39KB | GRO-999 — Emotion/Body/Belief Code deep dive |
| `allie-duzett-methodology.md` | 580 lines, 38KB | GRO-1000 — 30 Days of Belief Work, 12 books |

These research artifacts feed the `sources.html` credits page and the engine's belief generation logic.

## Content Pipeline

1. **Research** (agent:fred, type:research) → `~/work/research/<topic>.md`
2. **Content creation** (agent:fred, pipeline:content-seo) → `landing/<page>.html`
3. **Git push** → CF Pages auto-deploy → `belief-deprogrammer.pages.dev/<page>.html`

## Known Issues

- GRO-1010 (Voice recordings) depends on banter line data from GRO-957 (Backlog)
- GRO-957 (Banter System — 504 Lines Across 10 Biomes) needs to be moved to Todo and completed before TTS voice generation can proceed
