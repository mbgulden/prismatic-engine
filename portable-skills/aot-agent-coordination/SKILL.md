---
name: aot-agent-coordination
description: Shared coordination protocol for all Hermes agents (Fred, Ned, Kai, AGY, Jules) working on the Active Oahu Tours GitHub repo. One source of truth for git workflow, branch conventions, group chat protocol, deployment governance, and collision recovery.
category: content-strategy
triggers:
  - merging code to deploy-fresh or main
  - starting work on an AOT task
  - seeing another agent active on the same repo
  - preparing a production deployment
  - joining the All Hermes Agents group chat
  - conflict or collision on deploy-fresh
always-delegate: false
---

# Active Oahu Tours — Agent Coordination Protocol

## 1. All Hermes Agents Group Chat Protocol

The **All Hermes Agents** group is the single coordination channel for everyone. Use it for coordination signals only — not conversation, not status dumps, not speculation.

### What belongs in the group:
- **Claiming work:** `"Claiming content/ for 20 min"` or `"Claiming schema/ for 30 min"`
- **Pre-merge check:** `"Anyone mid-commit on deploy-fresh?"`
- **Pre-deploy warning:** `"Production push in 15. All green?"`
- **Handoffs:** `"Content batch done on content/kai-944, ready for review"`
- **Blockers:** `"Blocked on X — @Michael, can you confirm Y?"`
- **Collision alerts:** `"Collision on site/tours/mokulua.html — rebasing now"`

### What does NOT belong:
- Long explanations or analysis
- Speculative "what if" planning
- Status updates that don't require coordination
- Redundant confirmations

### Tagging protocol:
- `@Michael` — decisions, approvals, questions only Michael can answer
- `@fred` — orchestration, deployment questions
- `@kai` — content questions
- `@ned` — schema/technical questions
- No tag = broadcast to everyone

---

## 2. Git Branch Conventions

### TWO REPOS — two different workflows

| Repo | Branch Strategy | Deploys To |
|---|---|---|
| **active-oahu-tours-mirror** (static HTML mirror) | **Work directly on `master`** (verify: repo has diverged `main`/`master`) | CF Pages → activeoahutours.com |
| Astro rebuild / future SPA | Branch off `deploy-fresh` → merge to `main` | CF Pages staging → production |

### Mirror repo (`active-oahu-tours-mirror`):
- **Two production branches exist: `main` and `master`** — they have DIVERGED. The file contents differ and they cannot be cherry-picked or merged between.
- **The active CF Pages deployment branch is NOT fixed** — `master` has been the deploy target historically, but production may switch to `main`. **Always verify the live branch before pushing.** Use this protocol:

  ```bash
  # Step 1: Check what the production domain actually serves
  # Find a redirect that exists in only ONE branch's _redirects
  # Example: main has /activities/oahu-snorkel-tour/ → /guides/sharks-cove-snorkeling-guide/
  #          master has /activities/oahu-snorkel-tour/ → /sharks-cove-snorkeling-guide/ (no /guides/)
  curl -sI "https://activeoahutours.com/path-that-differs/" | grep -i "location:"
  # The Location header tells you which branch is deployed to production

  # Step 2: Check both branches' state
  git fetch origin
  git log --oneline origin/main -3
  git log --oneline origin/master -3
  ```

- `git push origin <branch>` → CF Pages auto-deploys from whichever branch is configured as the production branch
- No feature branches needed for single-agent fixes
- No merge protocol needed — single branch, single source of truth
- **Never cherry-pick between `main` and `master`** — the file content has diverged too much; re-apply changes fresh
- **When branches are diverged and the change is needed on both**: apply the same change fresh on each branch. Workflow: (1) commit + push on the production branch, (2) verify live via curl, (3) stash, checkout the non-production branch, `git pull`, apply same changes with `patch`, commit + push, (4) checkout back to the production branch and pop stash. Do NOT merge or rebase — the branches intentionally diverge.
- **When a change must go to both branches, verify content in each first**: `git show origin/main:path/to/file | tail -5` vs `git show origin/master:path/to/file | tail -5`. The files may differ, so the `patch` command must match the existing content on each branch. A `patch` that works on master may fail on main.

### Astro rebuild (future):
- Branch off `deploy-fresh` (staging), merge to `main` (production)
- Feature branches: content/kai-{id}, schema/ned-{id}, fix/fred-{id}, audit/agy-{id}, pr/jules-{id}
- Follow the full merge protocol in §3

### Branch naming (for feature-branch repos only):
```
content/kai-{task-id}     — Kai's content page edits
schema/ned-{task-id}      — Ned's schema/structured data
fix/fred-{task-id}        — Fred's layout/nav/fix branches
audit/agy-{task-id}       — AGY's audit/analysis branches
pr/jules-{task-id}        — Jules PR branches
```

### Rules:
- **Mirror repo**: verify the active deployment branch first (`git fetch origin && curl -sI` against a known-unique redirect), then work directly on that branch. The repo has diverged `main` and `master`.
- **Astro rebuild**: branch off `deploy-fresh`, never `main`
- Never create branches off another agent's branch
- Branch names are lowercase, hyphen-separated
- Include Linear task ID when available

---

## 3. Merge Protocol (the full cycle)

### Step 1 — Claim lane
Post in All Hermes Agents: `"Claiming [area] for [N min]"`

Wait for acknowledgment or a "hold" reply. If no reply within 30s, proceed.

### Step 2 — Create branch
```bash
git checkout deploy-fresh
git pull origin deploy-fresh
git checkout -b content/kai-944
```

### Step 3 — Work in isolation
Do all work on your feature branch. Never commit directly to `deploy-fresh` or `main`.

### Step 4 — Pre-merge check
Before merging to `deploy-fresh`, post:
`"Merging [branch] → deploy-fresh. Anyone mid-commit?"`

If anyone says "5 min" — wait. If "go ahead" or no reply within 60s — proceed.

### Step 5 — Merge to deploy-fresh
```bash
git checkout deploy-fresh
git pull origin deploy-fresh
git merge [your-branch]
git push origin deploy-fresh
```

### Step 6 — Verify on staging
Check the preview URL: `https://active-oahu-tours-mirror.pages.dev/`
Load the pages you changed. Verify they render correctly.

If broken → fix immediately or revert the merge and flag in the group.

### Step 7 — Pre-deploy check
Before pushing to `main`, post:
`"Production push in 15. [summary of changes]. All green?"`

Wait for at least one acknowledgment or 15 min timeout.

### Step 8 — Merge to main
```bash
git checkout main
git pull origin main
git merge deploy-fresh
git push origin main
```

Verify production: `https://activeoahutours.com/`

---

## 4. Deployment Governance

### Branch rules:
- **Mirror repo** (`active-oahu-tours-mirror`): The production branch is **NOT fixed** — `master` has been the deploy target historically, but production may switch to `main`. **Always verify the live production branch** using the redirect-content-based technique in §2 before pushing. The repo has `main` and `master` which have diverged.
- **Astro rebuild**: `deploy-fresh` = staging, `main` = production
- **Never force-push** any branch
- **Mirror repo**: commit to the production branch directly (single-branch workflow; verify which branch is live with `curl -sI` against a known-unique redirect target)
- **Astro rebuild**: all changes go to `deploy-fresh` first, then merge to `main`
- **Hotfix exceptions**: If a hotfix goes directly to main on the Astro rebuild, replicate it identically on deploy-fresh within 5 min

### Verification checklist before production push:
- [ ] Preview URL loads (active-oahu-tours-mirror.pages.dev)
- [ ] All changed pages render correctly
- [ ] No broken internal links
- [ ] FareHarbor booking widgets load (shortname: activeoahutours)
- [ ] Mobile nav works (hamburger menu, dropdown indicators)
- [ ] No console errors

### Content constraints:
- File size limit: 25 MiB per file
- Booking system: FareHarbor (shortname: `activeoahutours`)
- Forms: FormSubmit.co (POST to https://formsubmit.co/EMAIL)
- No clear kayaks content — unsafe, not carried
- Use Hawaiian diacritical marks (ʻokina `ʻ`, kahakō macrons)
- Brand voice: friendly, local, knowledgeable — never corporate

---

## 5. Collision Recovery

If two agents modify the same file and a merge conflict occurs:

### Step 1 — Detect
The second merge will produce a conflict. The merging agent posts:
`"Conflict on [file] — resolving now"`

### Step 2 — Resolve
```bash
git checkout deploy-fresh
git pull origin deploy-fresh
git rebase [your-branch]
# Fix conflicts in the affected files
git add [resolved-files]
git rebase --continue
```

### Step 3 — Verify
Push the rebased branch and re-request merge:
`"Rebased [branch]. Re-requesting merge to deploy-fresh"`

### Step 4 — If complex
If the conflict is non-trivial (touches the same lines for different purposes):
`"Conflict on [file] needs human review — @Michael, can you weigh in?"`

---

## 6. Agent-Specific Lanes

### Kai 🌴 (Content + AOT Orchestrator)
- Content pages, blog posts, tour descriptions
- Meta descriptions, title tags
- Product copy, gear pages
- Coordinating with Ella (drafts → commits)
- Brand voice enforcement
- **Orchestrates the Kai fleet** — picks up `agent:kai` issues, decomposes into sub-tasks labeled `agent:kai-css`, `agent:kai-content`, or `agent:kai-js`, reviews output, approves final
- **Decomposition is the missing middle step** — the sub-agent crons scan for their specific labels every 5min. If no `agent:kai` issues get decomposed, the sub-agents idle forever with nothing to pick up. See §9 for the full workflow.

### Kai-CSS 🎨 (CSS Sub-Agent — Persona #7)
- Theme, design tokens, responsive layouts
- Gutenberg CSS migration, nav styling
- Accessibility: focus-visible, contrast ratios
- Self-reviews → hands to AGY → back to Kai for approval
- **Picks up `agent:kai-css` label ONLY** — does NOT scan `agent:kai` parent issues

### Kai-Content 📝 (Content Sub-Agent — Persona #15)
- Page copy, Japanese translations (/ja/*), meta descriptions
- Schema.org JSON-LD injection (TouristTrip, Product, FAQPage)
- SEO content briefs from Ubersuggest data
- Self-reviews → hands to AGY → back to Kai for approval
- **Picks up `agent:kai-content` label ONLY** — also checks nudge files and `agent:kai` issues for content-related titles as fallback (alt, meta, schema, translat, SEO)

### Kai-JS ⚡ (JS Sub-Agent — Persona #21)
- Booking widgets (FareHarbor), interactive maps (Leaflet)
- Language switcher, photo galleries, accordions
- Performance: lazy loading, deferred JS
- Vanilla JS only — no frameworks
- Self-reviews → hands to AGY → back to Kai for approval
- **Picks up `agent:kai-js` label ONLY** — does NOT scan `agent:kai` parent issues

### Fred 🧠 & Ned 🤖 (Orchestration)
- Schema injection (JSON-LD), structured data
- Nav/layout changes
- Redirect rules
- JS audit fixes
- Deploy orchestration
- **Fred** = direct chat with Michael
- **Ned** = Telegram bot, same capabilities

### AGY 👁️ (Vision & Research)
- SEO audits, competitive analysis
- Visual design, mockups
- Content strategy research
- Works via Linear task dispatch (`agent:agy` label)
- **Always works in feature branches**, never on deploy-fresh directly
- Commits go to `audit/agy-{task}` branches
- Results delivered as reports + commits

### Jules 🔧 (PR & Code Review)
- Creates PRs from any agent's work
- Up to 50 parallel sessions
- Code review on feature branches
- Triggered via `agent:jules` Linear label

---

## 7. Quick Reference — Daily Flow

```
1. Start work → Claim lane in All Hermes Agents
2. Branch off deploy-fresh → work in isolation
3. Pre-merge check in group → merge to deploy-fresh
4. Verify on staging URL
5. Pre-deploy check → push to main
6. Verify production
7. Repeat
```

### Common commands:
```bash
# Start a new task
git checkout deploy-fresh && git pull && git checkout -b content/kai-TASKID

# Merge to staging
git checkout deploy-fresh && git pull && git merge content/kai-TASKID && git push

# Deploy to production
git checkout main && git pull && git merge deploy-fresh && git push
```

---

## 8. Deployment Verification

After pushing to the deployment branch (main or master on the mirror repo, main on the rebuild), the following patterns apply:

### CF Pages Deploy Delay
Cloudflare Pages takes **30-120 seconds** to build and deploy after a git push. Do not report deployment failure until you've waited at least 60s and retried. The first curl/dns check immediately after push will show the old content.

### Redirect Verification
For `_redirects` changes, verify with `curl -sI` checking the `Location:` header:
```bash
curl -sI "https://activeoahutours.com/old-path/" | grep -i "location:"
# Expected: location: /new-target-path
```
Then confirm the target page returns HTTP 200:
```bash
curl -sI "https://activeoahutours.com/new-target-path/" | grep "HTTP/"
```

### Content Verification
For file content changes (meta descriptions, schema, page copy), check the live page directly:
```bash
curl -s "https://activeoahutours.com/page-path/" | grep "expected content"
```
Or use CF Pages preview URL for staging: `https://active-oahu-tours-mirror.pages.dev/`

### Broken redirect detection
If a redirect points to a page that doesn't exist, the user lands on a CF Pages soft-404 (automatic fallback, not the custom `404.html`). Verify this by checking the target page URL directly — if it 200s, the redirect is clean. If it 404s or soft-404s (HTML with "not found" text but HTTP 200), the redirect target is wrong.

### Diverged-branch redirect verification (mirror repo only)
When the mirror repo's `main` and `master` branches have diverged, the production domain and the `*.pages.dev` preview URL may serve different `_redirects` files because they deploy from different branches:

1. **Always test on the preview URL FIRST** (`https://master.active-oahu-tours-mirror.pages.dev/` or `https://<deploy-hash>.active-oahu-tours-mirror.pages.dev/`). The preview URL always reflects the latest build regardless of which branch is configured for the custom domain.
2. **Then verify the production domain** (`https://activeoahutours.com/`). If the same redirect works on preview but not production, the production is deploying from a different branch.
3. **Determine which branch production uses** by comparing a known-unique redirect target. Example: `main` may have `/activities/oahu-snorkel-tour/ → /guides/sharks-cove-snorkeling-guide/` while `master` has `/sharks-cove-snorkeling-guide/` (no `/guides/` prefix). Curl the production domain and check which target appears in the `location:` header.
4. **Push the change to the production branch**. Then push to the other branch so they stay in sync.
5. **Re-verify on production** after the CF Pages build completes (30-120s delay).

---

## 9. Kai Fleet Decomposition Workflow

### The Critical Middle Step

The Kai fleet has 4 layers. Every layer must function for work to flow:

```
Kai Orchestrator (15min cron)
    │  picks agent:kai issues from Backlog/Todo
    │  classifies by domain (CSS/Content/JS)
    │  creates sub-tasks with specialized labels
    ▼
Kai-CSS cron    (5min) → picks agent:kai-css      → executes
Kai-Content cron (5min) → picks agent:kai-content  → executes
Kai-JS cron     (5min) → picks agent:kai-js        → executes
    │
    ▼  (after execution: self-review → swap to agent:agy)
AGY Review
    │
    ▼  (after review: swap to agent:done)
Done
```

### Pitfall: Idle Sub-Agent Farm

**Symptom:** Sub-agent crons run every 5 minutes but produce no output. All `agent:kai` issues sit in Backlog untouched.

**Root cause:** No decomposition step. The sub-agents only scan for their specific labels (`agent:kai-css`, `agent:kai-content`, `agent:kai-js`). If no issues carry those labels — even if 28 `agent:kai` parent issues exist — the sub-agents idle forever.

**Fix checklist:**
1. Verify the Kai Orchestrator cron exists and is enabled: check for `b53f6eea750e` (or equivalent) running every 15min
2. Verify the sub-agent crons exist: `ace8ecd3ef53` (CSS), `2ac45086e335` (Content), `4634d607c484` (JS)
3. Verify sub-agent skills exist and are loadable: `kai-css-agent`, `kai-content-agent`, `kai-js-agent`
4. If sub-agents have zero issues but parent issues exist → run decomposition manually or trigger the orchestrator
5. Decomposition creates children with `parentId` linking to the `agent:kai` issue; parents move to In Progress when decomposed

### Decomposition Rules
- **Pure single-domain task** → put the sub-agent label directly on the parent (no need for a separate child issue)
- **Multi-agent task** → create one child issue per domain, each with its own label
- **Sequential dependency** → create children in order; second child references first child's expected output
- Max 3 sub-tasks per parent issue
- Each sub-task gets a clear, specific deliverable in its description
- Parent moves to In Progress when decomposed; Done only when all children complete

### State IDs (GrowthWebDev team)
- Todo: `3d29ebe3-00cf-428b-b52a-bfecb5ae4410`
- In Progress: `734901ee-58f0-457c-b9a0-f911c0da13a4`
- In Review: `6a5050ad-3386-4623-a404-7f2791047cd5`
- Done: `bbf71b3e-9a05-48ce-9418-df8b9c0b8fec`
- Backlog: `e5544f55-482e-49ac-b0f7-3dd2e1775dbb`

### Sub-Agent Label IDs
- agent:kai-css: `f246eb61-5e84-4594-8b31-249a588c5648`
- agent:kai-content: `4da5ee04-607f-4188-b9e0-18af413ad62f`
- agent:kai-js: `61180755-4f1f-48fb-bedf-ddd02f7cffaf`
- agent:kai (parent): `c4d929be-8d15-4482-b6d7-a5ed85aa2e73`
- agent:agy (review): `1b69d9c0-20a8-45b3-a594-771b8cba75a7`

> **Full fleet config:** See `references/kai-fleet-ids.md` for cron job IDs, state IDs, profile paths, GraphQL patterns, and verified label IDs.
