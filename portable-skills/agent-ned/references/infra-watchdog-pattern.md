# Infrastructure Watchdog Pattern — Production-Readiness Sweeps

> **Source.** Ned's standing pattern for keeping Hermes + prismatic-engine infrastructure boring-and-reliable. Proven on GRO-2037 (Linear API lint) + GRO-2058..2062 (production-readiness sweep, Jun 2026).

## When to use this

Use when:
- A silent-failure mode has been discovered (typical: an incident reveals "X was broken for 3 weeks")
- Quarterly hygiene audit (combine with Linear API rate-limit review)
- Michael asks "what is Ned working on?" with no current urgent issue
- A new component is added to the engine (catch silent gaps early)

## The pattern in 5 steps

### 1. Survey

Build a list of every component in scope:

```bash
# Cron jobs (what runs, when, who)
hermes cron list --profile orchestrator > cron-inventory.txt

# Scripts (engine + per-profile)
find /home/ubuntu/work/prismatic-engine/scripts -name "*.sh" -o -name "*.py" | head -30
find ~/.hermes/profiles/*/scripts -name "*.py" | head -30

# State databases (what's persisted)
find ~/.prismatic /home/ubuntu/.prismatic -name "*.db" 2>/dev/null

# Log directories (what's written)
find ~/.prismatic /tmp -name "*.log" 2>/dev/null | head -20

# Config files (where state lives)
find /home/ubuntu/work/prismatic-engine/config ~/.prismatic -name "*.yaml" 2>/dev/null
```

### 2. Audit for silent-failure modes

For each category, ask "what would fail silently?":

| Category | Question | Silent failure |
|----------|----------|----------------|
| **Logging** | Is there log rotation? | Disk fills, /tmp wiped on reboot |
| **Databases** | Is VACUUM scheduled? | Fragmentation, slow queries |
| **Databases** | Are retention policies set? | Tables grow unbounded |
| **Monitoring** | Are DB sizes checked daily? | Silent growth invisible |
| **CI** | Does the lint run on PRs? | Regressions ship |
| **Auth** | Is HMAC verified on every webhook? | Spoofed events accepted |
| **Rate limits** | Is the budget gated? | API quota burned silently |
| **Secrets** | Are tokens rotatable? | Old tokens valid forever |
| **Cleanup** | Are temp files purged? | Disk fills |
| **State** | Are schema migrations versioned? | Old + new code collide |

### 3. File findings as separate Linear issues

For each finding:
- **Title**: action-oriented ("Add log rotation", "Gate `prismatic/X.py` through LinearBudget")
- **Priority**: tied to blast radius (P1 = active data loss, P3 = hygiene, P4 = cosmetic)
- **Description**: includes the file:line reference, the impact, and a fix pattern from prior art
- **Label**: `agent:ned` (this is Ned's lane) + `type:infra-readonly` if engine-side
- **Parent**: create a parent issue to bundle a sweep's findings (e.g. GRO-2063 bundled 5 production-readiness findings)

### 4. Ship mechanical fixes first, defer design

Priority order:
1. **Safety** — things that prevent data loss (rotation, retention, VACUUM)
2. **Observability** — health checks so silent gaps become loud
3. **CI** — prevent regressions (lint integration)
4. **Design** — dashboards, fancy analytics (defer until the boring stuff is done)

### 5. Verify by simulating the failure mode

Don't trust the fix until you've watched the failure mode disappear:

```bash
# Example: verify log rotation actually rotates
logrotate -d ~/.prismatic/logrotate.d/prismatic-engine
# Should show: "considering log /home/ubuntu/.prismatic/logs/engine.log"
# Should NOT show errors

# Example: verify lint catches a synthetic bypass
echo 'import requests; requests.post("https://api.linear.app/graphql", json={})' > /tmp/test_bypass.py
bash scripts/check_linear_cron_rate.sh 2>&1 | grep test_bypass
# Should show: ❌ test_bypass.py: makes Linear API calls without LinearBudget gate
```

### Worked scripts shipped

| Script | Purpose | Cron | Issue |
|--------|---------|------|-------|
| `scripts/check_linear_cron_rate.sh` | Detects LinearBudget bypass regressions | None (run on PR) | GRO-2037 |
| `scripts/vacuum-state-dbs.sh` | SQLite VACUUM on `prismatic_state/*.db` | Weekly Sun 03:00 UTC | GRO-2059 |
| `scripts/purge-retention.py` | Retention policy (drop dedup_log > 14d, etc.) | Daily 03:30 UTC | GRO-2060 |

## Worked example: GRO-2037 sweep

The full sweep that produced GRO-2037 + GRO-2053..2057 + GRO-2058..2062:

### Trigger

GRO-2034 audit found that `agent_dispatcher.py::_linear_gql()` (the legacy fallback path) was bypassing `LinearBudget`. Fixed by wrapping in `check_and_consume()`. **But** this was a one-time audit; how do we prevent the next similar bug?

### Survey

Looked at every Python file in `prismatic/` that could call Linear. Found 8 candidates:
- 3 already gated (`dispatcher.py`, `linear/__init__.py`, `linear/budget.py`)
- 5 ungated (listed below)

### Audit

For each ungated file:
- Read the file
- Identified the exact call site (`grep -n "api.linear.app\|LinearBudget"`)
- Verified the call really does bypass the gate (no LinearBudget import + api.linear.app URL in same file)

### File

Created **GRO-2037** (parent: "lint script must include webhook handlers"). Filed **GRO-2053..GRO-2057** as one-per-file follow-ups with the canonical fix pattern from GRO-2034.

### Ship

Mechanical order:
1. GRO-2037: lint script (1-2 hours) — landed
2. GRO-2053..2057: per-file gates (~30 min each) — small mechanical refactors

### Verify

Re-ran the lint after shipping each gate. Confirmed the count drops from 5 ungated → 4 → 3 → ... → 0.

## What to NOT do

- **Don't file one giant "fix everything" issue.** Split into atomic, shippable items. Each should be 1-2 hours.
- **Don't wait until after a silent failure causes user-visible damage.** Sweep quarterly.
- **Don't write a fancy dashboard before closing the boring gaps.** Observability without safety = expensive theatre.
- **Don't conflate "this would be nice to have" with "this prevents outages."** Be honest about severity.

## Anti-patterns

| Anti-pattern | Why bad | Better |
|--------------|---------|--------|
| Hand-wave "we'll monitor this manually" | Manual monitoring = no monitoring | Cron + alert |
| Fix in a single mega-PR | Hard to review, hard to revert | One fix per issue |
| Skip the lint because "we know" | The next agent doesn't know | Lint at PR time |
| Optimistic VACUUM cron | If cron fails, no one notices | Wrap in a watchdog |

## Templates

### "Add X rotation/retention/health-check" issue

```markdown
## Why
[Component X] currently [fails silently / grows unbounded / etc.].

## Impact
[What happens when this fails? When will it fail?]

## Scope
1. [Action 1]
2. [Action 2]
3. [Verification: simulate the failure mode and confirm fix]

## Acceptance
- [ ] [Measurable criterion]
- [ ] [Cron entry visible via `hermes cron list`]
- [ ] [Test recipe included in docstring or reference]

## Hand-off
[Time estimate] of mechanical work. [Which agent] appropriate.
```

### "Gate Y file through LinearBudget" issue

```markdown
## Why
GRO-2037's lint detected that `Y` makes Linear API calls without going through
`LinearBudget.check_and_consume()`. This is the same class of silent-loophole
bug that GRO-2034 fixed for `agent_dispatcher.py`.

## What needs fixing
The file uses [call mechanism] to call the Linear API. We need to wrap each
call site with:

```python
from prismatic.linear.budget import linear_budget
if not linear_budget.check_and_consume("<filename>"):
    raise Exception("Linear API rate limit exceeded")
# ... existing Linear API call ...
```

Specific context for this file:
[where the call is, what it does]

## Acceptance
- [ ] All Linear API calls in this file wrapped with `linear_budget.check_and_consume()`
- [ ] Lint script reports this file as gated (run `bash scripts/check_linear_cron_rate.sh`)
- [ ] Existing tests still pass
- [ ] No new dependencies introduced

## Pattern reference
GRO-2034's fix (in `~/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py`):
[code snippet]
```

## Reference

- **GRO-2037** — the Linear API lint script
- **GRO-2058..2062** — the production-readiness sweep findings
- **GRO-2063** — the parent initiative
- `prismatic-engine/docs/linear-rate-limit-audit.md` — the underlying rate-limit audit
- `prismatic-engine/scripts/check_linear_cron_rate.sh` — the lint script

---

*Author: Ned. Use this checklist the next time you're tempted to defer "boring" infrastructure work.*