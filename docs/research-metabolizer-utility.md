# AGY Research Metabolizer CLI Utility Spec

This document specifies the design, command surface, artifact layout, and implementation of `prismatic-research`, a harness-agnostic CLI utility to initialize AGY-ready research folders and task assets.

---

## 1. CLI Command Shape & Parameters

The `prismatic-research` CLI tool initializes structured research folders conforming to the `agy-as-research-metabolizer` / `agy-research-metabolizer` skill contract without coupling to any external agent harness.

### CLI Verb Structure

```bash
prismatic-research init \
  --topic "<research topic>" \
  --bundle <bundle_type> \
  --use-case "<downstream use case>" \
  --out <output_directory> \
  [--anchors <path_or_url_list>] \
  [--audience "<audience>"] \
  [--depth <depth>] \
  [--no-expansion]
```

### Options Specification

| Flag | Long Option | Type | Default | Description |
|---|---|---|---|---|
| `-t` | `--topic` | `str` | *Required* | Core subject or question (e.g. `"WASM Runtimes"`). |
| `-b` | `--bundle` | `str` | `standard` | Report bundle format: `brief`, `standard`, `deep`, `architecture`, `content-engine`, `competitive`, `golden-path`. |
| `-u` | `--use-case` | `str` | Same as `--topic` | Downstream product, architecture, or content objective. |
| `-o` | `--out` | `str` | `research/<slug>/` | Path where directory structure will be created. |
| `-a` | `--audience` | `str` | `"Engineering Team"`| Intended audience of the compiled report. |
| | `--anchors` | `str` | `""` | Comma-separated list of initial files, folders, or URLs. |
| | `--depth` | `str` | `standard` | Research rigor depth: `quick`, `standard`, `deep`. |
| | `--no-expansion` | `bool` | `False` | Disables agent autonomy to seek external sources. |

### Slugification Rule
If `--out` is not specified, a slug is automatically generated from the `--topic` string:
1. Convert text to lowercase.
2. Replace non-alphanumeric character blocks with single hyphens (`-`).
3. Remove leading and trailing hyphens.
4. Prefix with `research/`. E.g., `--topic "WASM Runtimes: Wasmtime vs Wasmer"` becomes `research/wasm-runtimes-wasmtime-vs-wasmer/`.

---

## 2. Directory Layout & Artifact Scaffolding

Executing the `init` command generates the following files at the specified output directory:

```
<output_dir>/
├── AGY_RESEARCH_BRIEF.md
├── SOURCE_MAP.md
├── EVIDENCE_LEDGER.md
├── AGY_LAUNCH_COMMANDS.md
└── REPORTS/
    ├── 01-history.md
    ├── 02-current-state.md
    └── ... (bundle-specific reports)
```

### 2.1 AGY_RESEARCH_BRIEF.md
The primary directive file read by AGY to understand the goals, bounds, and requirements of the research.

```markdown
# AGY Research Brief: {Topic}

## Objective
Research {Topic} to support the following objective: {Use Case}.

## Downstream Use
- {Use Case}

## Audience
- {Audience}

## Known Context / Anchors
- **Local files/repos:**
  {Anchors listed as links}
- **Assumptions:**
  - Initial context provided via anchors.

## Freedom to Investigate
{Freedom message based on --no-expansion}

## Report Bundle Spec
- **Type:** {Bundle}
- **Depth:** {Depth}

## Required Artifacts
The following reports must be generated in the `REPORTS/` subdirectory:
{Required reports bulleted list}

## Quality Bar
- Every major claim must reference the Evidence Ledger (e.g., `[Ledger #L01]`).
- Separate facts from interpretations and recommendations.
- Label all findings with a confidence rating: `[HIGH]`, `[MEDIUM]`, or `[LOW]`.
```

### 2.2 SOURCE_MAP.md
Catalogs all raw inputs and newly discovered external resources, ensuring clear origin tracking.

```markdown
# Source Map

This file tracks all sources utilized during this research cycle.

| ID | Source/Path/URL | Type | Relevance | Confidence | Notes |
|---|---|---|---|---|---|
| S01 | {Anchor Link} | Anchor File | High | High | Initial input anchor. |
```

### 2.3 EVIDENCE_LEDGER.md
The single source of truth for raw facts, benchmarks, and code snippets compiled during the research.

```markdown
# Evidence Ledger

Every factual claim in the final reports must link back to an ID in this ledger.

| ID | Claim / Finding | Source | Confidence | Rationale | Date/Timestamp |
|---|---|---|---|---|---|
| L01 | Example finding. | [Source Map #S01] | HIGH (Verified) | Verified via anchor inspection. | {Current Time} |
```

### 2.4 AGY_LAUNCH_COMMANDS.md
Pre-compiled launch options to copy-paste directly into shell/terminal, minimizing friction.

```markdown
# AGY Launch Commands

Use these commands to invoke AGY for this research task.

### 1. Pure Research Mode (Read-Only)
`agy --print "Read <path_to_brief>. Search external sources and populate <output_dir>."`

### 2. Repo-Anchored Audit Mode
`agy --print --add-dir <workspace_root> "Read <path_to_brief>. Audit local files and compile reports under <output_dir>."`
```

---

## 3. Harness-Agnostic Skills Integration

The utility does **not** import or depend on any agent harness (such as Hermes or other frameworks). Instead, it integrates via **data-contract specification**:

1. **Self-Describing Directives:** The generated `AGY_RESEARCH_BRIEF.md` contains strict markdown templates that trigger the `agy-as-research-metabolizer` skill rules inside the agent's prompt context.
2. **Implicit Matches:** By outputting standard file names (`EVIDENCE_LEDGER.md`, `SOURCE_MAP.md`), any skill matching system scanning the file tree will automatically pair the workspace with the correct validation or publishing pipeline (for example, `prismatic-validation-pipeline`).

---

## 4. Website Content Engine Support

When the `--bundle content-engine` option is specified, the output is geared toward structured ingestion by static site generators or content management systems (the content engine):

1. **JSON Frontmatter Templates:** Scaffolded reports in `REPORTS/` include YAML/JSON frontmatter schemas:
   ```markdown
   ---
   title: "Audience Map: {Topic}"
   seo_keywords: ["keyword1", "keyword2"]
   meta_description: "Enter meta description here."
   unique_id: "content-research-audience-map"
   ---
   ```
2. **Structured Outputs:** It creates content-specific report files:
   - `REPORTS/01-audience-map.md`
   - `REPORTS/02-source-themes.md`
   - `REPORTS/03-expert-vocabulary.md`
   - `REPORTS/04-search-content-opportunities.md`
   - `REPORTS/05-article-cluster-plan.md`
   - `REPORTS/06-authority-building-strategy.md`
   - `REPORTS/07-recommended-content-briefs.md`
3. **Automated Conversion:** Reports contain instructions telling AGY to output clean, semantic HTML-compatible structures and avoid markdown quirks that break content parser engines.
