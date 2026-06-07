# Code Review Skill

A Prismatic Engine skill for automated pull-request review. Scans changes for security vulnerabilities, performance regressions, and style violations, then posts structured feedback inline.

## Quick Start

Add the skill to your pipeline:

```yaml
pipeline:
  - skill: code-review
    config:
      default_rules:
        - security
        - performance
        - style
      max_comments: 10
      require_review: true
```

The skill hooks into pull-request events (`opened`, `synchronize`) and posts review comments directly on the diff.

## Checks Performed

### Security

| Check             | What It Looks For                                      |
|-------------------|--------------------------------------------------------|
| SQL injection     | Unsanitized variables in raw SQL / query builders      |
| XSS               | Unsafe HTML interpolation in templates or JSX          |
| CSRF              | Missing or weakened anti-forgery tokens                |
| Hardcoded secrets | API keys, passwords, tokens, certificates in source    |
| Auth bypass       | Missing authorization guards on internal endpoints     |
| Unsafe deserialization | `eval`, `pickle`, `yaml.load` on untrusted input  |
| Command injection | Shell calls built from user-controlled strings         |

### Performance

| Check              | What It Looks For                                         |
|--------------------|-----------------------------------------------------------|
| N+1 queries        | Repeated database queries inside loops                    |
| Missing indexes    | Schema diffs that add columns without accompanying indexes |
| Memory leaks       | Growing caches, unclosed resources, retained references   |
| Unnecessary allocs | Object creation inside hot paths, redundant copies        |
| Large payloads     | Fetching entire tables when a projection suffices         |
| Sync I/O in async  | Blocking calls inside async contexts                      |

### Style

| Check            | What It Looks For                                     |
|------------------|-------------------------------------------------------|
| Naming           | Deviations from project conventions (snake_case, etc) |
| Formatting       | Inconsistent indentation, trailing whitespace         |
| Dead code        | Commented-out blocks, unused imports/variables        |
| Complexity       | Deeply nested conditionals, excessive method length   |
| Duplication      | Repeated blocks that should be extracted              |
| Error handling   | Swallowed exceptions, bare `except:` clauses          |

## Configuration

### Rule Categories

You can enable or disable entire categories:

```yaml
config:
  rules:
    - security
    - performance
```

Or pick individual checks from each category:

```yaml
config:
  rules:
    - sql-injection
    - hardcoded-secrets
    - n-plus-one
    - missing-indexes
    - naming
    - formatting
```

### Custom Rules Drop-In

Place additional rule files in `skills/code-review/rules/` as YAML:

```yaml
name: no-raw-sprintf
description: Forbid sprintf-style formatting in log messages
pattern: "sprintf\\(.*%[sdf]"
severity: warning
```

### Comment Thresholds

```yaml
config:
  max_comments: 20          # Caps total inline comments per review
  max_comments_per_file: 5  # Caps comments per file to reduce noise
  skip_tests: true          # Skip test files entirely
  skip_generated: true      # Skip auto-generated files (pb.go, *_gen.go, etc.)
```

### Git Integration

The skill resolves the diff against the PR's base branch automatically. For local usage:

```bash
prismatic skill run code-review --diff HEAD~1 --format terminal
```

## Output

Comments are posted as **pull-request reviews** in the VCS provider (GitHub, GitLab, etc.). Each comment includes:

- The file and line number
- A severity label (`error`, `warning`, `info`)
- A clear explanation of the issue
- A suggested fix where applicable

Example comment:

> **⚠️ Security (hardcoded-secrets)**
> Line 42: Possible hardcoded AWS secret key detected.
> ```
> AWS_SECRET_KEY = "AKIAIOSFODNN7EXAMPLE"
> ```
> **Suggestion:** Use a secrets manager or environment variable instead.

## Rule Severity Levels

| Severity | Meaning                                     | Default behavior              |
|----------|---------------------------------------------|-------------------------------|
| `error`  | Definite bug or vulnerability               | Blocks merge / fails pipeline |
| `warning`| Likely problem that should be addressed     | Highlights in review          |
| `info`   | Style preference or minor nit               | Shown only if configured      |

## Skipping Rules

Authors can bypass the code-review skill on a per-PR basis with a label:

- `skip-code-review` — no review comments posted
- `skip-code-review:style` — skip only style rules

Use sparingly; project admins can override.

## Development

### Testing Rules

```bash
prismatic skill test code-review --fixture tests/fixtures/sql-injection.diff
```

### Adding a New Check

1. Create a YAML rule file under `rules/`.
2. Write a test fixture in `tests/fixtures/`.
3. Run `prismatic skill test code-review` to validate.
