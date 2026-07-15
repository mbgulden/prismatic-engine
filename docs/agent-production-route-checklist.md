# Agent Production Route Checklist

**Applies before touching any production-facing Prismatic route, dashboard surface, gateway path, public API, or authenticated operator page.**  
**Required checklist marker:** `AGENT_PRODUCTION_ROUTE_CHECKLIST_OK`

This checklist turns the Prismatic Production Durability Standard into an agent-executable gate. If any required box is unchecked, do not claim the route is fixed.

---

## 0. Required final labels

Use these labels in your proof packet:

```text
verification_scope=ad_hoc_targeted | canonical_suite_green | skipped_auth_required
production_state=not_deployed | deployed_unverified | public_verified | visual_verified
```

Never call ad-hoc targeted proof canonical suite green.

---

## 1. Branch / worktree gate

- [ ] I am on a clean production-safe branch/worktree.
- [ ] I am not patching the mutable live checkout as the production source of truth.
- [ ] I recorded branch, commit SHA, and `git status --short --branch`.
- [ ] I locked every file I will edit through the swarm lock protocol.
- [ ] I know whether production uses an immutable release, dedicated production checkout, container image, or systemd source path.
- [ ] I can read back the production source path/commit after deploy.

Proof fields:

```text
branch=
commit=
worktree_clean_before=
production_source_model=
production_source_path=
```

---

## 2. Local reproduce gate

- [ ] I reproduced the exact route/problem locally before claiming a fix.
- [ ] I checked `/health` or equivalent local service health.
- [ ] I inspected the local route table for the target path.
- [ ] I captured the local failure status/body/DOM marker.

Suggested commands:

```bash
curl -sS -D /tmp/local-health.headers -o /tmp/local-health.body http://127.0.0.1:9000/health
curl -sS -D /tmp/local-route.headers -o /tmp/local-route.body 'http://127.0.0.1:9000/workspace-tree?file=okf/operations/INDEX.md'
python3 - <<'PY'
import sys
sys.path.insert(0, '.')
from prismatic.gateway.server import app
for route in app.routes:
    print(getattr(route, 'path', None), sorted(getattr(route, 'methods', []) or []))
PY
```

---

## 3. Patch-scope gate

- [ ] The patch is in a reviewed branch/PR.
- [ ] The patch is minimal for the production route problem.
- [ ] The patch does not hide a backend route problem behind a frontend fallback.
- [ ] The patch does not expose arbitrary filesystem reads or private files.
- [ ] The PR description lists changed files and route(s) affected.

Proof fields:

```text
files_changed=
route_scope=
PR=
```

---

## 4. Local route / API proof gate

- [ ] Backend files compile or build.
- [ ] Frontend JavaScript touched by the fix passes syntax checks.
- [ ] Local route returns expected status.
- [ ] Local API returns expected schema/body marker.
- [ ] Browser/DOM proof shows the route is not blank and not a 404 body.

Suggested commands:

```bash
python3 -m py_compile prismatic/gateway/server.py
node --check /tmp/extracted-dashboard-script.js  # if inline JS changed
curl -sS -D /tmp/local-route.headers -o /tmp/local-route.body 'http://127.0.0.1:9000/workspace-tree?file=okf/operations/INDEX.md'
```

---

## 5. Security / path traversal proof gate

Required for workspace-tree-like routes:

- [ ] Safe repo/workspace-relative path works.
- [ ] `../../etc/passwd` is blocked.
- [ ] `%2e%2e/%2e%2e/etc/passwd` is blocked.
- [ ] `/etc/passwd` is blocked unless explicitly inside an allowed workspace root, which it should not be.
- [ ] Secret-like paths are not previewed.
- [ ] Preview size is capped or otherwise safe.

Suggested checks:

```bash
curl -sS -o /tmp/safe.body -w '%{http_code}\n' 'http://127.0.0.1:9000/api/workspace-tree/preview?file=docs/security.md'
curl -sS -o /tmp/traversal.body -w '%{http_code}\n' 'http://127.0.0.1:9000/api/workspace-tree/preview?file=../../etc/passwd'
curl -sS -o /tmp/encoded.body -w '%{http_code}\n' 'http://127.0.0.1:9000/api/workspace-tree/preview?file=%2e%2e/%2e%2e/etc/passwd'
curl -sS -o /tmp/absolute.body -w '%{http_code}\n' 'http://127.0.0.1:9000/api/workspace-tree/preview?file=/etc/passwd'
```

---

## 6. Browser / screenshot proof gate

- [ ] I loaded the exact route in a browser after the patch.
- [ ] I checked the browser console for fatal JavaScript errors.
- [ ] I captured a screenshot or compact DOM proof.
- [ ] I proved the page renders useful content, not a blank page.
- [ ] I proved no CDN failure can blank the critical shell, or I documented the remaining fallback gap.

Proof fields:

```text
browser_route=
console_errors=
screenshot_path=
DOM_markers=
CDN_fallback_status=
```

---

## 7. Deploy / restart / reload proof gate

- [ ] Deployment/restart/reload was intentional.
- [ ] I recorded the exact command or release action.
- [ ] I read back service state after restart.
- [ ] I read back source path/commit after restart.
- [ ] I reran `/health` after restart.

Suggested commands:

```bash
sudo systemctl restart prismatic-gateway
systemctl show -p ActiveState,ActiveEnterTimestamp,ExecMainPID prismatic-gateway
sudo nginx -t
sudo systemctl reload nginx
curl -sS http://127.0.0.1:9000/health
```

---

## 8. Public / authenticated proof gate

- [ ] I checked the public/authenticated URL after deploy.
- [ ] I recorded status, headers, and body/DOM marker.
- [ ] If auth blocks proof, I labeled it `skipped_auth_required` with the exact response.
- [ ] I did not substitute local proof for public proof without saying so.

Proof fields:

```text
public_url=
public_status=
auth_state=
public_DOM_marker=
public_screenshot_path=
```

---

## 9. Rollback / cleanup proof gate

- [ ] I identified rollback target and command.
- [ ] I documented whether rollback requires service restart/reload.
- [ ] I documented expected rollback verification.
- [ ] I cleaned temp files or listed retained artifacts intentionally.
- [ ] I released locks after commit/merge or after abandoning the edit.
- [ ] The production source/worktree remains durable and clean.

Proof fields:

```text
rollback_target=
rollback_command=
rollback_verification=
cleanup=
locks_released=
production_worktree_clean_after=
```

---

## 10. Required proof packet closeout

A route fix closeout must include this minimum closeout block:

```text
AGENT_PRODUCTION_ROUTE_CHECKLIST_OK

Route:
Branch/commit:
PR:
Local reproduce:
Local proof:
Security/path safety:
Browser/screenshot proof:
Deploy/restart/reload:
Public/authenticated proof:
Rollback:
Verification scope:
Cleanup:
Remaining blockers:
```

If the route is not fully fixed, say `Remaining blockers:` plainly and do not use a final OK marker.

---

## 11. Review gate and prompt imports

Before submitting production-facing work, import the review question block from:

```text
docs/production-durability-review-gate.md
```

When preparing AGY/Fred/Kai/Ned/Jules work, paste or link the reusable brief from:

```text
docs/prompts/production-durability-agent-brief.md
```

Required markers:

```text
PRODUCTION_DURABILITY_REVIEW_GATE_OK
PRODUCTION_DURABILITY_AGENT_BRIEF_OK
PRODUCTION_WORKTREE_DURABILITY_PLAN_OK
```

