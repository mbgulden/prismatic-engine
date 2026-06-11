# Backward-Compat Shim Pattern — Nudge File Migration

## The Bug (June 2026)

After wiring SignalProvider into the dispatcher, the Autobot Nudge Watch cron correctly
detected nudge files every minute — but the agents NEVER picked them up. The nudge files
sat unprocessed, triggering alert after alert.

**Root cause:** The SignalProvider wrote to the NEW path (`/tmp/prismatic/nudge-fred`),
but the agents' poll loops still checked the OLD path (`/tmp/nudge-fred`). Two different
file paths, two different formats. The notification pipeline worked, the execution pipeline
didn't.

## The Shim

When migrating from one path/format to another, write to BOTH during the transition:

```python
# signal_provider.py — FileSignalProvider.send()
def send(self, target: str, payload: SignalPayload) -> bool:
    # NEW: Proper SignalPayload JSON at canonical path
    nudge_path = self._dir / f"nudge-{target}"   # /tmp/prismatic/nudge-fred
    # ... atomic write with tempfile + rename ...

    # OLD: Backward-compat for agents still on the old path
    legacy_path = Path(f"/tmp/nudge-{target}")   # /tmp/nudge-fred
    with open(legacy_path, "w") as lf:
        lf.write(f"{payload.issue_id}\n{payload.title}\n")

    return True
```

## The Rule

During any file-path migration (nudge files, config files, socket paths, etc.):

1. **Write to BOTH paths** for one full release cycle
2. **Add a `# BACKWARD-COMPAT SHIM` comment** so it's findable with grep
3. **Note the removal condition** — "Remove once all agents read SignalPayload JSON from /tmp/prismatic/"
4. **Verify BOTH paths work** — write a test file and check both locations
5. **Schedule the cleanup** — add a Linear issue to remove the shim after migration

## Why Not Just Update All Agents First?

You could. But in a multi-agent system (Fred, Kai, Autobot, Jules, AGY), each agent
has its own poll loop, its own codebase, its own deployment cycle. Updating all of them
atomically is impossible. The shim is the practical bridge that keeps the system running
during the rollout.

## Detection Pattern

When something "works in the notification layer but not in the execution layer," suspect
a path mismatch. Check:

1. What path does the WRITER use? (`grep -r "nudge-" scripts/`)
2. What path does the READER use? (`grep -r "nudge-" bots/ profiles/ cron definitions`)
3. Do they match? If not → shim.

## Related

- `references/agent-dispatcher-nudge-pattern.md` — full nudge file architecture
- `references/signal-provider-drop-in-pattern.md` — drop-in module pattern
- `scripts/signal_provider.py` — the live code (includes the shim)

## The Two-Cron Nudge Architecture (after migration)

A single cron that both detects AND executes is fragile. The proven architecture:

| Cron | Type | Every | What |
|------|------|-------|------|
| Nudge Detection (`400a5a41cc45`) | Script-only | 1 min | Reads SignalPayload JSON, writes `/tmp/trigger-fred-work`, tracks seen signal_ids |
| Nudge Executor (`c2cce4fec4ed`) | LLM-driven | 5 min | Reads trigger file, loads skills, does the work |

**Why detection ≠ execution:**
- Script-only detection is free (no tokens) — runs every minute
- LLM execution costs tokens — gated behind trigger file, runs only when work exists
- `nudge_executor.py` dedups by `signal_id` in `/tmp/prismatic/.seen_signals.json`
- Fred signals → auto-triggered. Kai/Autobot signals → notified once, never repeats

**Why the old system failed (June 2026):**
The original migration replaced the LLM-every-minute cron with a notification-only script cron. Detection worked perfectly (Autobot found nudge files, notified Michael). But execution was gone — nobody processed the nudge files. Michael got 8 identical pings for the same stuck signal. Fix: added `c2cce4fec4ed` as the execution leg.

## Local-Model-First for Small Cron Tasks

For LLM-driven crons that do small, repetitive work (nudge executor, morning prep, journal close):

1. **Check local GPU availability first** — `ollama-qwen:32b` or `ollama-hermes:70b` on k3s node `100.78.237.7`
2. **If local is down** → fall back to cheapest cloud model (`deepseek-v4-flash`, ~$0.001/run)
3. **Never use the main model** (`deepseek-v4-pro`) for cron tasks — it's for interactive sessions
4. **Add GPU health to Autobot's Swarm Monitor** — detect drops before they force cloud spend

This preference is user-driven: "Shouldn't that be using a local Model? Let's start integrating my local models more for these small tasks since they are unlimited use."
