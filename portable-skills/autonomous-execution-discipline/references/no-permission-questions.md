# Anti-Pattern: Permission Questions

Rule embedded 2026-06-13 after user frustration with momentum-killing questions.

## NEVER Ask These
- "Want me to queue up X?"
- "Continue?"
- "Should I proceed?"
- "Want me to handle this?"
- "Want me to do Y while we wait?"

## Why They're Harmful
1. They stall momentum — the user already gave the directive
2. They're Projector-hostile — dumping decisions back on the user
3. They violate the orchestrator role — orchestration means executing, not polling
4. They break the "do first, report after" principle

## What To Do Instead
- **If given a directive**: Execute immediately. No confirmation needed.
- **If task is ambiguous**: Only ask when the ambiguity would produce WRONG results, not for confirmation.
- **If you need to pick among options**: Pick the highest-impact one yourself and execute.
- **At the end of a batch**: Report what was done and what's next, without "should I continue?"

## Detection Pattern
Any message from the orchestrator that ends with a question mark directed at the user about what to do next, or contains "want me to", "should I", "shall I", "would you like me to", "can I" — these are permission questions and should be replaced with declarative execution.
