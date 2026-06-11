# Don't Give Up on Tools — Michael's Directive (Jun 2026)

## The Rule

When you hit a wall with a tool (Jules, AGY, anything), the wall is usually a wrong approach, not a capability limit. Michael called this out explicitly:

> "Don't assume Jules isn't good at research. This whole exercise is meant to help you work better with Jules and you are hitting walls. Good. Now you know one way not to work with Jules. Jules is amazing and maybe Jules is smarter than you because you gave up trying to work with Jules too early."

> "You still need to do more investing. Those conclusions are not complete. You still gave up too soon."

## The Pattern

When you hit a wall, cycle through these BEFORE concluding a tool can't do something:

1. **Different invocation mode** — CLI vs TUI vs web UI. Jules has all three. AGY has `--print` vs `--prompt-interactive`.
2. **Different framing** — chatbot question vs goal-with-parameters. Jules responds completely differently to "what can you do?" vs "GOAL: Research and document the full capability envelope of Jules CLI. CONTEXT: ... PARAMETERS: ... OUTPUT: ..."
3. **Different environment** — PTY vs direct, tmux vs raw PTY, foreground vs background
4. **Different context delivery** — inline vs file path, GitHub-pushed vs local-only

## The Jules-Specific Lesson

Jules sessions going "Awaiting User F" is NOT failure. It means Jules proposed a plan and is waiting for approval. The fix is NOT to give up — it's to:
- Respond via TUI (tmux-based navigation if PTY fails)
- Or prevent the approval gate by including "DO NOT wait for approval" in the prompt
- Or use the web UI at jules.google.com/session/<id>

Jules creating empty onboarding files instead of answering interview questions is NOT Jules being bad at research — it's Jules being given unreadable local file paths and defaulting to what it CAN do in the repo. The fix: inline the questions in the prompt.

## The AGY-Specific Lesson

AGY `--print` returning SIGTERM 143 + tcsetattr is NOT AGY being broken — it's `--print` mode having no real terminal. The fix: use `--prompt-interactive` with PTY (TUI mode). Different mode, same tool, completely different result.

## The Meta-Lesson

After hitting a wall, the NEXT attempt should be a creative angle, not a retry of the same approach. Three walls before drawing conclusions. Michael: "Show your gumption and try creative angles."
