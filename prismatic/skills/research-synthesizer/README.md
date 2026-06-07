# Research Synthesizer

A Prismatic Engine reference skill for gathering, analyzing, and synthesizing research from multiple sources — Google Drive documents, web pages, and local files — into structured, citation-backed summaries.

## Overview

The Research Synthesizer skill automates the tedious process of collecting information across disparate sources and producing a coherent synthesis. It is designed for researchers, analysts, and anyone who needs to quickly make sense of a body of material.

**Key capabilities:**

- **Multi-source ingestion** — Collect content from Google Drive (Docs, Sheets, Slides), web URLs, and local files.
- **Structured output** — Produce summaries organized by theme, finding, source, or custom taxonomies.
- **Citation management** — Every synthesized claim is traceable back to its source with automatic citation rendering.
- **Configurable depth** — Choose between quick overviews (executive brief) or comprehensive deep-dive syntheses.
- **Template-driven configuration** — Use YAML config files to define research scopes, source lists, and output preferences without touching code.

## Quick Start

### 1. Install

Add the skill to your Prismatic Engine configuration or copy the skill directory into your skills path:

```bash
# If using a skills registry or loader that supports reference skills
cp -r research-synthesizer /path/to/your/skills/
```

### 2. Configure

Create a research configuration file. A template is provided at `templates/research-config.yaml`:

```bash
cp templates/research-config.yaml my-research.yaml
```

Edit the configuration to define your research scope:

```yaml
research:
  title: "Market Intelligence — Q2 2026"
  description: "Synthesize competitor analysis from internal docs and web sources"
  sources:
    - type: google_drive
      query: "Q2 competitive analysis"
      mime_types:
        - application/vnd.google-apps.document
        - application/vnd.google-apps.spreadsheet
    - type: web
      urls:
        - https://example.com/industry-report-2026
        - https://news.example.com/competitor-news
    - type: local
      paths:
        - /home/user/research/notes.md
        - /home/user/research/data/*.csv
  depth: comprehensive
  output_format: structured
  include_citations: true
```

### 3. Run

Invoke the skill through Prismatic Engine:

```bash
# Using engine CLI
prismatic run research-synthesizer --config my-research.yaml

# Or trigger via agent dispatch
prismatic dispatch research-synthesizer --title "Deep dive: supply chain risks"
```

### 4. Review Results

The skill outputs:

- **Structured summary** — Organized by theme or source, with findings, evidence, and citations.
- **Source appendix** — Full list of all sources consulted, with access links and timestamps.
- **Confidence estimates** — Where appropriate, the synthesis flags confidence levels based on corroboration across sources.

## Configuration Reference

### Global Config (manifest.yaml)

| Field | Type | Default | Description |
|---|---|---|---|
| `max_sources` | int | 10 | Maximum number of individual sources to process in a single run |
| `output_format` | string | `structured` | Output style: `structured`, `narrative`, `bullet`, `executive` |
| `include_citations` | bool | true | Whether to append source citations to synthesized findings |
| `preferred_depth` | string | `comprehensive` | Analysis depth: `quick`, `balanced`, `comprehensive` |

### Research Config (YAML)

| Field | Sub-field | Description |
|---|---|---|
| `research.title` | — | Human-readable title for the synthesis |
| `research.description` | — | Brief describing the research goal |
| `research.sources[].type` | — | One of: `google_drive`, `web`, `local` |
| `research.sources[].query` | — | Search query (Google Drive) or URL list (web) or file paths (local) |
| `research.depth` | — | Override preferred_depth per research run |
| `research.output_format` | — | Override output_format per research run |
| `research.include_citations` | — | Override include_citations per research run |

## Use Cases

### Competitive Intelligence

Gather competitor product pages, internal sales notes from Google Drive, and industry news articles. Synthesize into a structured brief with identified strengths, weaknesses, and market positioning.

### Literature Review

Point the skill at a set of academic PDFs, Drive docs with reading notes, and relevant web articles. Produce a thematic synthesis organized by key arguments, methodologies, and gaps.

### Project Kickoff Research

Before starting a new initiative, collect all existing internal documentation (Drive), relevant standards or regulations (web), and previous project retrospectives (local). Get a comprehensive "state of play" document in minutes.

### Due Diligence

Aggregate financial data from Drive Sheets, company filings from the web, and internal assessment notes. Generate a structured due diligence report with source-traceable assertions.

## Source Connectors

### Google Drive

Queries Drive by filename, content, or metadata. Supports:

- **Docs** — Full text extracted to Markdown
- **Sheets** — Tabular data exported as CSV
- **Slides** — Slide content extracted as plain text
- **PDFs / text files** — Read natively

### Web

Accepts a list of URLs. Content is fetched and converted to text for analysis.

### Local Files

Accepts glob patterns and explicit file paths. Supported formats include `.md`, `.txt`, `.csv`, `.json`, `.yaml`, `.pdf` (text-extractable).

## Extending the Skill

The Research Synthesizer is designed as a **reference skill** — a starting point. To customize:

- **Add new source types** — Extend the source connector registry in the skill logic.
- **Change output templates** — Modify the structured output formatter to produce, e.g., JSON, HTML, or Notion-formatted documents.
- **Add custom taxonomies** — Inject domain-specific classification tags (e.g., "regulatory", "technical", "financial") into the synthesis pipeline.

## License

This skill is provided as part of the Prismatic Engine reference skill collection.
