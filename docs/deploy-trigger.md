# Deploy Trigger

**WS4 of the deploy-portability plan.** The whole trigger story in one place:
how a merge becomes a deploy today, what a new repo needs to get deploys,
what "working" looks like in the logs, and every failure mode with its fix.

## 1. How the trigger works (the one important correction)

There is **no inbound GitHub webhook**. The deploy is triggered by
`.github/workflows/post-merge-deploy.yml` — a GitHub Actions job that runs
on a **self-hosted runner** on the deploy host (webtop-hermes) and POSTs an
HMAC-signed JSON payload to the deploy receiver:

```
push to main (or manual workflow_dispatch)
  -> post-merge-deploy.yml runs on the self-hosted runner
  -> builds the payload, signs it with HMAC-SHA256 (DEPLOY_HMAC_SECRET)
  -> POST http://<DEPLOY_RECEIVER_HOST or localhost>:9460/deploy
       Header: X-Hub-Signature-256: sha256=<hex signature>
  -> pe.deploy.receiver routes on payload["repository"], verifies the
     signature, runs the deploy pipeline (build release, flip symlinks,
     restart gateway, 90s health check, auto-rollback on failure)
  -> alerts.log gets PostMergeDeploySucceeded / PostMergeDeployFailed
  -> the workflow run goes green (success) or RED (any failure)
```

Nothing in GitHub ever calls into the receiver directly. Anyone wanting a
true GitHub webhook (push events delivered to an endpoint) instead of this
Actions trigger should see §10 — it is a named alternative, not the
recommended path.

## 2. The payload (exact fields)

Built by the workflow with `jq`, sent compact (`-c`). The receiver
(`create_deploy_receiver_app()`) reads these fields:

| Field | Sent by the workflow | Notes |
|---|---|---|
| `pr_sha` | `${{ github.sha }}` | 40-char merge commit SHA the deploy validates against the mirror |
| `pr_title` | first line of `git log -1 --pretty=%B` | |
| `deployer` | `${{ github.actor }}` | who merged / who dispatched |
| `merged_at` | `date -u +"%Y-%m-%dT%H:%M:%SZ"` | ISO-8601 UTC |
| `repository` | `${{ github.repository }}` | `owner/repo` — **required** (WS1). The receiver routes on this field and **fail-closes** when it is missing or not in the registry |

The receiver also accepts (but the workflow does not currently send)
`pr_number`, `commits`, `ref`, and `dry_run`.

The signature is computed over the exact bytes of the compact payload:

```bash
HMAC_SIG=$(echo -n "$PAYLOAD_JSON" | openssl dgst -sha256 -hmac "$HMAC_SECRET" | sed 's/^.*= //')
```

`echo -n` matters: a trailing newline would produce a different signature.

## 3. The receiver's intake path

`POST /deploy` on `:9460`, in order:

1. **Parse JSON.** Invalid body -> `400 "Invalid JSON body"`.
2. **Require `repository`.** Missing or non-string -> `400` with
   `"payload is missing the required 'repository' field (owner/repo); refusing to route an unidentified deploy"`.
   The receiver never guesses which repo a deploy is for.
3. **Registry lookup** (`load_repo_registry()` — reloaded per request, so
   registry edits take effect without a receiver restart). Unknown repo ->
   error log, a `PostMergeDeployFailed` alert naming the repo, then `400`
   `"unknown repository '<owner/repo>': not in the deploy registry"`.
4. **Resolve the HMAC secret for the routed repo** (`get_repo_hmac_secret`):
   the repo's per-repo var (e.g. `DEPLOY_HMAC_SECRET_MBGULDEN_PRISMATIC_ENGINE`)
   in the environment or in any deploy `.env` file (cwd, repo root,
   `~/.prismatic/.env`), else the shared `DEPLOY_HMAC_SECRET` the same way,
   else the dev fallback (non-strict mode) — else `500` refusing loudly:
   `"no HMAC secret for repository '<owner/repo>': <PER_REPO_VAR> is not set and the shared DEPLOY_HMAC_SECRET fallback is unavailable: ..."`.
5. **Verify HMAC-SHA256.** Missing or wrong signature -> `401 "Invalid or missing HMAC signature"`.
6. **Run the deploy** in a worker thread (so `/health` stays responsive),
   bounded by the overall deploy timeout (`PRISMATIC_DEPLOY_TIMEOUT_S`,
   default 1800s). Timeout -> `500 "deploy timed out after 1800s"`.
7. **Report.** Failed deploy -> `500 {"status": "failed", "deploy_record": {...}}`;
   success -> `200 {"status": "success", ...}`. The workflow uses
   `curl -s -f`, so any non-2xx turns the workflow run RED — a failed
   deploy is never a green lie.

## 4. Per-repo setup checklist (making a new repo deploy)

Each repo that wants post-merge deploys needs **all four**:

1. **The workflow file** (or a reusable workflow — see §9). Copy
   `.github/workflows/post-merge-deploy.yml` into the repo. The payload
   **must** include `repository: ${{ github.repository }}` — without it the
   receiver refuses every trigger with a 400.
2. **A runner the repo can access.** Today that is a self-hosted runner on
   the deploy host (webtop-hermes), labelled `self-hosted, linux, x64`.
   GitHub-hosted runners are a silent no-op: nothing listens on their
   localhost, so the trigger dies and nobody is told.
3. **The secrets.** As GitHub Actions repository secrets on the repo:
   - `DEPLOY_HMAC_SECRET` — the signing key. The receiver side needs the
     matching secret: either the per-repo var
     `DEPLOY_HMAC_SECRET_<OWNER>_<REPO>` (e.g.
     `DEPLOY_HMAC_SECRET_ACME_WIDGETS`) or the shared `DEPLOY_HMAC_SECRET`,
     in the receiver's environment or its `~/.prismatic/.env` /
     `~/.prismatic/secrets/deploy-receiver.env` file. See §5 for the
     per-repo vs shared model.
   - `DEPLOY_RECEIVER_HOST` — where the runner POSTs. Defaults to
     `localhost` when unset (same-box runner + receiver). Point it at a
     remote receiver's address for WS7-style fan-out (see §6).
4. **A registry entry on the receiver.** The repo must be in the receiver's
   repo registry (`PRISMATIC_DEPLOY_REPOS_FILE` JSON, or
   `PRISMATIC_DEPLOY_REPOS` comma list; see `pe/deploy/config.py` and WS1).
   Registry is reloaded on every trigger, so no receiver restart is needed.

Also generate a fresh secret per repo (or per account — see §5), store it as
the repo's Actions secret, and put the matching value in the receiver env.

Guided onboarding does the machine half for you (validate, register, mirror,
secret) and prints the exact GitHub-side steps:

```bash
prismatic deploy add-repo OWNER/REPO [--dry-run]   # onboard, nothing deploys
prismatic deploy list-repos                       # registry readiness
prismatic deploy validate-repo OWNER/REPO         # dry-run proof, no side effects
```

## 5. Secrets: per-repo vs shared

- **Sender side is already per-repo:** GitHub Actions repository secrets are
  scoped to the repo whose workflow reads them. Nothing to invent — reuse.
- **Receiver side:** `DEPLOY_HMAC_SECRET_<OWNER>_<REPO>` (sanitized uppercase,
  e.g. `acme/widgets` -> `DEPLOY_HMAC_SECRET_ACME_WIDGETS`) takes precedence;
  the shared `DEPLOY_HMAC_SECRET` is the fallback. A repo with neither gets a
  loud refusal at trigger time (500), and the receiver's startup log names
  every routed repo missing a secret (see §7).
- **Model choice is yours:** per-repo secrets isolate repos from each other
  (rotate one without touching the others — the right default for multiple
  GitHub accounts); the shared secret is simpler for one owner with many
  repos. Both work; the doctor check (§12) lists which repos rely on the
  shared fallback.
- **Strict mode:** `PRISMATIC_STRICT_SECRETS` set -> no dev fallback; a
  missing secret is a hard refusal. Unset -> the well-known dev secret
  `prismatic-deploy-hmac-secret-v1` is used with a logged warning. **Never
  rely on the dev fallback in production.**
- **Rotation:** set the new secret on the receiver first, then update the
  repo's Actions secret. Between the two, triggers fail closed (401), never
  half-signed.

## 6. DEPLOY_RECEIVER_HOST — remote receivers and WS7 fan-out

`DEPLOY_RECEIVER_HOST` is the workflow's only knob for *where* the trigger
goes. `http://${RECEIVER_HOST:-localhost}:9460/deploy`:

- **Unset** -> `localhost`: the same-box setup (runner and receiver on one
  host), which is today's webtop-hermes arrangement.
- **Set to a tailnet address or MagicDNS name** -> the runner POSTs to a
  remote receiver over the tailnet. That is WS7's lightweight fan-out:
  each node runs its own receiver, and a repo's workflow targets the node
  it should deploy to. No SSH, no shared filesystem — just an HTTPS-capable
  POST to another receiver that has the repo in its own registry.
- The receiving node must be reachable from the runner host on port 9460
  (tailnet membership covers this; the public internet does not).

The alternative for true push-from-GitHub instead of Actions-pull: §10.

## 7. What "working" looks like

**Receiver log** (`~/.prismatic/logs/deploy-receiver.log` on webtop-hermes;
the systemd unit is `prismatic-deploy-receiver.service`). At every receiver
start:

```
deploy receiver startup: bind 0.0.0.0:9460; routing 1 repo(s): mbgulden/prismatic-engine
deploy receiver: HMAC secret configured for mbgulden/prismatic-engine
```

**A routed repo with no secret configured gets a loud warning instead of
silence:**

```
deploy receiver: NO HMAC secret configured for repository acme/widgets (checked DEPLOY_HMAC_SECRET_ACME_WIDGETS and shared DEPLOY_HMAC_SECRET in env and .env files); triggers for this repo will fail closed -- set the per-repo var or the shared secret before expecting deploys
```

(Note: "configured" here means a real secret in env or a deploy `.env`
file — the well-known dev fallback does not count.)

**`alerts.log`** (`~/.prismatic/alerts.log`, JSONL, AlertRouter schema).
Success:

```json
{"timestamp": "...", "name": "PostMergeDeploySucceeded", "severity": "info",
 "summary": "deploy deploy-a1b2c3d4 succeeded: gateway at a1b2c3d4e5f6",
 "details": "deploy_id=deploy-a1b2c3d4 pr_sha=a1b2... pr_number=532 version_dir=prismatic-engine-a1b2c3d4 duration_ms=..."}
```

Failure: `PostMergeDeployFailed` (critical) with `failure_reason=...` and
`rolled_back=true/false` in the details. A refused trigger (unknown repo)
also lands here with `reason=unknown-repo-not-in-registry`.

**The workflow run** goes green, and the gateway serves the new code.

## 8. Failure modes — exact error and fix

| # | Symptom | Exact error | Fix |
|---|---|---|---|
| 1 | Workflow fails at "Calculate Payload & HMAC Signature" | `::error::DEPLOY_HMAC_SECRET repository secret is missing!` (step exits 1) | Add `DEPLOY_HMAC_SECRET` under the repo's Settings -> Secrets and variables -> Actions |
| 2 | Every trigger refused, 400 | `"payload is missing the required 'repository' field (owner/repo); refusing to route an unidentified deploy"` | The repo's workflow is an old copy without the field — copy the current `post-merge-deploy.yml` (WS1 added `repository: ${{ github.repository }}`) |
| 3 | Trigger refused, 400 + alert | `"unknown repository '<owner/repo>': not in the deploy registry"`; alert `PostMergeDeployFailed`, details contain `reason=unknown-repo-not-in-registry` | Add the repo to the receiver's registry (`PRISMATIC_DEPLOY_REPOS_FILE` / `PRISMATIC_DEPLOY_REPOS`). No restart needed — the registry reloads per trigger |
| 4 | Trigger refused, 500 | `"no HMAC secret for repository '<owner/repo>': DEPLOY_HMAC_SECRET_<OWNER>_<REPO> is not set and the shared DEPLOY_HMAC_SECRET fallback is unavailable: ..."` | Set the per-repo var or the shared secret in the receiver's environment / `.env`; the startup log (§7) warns about this at boot |
| 5 | Trigger refused, 401 | `"Invalid or missing HMAC signature"` | Secret mismatch between the repo's Actions secret and the receiver's env. Re-set both to the same value; watch for trailing whitespace/newlines (the workflow uses `echo -n`); confirm the header `X-Hub-Signature-256: sha256=<hex>` is sent |
| 6 | curl can't connect; workflow step fails | `::error::Deploy receiver call failed -- the gateway was NOT redeployed. Check the receiver log at ~/.prismatic/logs/deploy-receiver.log` (curl exit != 0) | Receiver is down: `systemctl --user status prismatic-deploy-receiver.service`; it binds `0.0.0.0:9460` |
| 7 | Trigger goes nowhere / wrong host | Same curl error as #6, or the POST lands on a box with no receiver | Check the repo's `DEPLOY_RECEIVER_HOST` secret — unset means `localhost`. For a remote receiver it must be the receiver's reachable address on port 9460 |
| 8 | Workflow ran but nothing deployed, no receiver log entry | (no error — the workflow comments call this out) | The job ran on a GitHub-hosted runner: nothing listens on its localhost. Pin `runs-on` to the self-hosted runner |
| 9 | Deploy fails mid-pipeline, 500 | `{"status": "failed", "deploy_record": {...}}`; alert `PostMergeDeployFailed` with `failure_reason=...` and `rolled_back=true/false` | Read `failure_reason` in `alerts.log` and the receiver log. If the gateway health checks failed, the receiver already rolled back to the previous release automatically |
| 10 | Deploy hangs, then 500 | `"deploy timed out after 1800s"` | The deploy exceeded `PRISMATIC_DEPLOY_TIMEOUT_S` (default 1800). The receiver stays up; investigate the stuck step in the receiver log (a previous unbounded rsync hung it for 37 minutes) |
| 11 | Deploy refused: SHA unknown in mirror | `pr_sha '<sha>' is not a commit in <src>; refusing to deploy` (pre-WS5 behavior) | The merge beat the 15-minute mirror sync timer. WS5 (fetch-before-validate) removes this race; until then, wait for the timer or re-run |
| 12 | Receiver won't start | `RuntimeError: PRISMATIC_DEPLOY_SOURCE_REPO is not set` at startup | The receiver fail-fasts without an explicit deploy source. Set `PRISMATIC_DEPLOY_SOURCE_REPO` (the unit reads it from `~/.prismatic/secrets/deploy-receiver.env` on webtop-hermes) |
| 13 | Receiver restarts in a loop / won't bind | Port 9460 already in use, or the env file is missing (the unit has no `-` prefix on `EnvironmentFile`, so a missing file fails the unit by design) | Free the port / restore `~/.prismatic/secrets/deploy-receiver.env` (0600) |

All refusals are loud by design: missing secret, unknown repo, bad
signature, unreachable receiver, unknown SHA — none of them silently skip.

## 9. One reusable workflow vs per-repo workflows

Either works; the routing key is always the `repository` field:

- **Per-repo workflow:** copy `post-merge-deploy.yml` into each repo. The
  payload's `${{ github.repository }}` identifies the caller automatically.
- **Reusable workflow:** one shared workflow (`workflow_call`) that each repo
  calls with its own runner labels and secrets. The caller still sends its
  own `github.repository` value, so the receiver routes correctly — the
  reusable workflow must pass the field through, not hardcode a repo name.

Every repo still needs its own runner access and its own `DEPLOY_HMAC_SECRET`
Actions secret; a reusable workflow shares the YAML, not the secrets.

## 10. Alternative: true GitHub webhooks (named, not recommended)

If someone wants GitHub itself to push delivery events to an endpoint
instead of the Actions trigger, the receiver's `POST /deploy` already
accepts a GitHub-webhook-style call: it expects the `X-Hub-Signature-256:
sha256=<hex>` header and an HMAC-SHA256-signed JSON body — the same
conventions as a GitHub webhook delivery. What it does **not** do is parse
GitHub's push-event schema (no `repository.full_name` lookup, no
`after`/`ref` handling); a shim in front of it would have to translate the
push event into the receiver's payload shape (with `repository`,
`pr_sha`, ...).

The receiver is not exposed to the public internet on webtop-hermes, so a
true webhook needs a tunnel or reverse proxy in front of `:9460` (e.g. a
tailnet-serve endpoint, Cloudflare Tunnel, or an nginx reverse proxy with
TLS), plus firewall rules limiting the source. This is a named alternative
for anyone who insists on it — the Actions trigger (§1) remains the
supported path.

## 11. Deferred: polling fallback

A receiver-side poller (check GitHub for new `main` commits on an
interval) is named as a fallback for trigger-less machines but is not
built. Michael's standing rule is event-based first; polling is honest
future work in its own workstream if he wants it.

## 12. Doctor `trigger` check — SPEC (implements in WS3's doctor deploy section)

> This section is a precise specification, not code. WS3 owns the doctor;
> it adds a `trigger` check to the deploy section of `prismatic/doctor.py`
> implementing exactly this. No `pe/deploy/doctor.py` may be created.

**Check name:** `trigger`. **Inputs:** the loaded repo registry
(`pe.deploy.config.load_repo_registry()`), the receiver bind config, and
the secret-resolution rules from `pe.deploy.receiver`.

**It reports, per routed repo:**

1. `receiver_listening`: is something listening on the receiver's bind
   address/port (`RECEIVER_BIND_HOST` / `RECEIVER_PORT`, defaults
   `0.0.0.0:9460`)? Probe locally (TCP connect). FAIL with the exact
   address:port when nothing listens. (Remote-receiver setups: report the
   configured `DEPLOY_RECEIVER_HOST` target as informational; the doctor
   checks the local receiver only.)
2. `secret_configured`: for each repo in the registry, is a real HMAC secret
   present — the repo's per-repo var (`DEPLOY_HMAC_SECRET_<OWNER>_<REPO>`)
   or the shared `DEPLOY_HMAC_SECRET`, in env or the deploy `.env` files
   (the same lookup as `pe.deploy.receiver._secret_configured` — the dev
   fallback does NOT count)? WARN per repo when missing, naming the repo
   and the expected var name. Include a `shared_fallback` flag per repo:
   `true` when the repo resolves only via the shared secret (INFO — the
   WS3 secret-coverage check), `false` when it has its own per-repo var.
3. `registry_resolves`: does every routed repo resolve (`registry.get`)
   with a mirror dir that exists on disk? FAIL naming the first repo whose
   mirror dir is missing.

**Output contract:** the check emits one row per repo with fields
`repository`, `receiver_listening` (bool), `secret_configured` (bool),
`shared_fallback` (bool), `mirror_present` (bool), plus a top-level
`bind` string (`host:port`). Verdict is FAIL if the receiver is not
listening or any mirror is missing; WARN if any repo lacks a configured
secret; OK otherwise.

**Tests (WS3):** stubbed checks — a fake listening socket, hermetic env
with fixture registry files, and the `.env`-file lookup stubbed so no real
secrets leak into the test. Cover: all-green, missing secret -> WARN
naming the repo, nothing listening -> FAIL with address:port, missing
mirror dir -> FAIL naming the repo.
