---
name: agy-secure-coding
description: "AGY secure coding guardrails: secrets, auth, payments, webhooks, file serving, OAuth, permissions, logging redaction, and OWASP-oriented review."
tags: []
related_skills:
  - agy-as-coder
  - agy-as-reviewer
---

# agy-secure-coding

## Purpose

Prevent AGY from shipping credential, auth, payment, or file-serving mistakes while coding or reviewing.

## When to Use

Use for API keys, OAuth, webhooks, Stripe/payment, auth/session code, file serving, user data, Linear/GitHub/Cloudflare integrations, and any logging of requests/responses.

## Operating Loop

1. Identify security boundary.
2. Verify secrets come from env/secret store.
3. Add signature/permission/input validation.
4. Redact logs.
5. Add negative tests.
6. State security note in summary.

## Required Artifacts

- Security checklist result
- Negative tests
- Redaction proof
- Webhook/signature validation proof when relevant
- Security note

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- Never invent IDs/keys.
- Never log tokens/cookies/auth headers.
- Never serve arbitrary files.
- Never mix test/live payment modes silently.

## Dispatch Skeleton

```text
Lane: agy-secure-coding
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
