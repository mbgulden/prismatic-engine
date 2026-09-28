# Cloudflare Pages — Multi-Account Setup (Pages ≠ DNS)

## When to Use
Setting up a new Cloudflare Pages project where the Pages project lives in one Cloudflare account but the custom domain's DNS zone is in a DIFFERENT Cloudflare account. Common for Michael: Pages projects under the Active Oahu account (`3e13f120ec...`), domains under the GrowthWebDev account.

## Credential Matrix

| Operation | Account | Auth Method | Credential |
|-----------|---------|-------------|------------|
| Pages CRUD (create project, upload, deploy) | Active Oahu (`3e13f120...`) | Bearer token | `CLOUDFLARE_API_TOKEN` (prefix `cfat_`) from `~/.hermes/profiles/kai/.env` |
| DNS records (CNAME, zone list) | GrowthWebDev (`196c1798...`) | Global Key | `CLOUDFLARE_GROWTHWEB_API_KEY` (prefix `cfk_`) + `CLOUDFLARE_GROWTHWEB_EMAIL` from runtime env |
| Domain verification polling | Active Oahu (same as Pages) | Bearer token | Same as Pages CRUD |
| GitHub OAuth connection | Dashboard only | Manual | Cannot be done via API — requires Cloudflare Dashboard |

## End-to-End Flow

### 1. Create Pages Project (Active Oahu account)

```python
# Use Bearer token auth
headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
account_id = '3e13f120ec7532f0bc8ac0bc9bfc7108'

body = {
    "name": "my-project",
    "production_branch": "main"
}
POST /accounts/{account_id}/pages/projects
```

Response includes: `subdomain` (`.pages.dev` URL), `id` (project UUID).

### 2. Add Custom Domain to Pages (Active Oahu account)

```python
POST /accounts/{account_id}/pages/projects/{name}/domains
body = {"name": "example.com"}
```

Response: `{"status": "initializing", "verification_data": {"status": "pending"}, "validation_data": {"method": "http"}}`

The `validation_data.method` is usually `"http"` — meaning Pages verifies by making an HTTP request to the domain, checking the response comes from Pages infrastructure.

### 3. Find the Domain Zone (GrowthWebDev account)

```python
# Use Global Key auth
headers = {
    'X-Auth-Email': email,
    'X-Auth-Key': api_key,
    'Content-Type': 'application/json'
}
GET /zones?name=example.com
```

Zone ID is needed for DNS record creation. If the domain isn't found, list all zones: `GET /zones?per_page=50`.

### 4. Add CNAME DNS Records (GrowthWebDev account)

```python
# Root domain
POST /zones/{zone_id}/dns_records
body = {
    "type": "CNAME",
    "name": "example.com",
    "content": "my-project-abc.pages.dev",  # From step 1 subdomain
    "ttl": 1,
    "proxied": True  # Orange-cloud; CF handles apex CNAME flattening
}

# WWW subdomain
body = {"type": "CNAME", "name": "www.example.com", "content": "...", "proxied": True}
```

**Both records are needed.** Pages will aliase the custom domain to the deployment URL once verified.

### 5. Upload Initial Deployment (Active Oahu account)

Domain verification uses the `http` method — it needs a LIVE deployment to succeed. Without a deployment, verification stays `pending` forever.

**Direct upload via REST API** (no GitHub, no wrangler):

```bash
# Create zip with at least index.html
python3 -c "
import zipfile
with zipfile.ZipFile('/tmp/deploy.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write('index.html')
"

# Generate manifest (JSON string mapping file paths → metadata)
MANIFEST=$(python3 -c "
import zipfile, json, hashlib
files = {}
with zipfile.ZipFile('/tmp/deploy.zip', 'r') as zf:
    for info in zf.infolist():
        if not info.is_dir():
            content = zf.read(info.filename)
            h = hashlib.sha256(content).hexdigest()
            files['/' + info.filename] = {'size': info.file_size, 'hash': h}
print(json.dumps(files))
")

# Upload — manifest is a form FIELD string, not a file
curl -X POST \
  "https://api.cloudflare.com/client/v4/accounts/{id}/pages/projects/{name}/deployments" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/tmp/deploy.zip" \
  -F "manifest=$MANIFEST"
```

**Pitfall:** The `manifest` parameter must be a **form field string** (JSON), not a file. Use `-F "manifest=$JSON_STRING"` not `-F "manifest=@file.json"`.

### 6. Poll Domain Until Active

```python
GET /accounts/{account_id}/pages/projects/{name}/domains/example.com
# Status progression: initializing → pending → active
# verification_data.status: pending → active
# validation_data.status: initializing → active
```

Once `status == "active"`, the custom domain serves from Pages. SSL is auto-provisioned (Google CA).

### 7. GitHub Connection (Dashboard Only — Cannot Be Automated)

GitHub auto-deploy requires OAuth authorization that CANNOT be done via API. The user must:

1. Go to Cloudflare Dashboard → Workers & Pages → [project] → Settings → Builds & Deployments
2. Connect Git Repository → Authorize GitHub → Select repo + branch
3. Dashboard URL pattern: `https://dash.cloudflare.com/{account_id}/pages/view/{project_name}`

Once connected, every push to the production branch auto-deploys.

## Pitfalls

- **Domain in wrong account — check both:** `GET /zones?name=` in both accounts. If the domain isn't in the Pages account, it's in the other one. The GrowthWebDev account uses Global Key auth, not Bearer.
- **Deployment required for HTTP verification:** The `validation_data.method: "http"` means Pages needs a live deploy to verify. Without at least one deployment, domain verification stays `pending` indefinitely. Upload a placeholder `index.html` to trigger verification.
- **CNAME at apex domain:** RFC says you can't have a CNAME at the root — but Cloudflare flattens it automatically when `proxied: true`. Always set `proxied: true`.
- **Edge propagation delay:** New deployments may return 500 or 403 for the first 30–120 seconds while the edge caches propagate. The API shows `stage: deploy, status: success` during this window. Wait and retry — do not assume the deployment failed.
- **Shell escaping with `python3 -c`:** When working with Cloudflare API keys and JSON payloads containing quotes and braces, shell-level `python3 -c "..."` frequently fails with syntax errors. **Always write a `.py` file to `/tmp/` and execute with `python3 /tmp/script.py`** — the terminal inherits env vars. Multiple attempts in this session failed on shell escaping before switching to file-based execution.
- **`/user/tokens/verify` rejects Global Keys:** This endpoint ONLY accepts Bearer tokens. For Global Key validation, use `GET /user` with `X-Auth-Email` + `X-Auth-Key` headers instead.
- **Remove/re-add doesn't fix stale verification:** If domain verification stalls with "CNAME record not set", deleting and re-adding the domain (after DNS is configured) triggers a fresh verification cycle. The old domain ID changes but the flow is identical.
- **Two CNAME records needed:** Both the apex (`example.com`) and `www.example.com` need CNAME records pointing to the Pages subdomain. Omitting `www` means `www.example.com` won't resolve.
