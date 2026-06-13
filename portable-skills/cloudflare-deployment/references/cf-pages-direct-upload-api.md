# Cloudflare Pages Direct Upload via REST API

Use when wrangler is not installed or when deploying static sites programmatically without Git integration.

## Quick Reference

```bash
# 1. Create project
curl -X POST "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/pages/projects" \
  -H "X-Auth-Email: {EMAIL}" \
  -H "X-Auth-Key: {GLOBAL_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-project","production_branch":"main"}'

# 2. Create zip of site
python3 -c "
import zipfile, os
os.chdir('/path/to/site')
with zipfile.ZipFile('/tmp/deploy.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in ('.git', 'docs')]
        for f in files:
            if not f.endswith('.py') and not f.endswith('.md'):
                zf.write(os.path.join(root, f))
"

# 3. Generate manifest
MANIFEST=$(python3 -c "
import zipfile, json
files = {}
with zipfile.ZipFile('/tmp/deploy.zip', 'r') as zf:
    for info in zf.infolist():
        if not info.is_dir():
            files['/' + info.filename] = {'size': info.file_size, 'hash': ''}
print(json.dumps(files))
")

# 4. Upload deployment
curl -X POST "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/pages/projects/my-project/deployments" \
  -H "X-Auth-Email: {EMAIL}" \
  -H "X-Auth-Key: {GLOBAL_KEY}" \
  -F "file=@/tmp/deploy.zip" \
  -F "manifest=$MANIFEST"
```

## Verified Account IDs

| Account | ID | Auth |
|---------|-----|------|
| GrowthWebDev (Michael) | `196c1798da487413b0281ccc570f05a1` | X-Auth-Email: michael@growthwebdev.com + X-Auth-Key |
| Active Oahu Tours | `3e13f120ec7532f0bc8ac0bc9fcbf7108` | Bearer token |

## Auth Discovery

If you have a Global Key but not the account ID:
```bash
curl -s "https://api.cloudflare.com/client/v4/accounts" \
  -H "X-Auth-Email: {EMAIL}" \
  -H "X-Auth-Key: {KEY}" | python3 -c "
import json, sys
for acct in json.load(sys.stdin).get('result', []):
    print(f\"{acct['name']}: {acct['id']}\")
"
```

## Manifest Format

The manifest is a JSON string mapping file paths to metadata:
```json
{
  "/index.html": {"size": 45233, "hash": ""},
  "/assets/sprites/player_0.png": {"size": 444, "hash": ""}
}
```

Paths must start with `/`. The `hash` field can be empty string.

## Pitfalls

- **`manifest` must be a form field, not a file upload.** Use `-F "manifest=$JSON_STRING"` not `-F "manifest=@file.json"`.
- **Global Key vs API Token**: Global Keys use `X-Auth-Email` + `X-Auth-Key` headers. API Tokens use `Authorization: Bearer <token>`. Mixing them returns 7000 or 10000 errors.
- **Error 7000 "No route for that URI"**: Wrong auth method or missing account ID in URL.
- **Error 8000096 "manifest field expected"**: Manifest must be sent as a form field string, not missing or wrong format.
- **13MB zips deploy fine** — tested with 18 sprite files + index.html.
- **Deployment takes ~30 seconds** — check status via `GET /accounts/{id}/pages/projects/{name}/deployments/{id}`.
- **Bash `ARG_MAX` exceeded: manifest too large for curl (CRITICAL)** — When deploying repos with 1000+ files (e.g., game repos with asset directories), the manifest JSON string can exceed bash's argument size limit. Symptom: `bash: /usr/bin/curl: Argument list too long`. The Python manifest generator succeeds but passing the string to curl via `-F "manifest=$MANIFEST"` fails. **Fix:** Do the entire upload in Python using `urllib.request` with manual multipart form-data construction (see Python snippet below). This bypasses bash argument limits entirely. For a 3.6GB zip with 3810 manifest entries, the Python approach works reliably.
- **Pages API tokens (Bearer) work for upload endpoint** — The `/accounts/{id}/pages/projects/{name}/deployments` endpoint accepts Bearer auth with `cfut_`-prefixed Pages tokens. Global Key is NOT required for direct upload, despite the endpoint path containing `/accounts/`.

### Python Multipart Upload (When Bash Fails)

```python
import zipfile, os, json, urllib.request

# Build zip (same as bash version)
exclude_dirs = {'.git', '__pycache__', 'dist'}
os.chdir('/path/to/site')
with zipfile.ZipFile('/tmp/deploy.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for f in files:
            if not f.endswith('.py') and not f.endswith('.md'):
                zf.write(os.path.join(root, f))

# Generate manifest
files_map = {}
with zipfile.ZipFile('/tmp/deploy.zip', 'r') as zf:
    for info in zf.infolist():
        if not info.is_dir():
            files_map['/' + info.filename] = {'size': info.file_size, 'hash': ''}
manifest = json.dumps(files_map)

# Build multipart form-data manually
boundary = '----FormBoundary7MA4YWxkTrZu0gW'
with open('/tmp/deploy.zip', 'rb') as f:
    file_data = f.read()

body = b''
body += f'--{boundary}\r\n'.encode()
body += f'Content-Disposition: form-data; name="file"; filename="deploy.zip"\r\n'.encode()
body += f'Content-Type: application/zip\r\n\r\n'.encode()
body += file_data
body += f'\r\n--{boundary}\r\n'.encode()
body += f'Content-Disposition: form-data; name="manifest"\r\n\r\n'.encode()
body += manifest.encode()
body += f'\r\n--{boundary}--\r\n'.encode()

# Upload
req = urllib.request.Request(
    f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/pages/projects/{PROJECT}/deployments',
    data=body,
    headers={
        'Authorization': f'Bearer {TOKEN}',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
    },
    method='POST'
)
resp = urllib.request.urlopen(req, timeout=300)
result = json.loads(resp.read())
print(result['result']['url'] if result.get('success') else result.get('errors'))
```
