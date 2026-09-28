# PWP credential providers: Ubersuggest OAuth auto-refresh

PWP owns provider credentials used by SEO/content automation. Those credentials must be refreshable without an agent remembering a profile-local script or asking Michael to re-auth every two days.

This runbook documents the first provider integration: **Ubersuggest MCP**.

## Provider contract

Implementation lives in:

- `plugins/pwp/oauth_credentials.py`
- `scripts/pwp credentials ...`
- `PWPDesignTokenPlugin.register_tools()` → `pwp_credentials_refresh`, `pwp_credentials_status`

The provider contract is intentionally small:

1. A provider config declares:
   - `client_id`
   - OAuth token endpoint
   - required scope
   - expected token prefix / minimum token length
2. A refresh command reads a stored refresh token.
3. The token endpoint returns **both** a new access token and a new refresh token.
4. PWP safe-writes both tokens atomically with `0600` permissions.
5. PWP optionally verifies live provider access before returning success.
6. PWP returns only non-secret metadata: token lengths, expiry, scope, and verification summary.

No tool or CLI response may print raw token material.

## Ubersuggest provider

Registered provider name:

```text
ubersuggest
```

Default files:

```text
/tmp/ubs_token
/tmp/ubs_refresh
/tmp/ubs_refresh_response.json
```

Environment overrides:

```bash
UBERSUGGEST_ACCESS_TOKEN_FILE=/path/to/access
UBERSUGGEST_REFRESH_TOKEN_FILE=/path/to/refresh
UBERSUGGEST_REFRESH_RESPONSE_FILE=/path/to/response.json
```

Required scope:

```text
profile domain keywords serp backlinks site_audit content
```

Endpoint:

```text
https://ubersuggest-mcp.neilpatelapi.com/token
```

Live verification, when enabled, checks:

- MCP `auth_status`
- MCP `domain_overview` for `activeoahutours.com`

## CLI usage

Validate local token files without network verification:

```bash
python3 scripts/pwp credentials status ubersuggest
```

Validate token files and run live MCP smoke verification:

```bash
python3 scripts/pwp credentials status ubersuggest --verify
```

Rotate tokens silently; exits nonzero on failure:

```bash
python3 scripts/pwp credentials refresh ubersuggest
```

Rotate tokens with a non-secret JSON receipt:

```bash
python3 scripts/pwp credentials refresh ubersuggest --verbose
```

Skip live MCP smoke verification, useful for isolated unit environments:

```bash
python3 scripts/pwp credentials refresh ubersuggest --no-verify
```

## Tool/API usage

PWP plugin tool definitions:

```python
from prismatic.shipped_plugins.pwp.plugin import PWPDesignTokenPlugin

plugin = PWPDesignTokenPlugin()
plugin.register_tools()
# includes:
# - pwp_credentials_refresh
# - pwp_credentials_status
```

Callable API:

```python
plugin.credentials_status("ubersuggest", verify=True)
plugin.credentials_refresh("ubersuggest", verify=True)
```

Returned payloads are safe to log because they contain lengths/scope/status only, not token values.

## Scheduling

Run refresh before any Ubersuggest-dependent task. Current Ubersuggest access tokens expire in about `172800` seconds (~2 days), so daily refresh is appropriate.

Example cron/system schedule:

```cron
0 3 * * * cd /path/to/prismatic-engine && python3 scripts/pwp credentials refresh ubersuggest
```

Recommended dependency order for AOT:

1. `03:00 UTC` — PWP Ubersuggest credential refresh
2. `04:00 UTC Monday` — Active Oahu weekly rankings report
3. `06:00 UTC Sunday` — Active Oahu competitor content velocity

Cron/no-agent wrappers should be **silent on success** and alert only if this command exits nonzero.

## Safety checks

The refresh path rejects:

- missing refresh token
- empty token files
- suspiciously short tokens
- literal `...` inside tokens, which indicates display/truncation corruption
- tokens with the wrong provider prefix
- token endpoint responses that omit a replacement refresh token
- non-JSON token endpoint responses
- HTTP errors from the token endpoint
- live verification failures when verification is enabled

## Human re-auth boundary

This setup removes routine re-auth work as long as the refresh token remains valid.

A human PKCE/browser re-auth is still required if:

- `/tmp/ubs_refresh` is missing
- the token endpoint returns `invalid_grant`
- the provider revokes the refresh token
- the Ubersuggest account requires login/2FA/CAPTCHA/consent again

When that happens, complete the PKCE flow once, save the fresh access + refresh tokens, and PWP resumes automatic rotation from there.

## Tests

Focused tests:

```bash
python3 -m pytest plugins/pwp/tests/test_oauth_credentials.py -q
```

Broader PWP smoke:

```bash
python3 -m pytest \
  plugins/pwp/tests/test_oauth_credentials.py \
  plugins/pwp/tests/test_theme_validator.py \
  plugins/pwp/tests/test_compiler_determinism.py \
  -q
```
