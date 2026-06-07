# Prismatic Docs Generator

> **Skill:** `docs-generator` · **Version:** 1.0.0 · **Author:** Prismatic Engine

Auto-generate clean, structured markdown documentation from your codebase. Reads code structure, docstrings, type annotations, and module metadata — then produces a ready-to-publish documentation site.

---

## Quick Start

```bash
# Install the skill via Prismatic Engine CLI
prismatic install skill docs-generator

# Generate docs for a project
prismatic run docs-generator --input ./my_project --output ./docs
```

Or use the config-driven approach:

```bash
prismatic run docs-generator --config docs-config.yaml
```

---

## Supported Input Types

| Type | Description | Supported |
|------|-------------|-----------|
| Python (`.py`) | Modules, classes, functions, docstrings | ✅ Full |
| JavaScript / TypeScript (`.js`, `.ts`, `.jsx`, `.tsx`) | Exports, JSDoc, interfaces | ✅ Full |
| Go (`.go`) | Packages, structs, functions, comments | ✅ Full |
| Rust (`.rs`) | Modules, structs, enums, traits, doc comments | ✅ Full |
| Java (`.java`) | Classes, interfaces, methods, Javadoc | ✅ Full |
| YAML / JSON (`.yaml`, `.json`) | Schema definitions, config files | ✅ Structural |
| Markdown (`.md`) | Already-written docs (passed through) | ✅ Passthrough |
| Shell (`.sh`, `.bash`, `.zsh`) | Function comments, usage blocks | ✅ Basic |
| Dockerfile | Instruction-level breakdown | ✅ Basic |
| Makefile | Target list with comments | ✅ Basic |

---

## Output Formats

| Format | Description | Config Key |
|--------|-------------|------------|
| **Markdown** | Clean `.md` files in a mirrored directory tree | `markdown` |
| **Single-page Markdown** | Everything concatenated into one `README.md` | `markdown-single` |
| **HTML** | Rendered HTML with optional CSS theming | `html` |
| **JSON** | Structured JSON (for CI/API consumption) | `json` |
| **YAML** | Structured YAML (for config pipelines) | `yaml` |

Default format is **markdown (multi-file)**.

---

## Configuration

Copy the template config and customise:

```bash
cp templates/docs-config.yaml ./docs-config.yaml
# Edit docs-config.yaml with your project settings
```

### Key Options

| Option | Default | Description |
|--------|---------|-------------|
| `source_dir` | `./src` | Root directory of your source code |
| `output_dir` | `./docs` | Where generated docs are written |
| `output_format` | `markdown` | One of: `markdown`, `markdown-single`, `html`, `json`, `yaml` |
| `include_examples` | `true` | Extract and render `# Example` / `@example` blocks |
| `include_api_ref` | `true` | Generate full API reference section |
| `max_depth` | `3` | Maximum nesting depth for module/submodule traversal |
| `ignore_patterns` | `[]` | Glob patterns to exclude (e.g. `**/test/**`, `**/vendor/**`) |
| `title` | Project name | Documentation site title |
| `description` | — | Site-level description, rendered at the top |
| `version` | — | Project version, rendered in the header |

---

## Examples

### Python project

```yaml
source_dir: ./src/my_package
output_dir: ./docs/api
output_format: markdown-single
max_depth: 2
ignore_patterns:
  - "**/tests/**"
  - "**/__pycache__/**"
```

### Multi-language monorepo

```yaml
source_dir: ./
output_dir: ./website/docs
output_format: markdown
include_examples: true
include_api_ref: true
max_depth: 4
ignore_patterns:
  - "**/node_modules/**"
  - "**/vendor/**"
  - "**/.git/**"
```

---

## Result Structure

When using `output_format: markdown`, the output mirrors your source tree:

```
docs/
├── index.md               # Overview / readme
├── api-reference.md       # Full API reference (if include_api_ref)
├── src/
│   ├── my_package/
│   │   ├── README.md      # Package-level doc
│   │   ├── core.md        # core.py extracted docs
│   │   └── utils.md       # utils.py extracted docs
│   └── ...
└── examples/              # Extracted example blocks (if include_examples)
    └── ...
```

---

## CI / Pipeline Usage

```yaml
# .github/workflows/docs.yml
- name: Generate documentation
  run: |
    prismatic run docs-generator \
      --config docs-config.yaml \
      --fail-on-warnings
```

---

## Supported Docstring Styles

- **reStructuredText** (Sphinx-style, Python)
- **Google-style** (Napoleon-compatible, Python)
- **NumPy-style** (Python)
- **JSDoc / TSDoc** (JavaScript / TypeScript)
- **Javadoc** (Java)
- **Godoc** (Go)
- **Rustdoc** (Rust)
- **Plain /** block comments (Shell, YAML, etc.)

---

## License

Prismatic Engine — Internal Tooling. See root repository license.
