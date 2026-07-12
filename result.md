# GRO-3075: PWP Marketing - Analytics Injection (Plausible Default, GTAG Fallback)

## Overview
Every client site built via the Prismatic Web Plugin (PWP) stage requires analytics. This implementation adds a modular injection step to the template compiler that checks for a tenant's analytics configuration and inserts the appropriate analytics script tag into the HTML `<head>`.

## Implementation Details

### 1. Per-Tenant Configuration
The configuration is loaded from:
`plugins/pwp/tenants/<id>/analytics.json`

Supported configurations:
- **Plausible (Default)**: Used if no config exists, or if neither `gtag_id` nor `zaraz: true` is configured.
  - Snippet: `<script defer data-domain="<domain>" src="https://plausible.io/js/script.js"></script>`
  - Domain resolves to the configured `domain` or `plausible_domain` in the JSON, falling back to `<tenant_id>.com` (or `default.com` if no tenant ID is provided).
- **GTAG (Google Analytics 4 Fallback)**: Used if the configuration provides a `gtag_id`.
  - Snippet: Injects the standard global site tag snippet asynchronously loaded for the specified `gtag_id`.
- **Cloudflare Zaraz (Server-side/No-JS)**: Used if `zaraz: true` is set in the configuration.
  - Snippet: `<script src="/cdn-cgi/zaraz/i.js" referrerpolicy="origin"></script>`
  - This allows executing third-party scripts at the edge server-side, removing client-side execution overhead.

### 2. Injection Hook
The injection is wired directly into `render_template` within [plugins/pwp/compiler.py](file:///home/ubuntu/work/prismatic-engine/plugins/pwp/compiler.py). The compiled analytics script snippet is appended directly before the closing `</head>` tag.

---

## Verification & Testing
A robust test suite has been implemented at [tests/test_pwp_analytics.py](file:///home/ubuntu/work/prismatic-engine/tests/test_pwp_analytics.py), covering the following scenarios:
1. **Plausible Default (No Config)**: Verifies default Plausible script injection using the `<tenant_id>.com` domain.
2. **Plausible with Config**: Verifies Plausible injection using the explicitly configured domain/plausible_domain.
3. **GTAG Fallback**: Verifies GTAG injection and configuration of the measurement ID when `gtag_id` is defined.
4. **Zaraz Injection**: Verifies Zaraz script injection when `"zaraz": true` is defined.
5. **No Tenant Defaults**: Verifies Plausible injection with `default.com` domain when no `tenant_id` is supplied.

### Test Execution Output:
```bash
$ .venv_dev/bin/python -m pytest tests/test_pwp_analytics.py
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/ubuntu/work/prismatic-engine
configfile: pyproject.toml
plugins: anyio-4.14.1
collecting ... collected 5 items

tests/test_pwp_analytics.py .....                                        [100%]

============================== 5 passed in 0.46s ===============================
```

### Full PWP test suite verification:
```bash
$ .venv_dev/bin/python -m pytest plugins/pwp/tests/ tests/test_pwp_analytics.py
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/ubuntu/work/prismatic-engine
configfile: pyproject.toml
plugins: anyio-4.14.1
collecting ... collected 28 items

plugins/pwp/tests/test_compiler_determinism.py .                         [  3%]
plugins/pwp/tests/test_oauth_credentials.py ........                     [ 32%]
plugins/pwp/tests/test_theme_diff.py ........                            [ 60%]
plugins/pwp/tests/test_theme_validator.py ......                         [ 82%]
tests/test_pwp_analytics.py .....                                        [100%]

============================== 28 passed in 2.74s ==============================
```
