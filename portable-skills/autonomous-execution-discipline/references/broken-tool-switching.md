# Broken Tool Switching Rule (Jun 2026)

## The Rule

When a tool is broken (SIGTERM, hang, 0 bytes, auth failure), spend MAX 2 attempts to fix it, then SWITCH to alternatives. Create a Linear issue for the tool problem and MOVE ON to the actual deliverable.

## Michael's Directive

"You could have built and launched a whole website in that time. Be more proactive about fixing problems like these."

## This Session (Darius Star, Jun 9 2026)

45 minutes lost to AGY OAuth/debugging while creative deliverables (banter, plot gaps, multiplayer design, voice pipeline) sat untouched. The fix was subagents — they completed 1,631 lines of creative content in 9 minutes.

## Pattern

1. Tool fails → diagnose ONCE (check auth, test trivial command)
2. Retry ONCE with known fix (refresh token, kill stale processes)
3. Still fails → SWITCH. Do not try variant modes, PTY wrappers, chunking strategies.
4. Create Linear issue: "AGY --print mode broken — investigate"
5. Use alternatives: subagents for research, Fred for implementation
6. Report: "AGY is down. Switched to subagents. Here's what got done: [results]"

## NEVER

- Debug for 3+ turns
- Try 3+ different invocation modes
- Ask the user for auth/credentials across multiple turns
- Let a broken tool block all other work
