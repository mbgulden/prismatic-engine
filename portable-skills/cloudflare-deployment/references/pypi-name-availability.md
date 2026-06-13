# PyPI Package Name Availability Check

## The Problem

`pypi.org/project/<name>` returns HTTP 200 for EVERYTHING — both real packages and nonexistent names. The HTML page doesn't distinguish between a registered package and a "not found" placeholder.

## The Fix

Use the JSON API endpoint: `pypi.org/pypi/<name>/json`

| Endpoint | Real package | Nonexistent |
|----------|-------------|-------------|
| `/project/<name>` | 200 HTML | 200 HTML (placeholder) |
| `/pypi/<name>/json` | 200 JSON | **404** |

## Quick Check

```bash
for name in my-package another-name cool-project; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "https://pypi.org/pypi/$name/json")
  echo "$code $name"
done
# 200 = taken, 404 = available
```

## Combined Domain + PyPI Audit

When naming a new project, check all three in parallel:

| Asset | Check | Available signal |
|-------|-------|-----------------|
| Domain (.com) | `whois <name>.com` | "No match for domain" |
| GitHub repo | `curl -sI https://github.com/user/<name>` | HTTP 404 |
| PyPI package | `curl -sI https://pypi.org/pypi/<name>/json` | HTTP 404 |

Discovered June 2026 while naming `prismatic-engine` — the HTML page returned 200 but the JSON API correctly returned 404 (available).
