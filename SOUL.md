# The Soul of Prismatic Engine

*This is not a spec. This is the why. Every feature, every line of code, every decision must serve this.*

---

## The One-Line Truth

**Prismatic Engine is the missing link between your idea and the manifestation of that idea in reality.**

Not a plan. Not a prototype. The *thing itself*, fully realized, in the world, working.

---

## The Dream That Started This

You have an idea. A business. A game. A program that could change lives. You've felt it — that pulse of clarity where you *see* what's possible.

Then you look at what's between you and the finish line.

The paperwork. The compliance forms. The website. The marketing funnel. The SEO. The business cards. The payment integration. The coaching curriculum. The ad campaigns. The KPI dashboards. The real-time adjustments when something isn't working. The thousand small, soul-draining tasks that stand between a vision and a living, breathing reality.

Prismatic Engine is the bridge over all of that.

**An idea in the morning. Business cards at your door that afternoon. Registration filed. Compliance submitted. A living business, not a todo list.**

---

## How It Works (The Simple Version)

You provide the input — your idea, your context, your research, your voice, your standards. You provide the *why*.

Prismatic Engine provides the *who* — a whole team of AI agents, each with their own expertise, all communicating, all working together, all pulling toward one goal.

You walk away from the prompt.

You come back to a clean merge. The thing is done. Not a plan for the thing. The thing.

No stepping on toes. No context drift. No "I thought Fred was handling that." No "we need another meeting to align on scope." Just a team of agents that *align themselves* to your goal and execute with precision.

---

## The Prismatic Spectrum

A prism takes a single beam of white light and splits it into the full spectrum — every wavelength, every color, every possibility hidden inside that single beam.

Prismatic Engine does the same with your idea.

| One Input | The Full Spectrum of Outputs |
|-----------|------------------------------|
| "I want to launch a coaching business" | Website, payment system, curriculum, marketing funnel, compliance docs, email sequences, social presence, analytics dashboard |
| "Build me a game" | Code, art, sound, levels, store page, monetization, analytics, community |
| "I need a SaaS product" | App, pricing page, docs, onboarding flow, billing, support system, growth metrics |
| "Write me a book" | Manuscript, cover design, ISBN registration, distribution channels, launch campaign, audiobook |
| "Help me grow" | Strategy, content calendar, ad campaigns, KPI dashboards, weekly accountability, dynamic adjustments |

The *same* engine. The *same* input-to-reality pipeline. Different agents, different recipes, different outputs — but always the complete spectrum.

---

## What This Is NOT

- **Not an orchestration hub.** That's the mechanism, not the purpose. Prismatic Engine orchestrates agents the way a heart pumps blood — it's necessary, but nobody says "I want a blood-pumping machine." They say "I want to be alive."
- **Not a task manager.** Tasks are the atoms. The molecule is the realized outcome. Prismatic Engine doesn't care about your task board. It cares about what's real and working at the end of the day.
- **Not another "AI wrapper."** Wrappers let you chat with a model. Prismatic Engine lets you launch a civilization of agents that build, ship, market, and maintain a real-world thing.
- **Not a "build it for you" tool.** The engine doesn't replace you. It *multiplies* you. Your vision, your taste, your standards — the agents execute within your constraints. You are the prism. The agents are the light.

---

## The Non-Negotiables

These are the inviolable rules. Every feature, every design, every decision must serve these or be cut.

### 1. Input → Reality. No middle ground.
If a feature produces a plan, a document, or a recommendation without also producing the *thing itself*, it's incomplete. Plans are step 1. The engine does steps 1 through N and hands you the result.

### 2. The whole spectrum, every time.
No single-output agents. A business idea gets the full treatment: website, compliance, marketing, operations, the works. A game gets code, art, store page, monetization. The engine doesn't do pieces. It does wholes.

### 3. Walk away, come back to done.
The engine's job is to be trustworthy enough that you can set it in motion and leave. No micro-managing. No hand-holding. No "can you check this intermediate step?" If it needs you in the middle, it's not done.

### 4. Agents work together, not in silos.
AGY researches, Fred architects, Kai writes, Jules reviews, Codex ships — but they talk. They hand off context. They catch each other's blind spots. A single agent's output is a draft. The team's output is the truth.

### 5. The output IS the deliverable.
Not a link to a staging environment. Not a Figma file. Not a Google Doc. The business cards arriving at your doorstep. The app in the store. The marketing campaign generating leads. Real. Tangible. In the world.

### 6. You set the bar. The engine meets it.
Your standards. Your taste. Your brand. Your voice. The agents don't have opinions about what looks good — they have your guidelines. If it doesn't feel like *you*, it's not done.

### 7. Relentless iteration until it works.
The first version ships fast. But the engine doesn't stop. KPI data comes back → campaigns adjust. User feedback comes in → product improves. The engine keeps running until the goal is not just met, but *exceeded*.

---

## What We Already Have

You said you're already using Prismatic Engine and it's already better than what you were doing two weeks ago. That's the north star. Every improvement makes that delta bigger.

The current implementation:
- **Fred** — Orchestration & Infrastructure (`src/`, `infra/`, `deploy/`)
- **Kai** — Oahu Content & Voice (`content/`, `active-oahu/`)
- **AGY** — Swarm Research & Design (`assets/`, `designs/`, `research/`)
- **Jules** — Code Audits & PR Reviews (PR-only read-only)
- **Ned** — Code & Task Execution (`scripts/`, `prismatic/`)

### Swarm Governance Protocols

To prevent merge chaos and namespace collisions, the swarm adheres to the following Phase 1 governance conventions:

1. **Lane Ownership (Write Boundaries)**: Agents may only modify files in their owned directories. All other paths are strictly read-only for them.
2. **Branch Conventions**: Agents must work in branches matching their prefix (e.g. `content/add-page` or `execution/run-job`). Direct pushes to `deploy-fresh` (staging) or `main` (production) are rejected. Fred acts as the sole Staging Governor authorized to merge approved PRs.
3. **Commit Prefix Format**: All commit messages must be prefixed with `[AGENT]`, followed by a clear summary and issue reference. Example: `[Kai] Add mokulua islands tour page (#GRO-1215)`.
4. **Lock Protocol Reference**: Agents must claim a file using the centralized lock manager `/home/ubuntu/.antigravity/swarm_locks.json` prior to editing.
   * **Heartbeat**: Pinged every 60 seconds.
   * **Heartbeat TTL**: 5 minutes (300,000 ms). Locks that do not receive a heartbeat are auto-released as stale.

---

## What We're Building Toward

Phase by phase, the engine gets closer to the dream:

**Phase 1 — Convention (this week)**
Every agent knows their lane. No collisions. No confusion. The team works like a team.

**Phase 2 — Locking (if needed)**
When collisions happen, the engine prevents them before they start. File-level coordination.

**Phase 3 — Hooks (as needed)**
Validation before anything reaches the repo. Quality gates that enforce the soul.

**Phase 4 — Visibility**
See what every agent is doing, in real time. Trust through transparency.

**Phase 5 — The Loop**
The full 7-step cycle: decompose, dispatch, execute, review, feedback, refine, integrate. Autonomous mode where you set it and forget it.

**Phase ∞ — The Dream**
"I have a business idea at 8 AM. By 5 PM, the LLC is filed, the website is live, the first ad campaign is running, and the coaching curriculum is written." That's the bar. We won't stop until the engine clears it.

---

## The Final Word

Prismatic Engine is not a project. It's not a product. It's not a startup.

It's the *bridge* — between what you imagine and what's real. Between the person with an idea and the world that needs it. Between the spark and the flame.

Every agent, every plugin, every line of code exists for one reason: to shorten that bridge. To make it wider. To make it so anyone with a vision can walk across it and find their reality waiting on the other side.

That's the soul.

Now let's build it.
