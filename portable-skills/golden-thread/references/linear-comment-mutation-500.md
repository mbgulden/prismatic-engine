# Linear `commentCreate` Mutation — HTTP 500 with Long Bodies

## Symptom
`commentCreate` with a GraphQL variable for the body returns HTTP 500 when the body is long and formatted (markdown tables, newlines, >~300 chars). Shorter inline bodies work fine.

## Worked Example (June 9, 2026 — GRO-1015)

### ❌ Fails — Variables with long markdown body
```python
body = """✅ **Ned: Done.** Generated all 10 relief/resolution tracks...

### Tracks Generated (assets/audio/)
| ID | Scene | File |
|---|---|---|
| relief_post_boss | Post-Boss Victory Calm | relief_post_boss.mp3 |
... (10 rows)
"""

query = 'mutation($body: String!) { commentCreate(input: { issueId: "' + issue_id + '", body: $body }) { success } }'
# → HTTP 500
```

### ❌ Fails — Inline f-string with markdown table
```python
# Python f-string with embedded quotes and newlines → shell escaping nightmare → HTTP 500
```

### ✅ Works — Short inline body
```graphql
mutation { commentCreate(input: { issueId: "6faf72fe-...", body: "Ned executing this." }) { success } }
```
→ `{"data":{"commentCreate":{"success":true}}}`

### ✅ Works — Short plain summary via variables
```python
body = "Ned complete. 10 relief/resolution tracks generated via Lyria 2: post_boss, checkpoint, bonding..."
query = 'mutation($body: String!) { commentCreate(input: { issueId: "' + issue_id + '", body: $body }) { success } }'
# → success
```

## Rule
- **<200 chars, plain text, no markdown formatting**: variables or inline both work
- **>300 chars, markdown, tables, special chars**: BOTH variables and inline fail with HTTP 500
- **Workaround**: post a short summary comment. The detailed report goes in the agent's final response, not the Linear comment.

## Contrast with `issueCreate`
`issueCreate` with `description` via variables handles long markdown bodies fine — this is specific to `commentCreate`.
