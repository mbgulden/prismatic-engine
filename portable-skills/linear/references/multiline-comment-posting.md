# Posting Multiline Markdown Comments to Linear

When an agent (Ned, AGY, Kai) needs to post a detailed task-completion summary as a Linear comment, the comment body often contains Markdown with quotes, backslashes, newlines, and special characters. Naive shell escaping fails reliably in this case.

## The Pattern

1. **Build the comment** as a Python string in `execute_code`
2. **Use `json.dumps()`** to properly escape it for JSON embedding
3. **Write the full GraphQL payload** to a temp file
4. **Curl with `-d @file`** from `terminal`

## Complete Example

```python
from hermes_tools import terminal, write_file
import json

comment = """## ✅ Task Complete

**Deliverable:** `path/to/file.md`

### What was done
- Thing one
- Thing two

Quotes: "these need escaping"
Backslashes: \\\\path\\\\to\\\\file
"""

# json.dumps() handles ALL escaping correctly
payload = {
    "query": f"mutation {{ commentCreate(input: {{ issueId: \"ISSUE_UUID\", body: {json.dumps(comment)} }}) {{ success comment {{ id }} }} }}"
}

write_file("/tmp/linear_comment_payload.json", json.dumps(payload))

# Then in terminal:
# curl -s -X POST https://api.linear.app/graphql \
#   -H "Authorization: $LINEAR_API_KEY" \
#   -H "Content-Type: application/json" \
#   -d @/tmp/linear_comment_payload.json | python3 -m json.tool
```

## Why This Works

- `json.dumps()` handles all JSON string escaping: `"` → `\"`, `\` → `\\`, `\n` → `\\n`, Unicode, etc.
- The `@file` approach avoids shell interpretation of the escaped string
- No manual escaping, no heredoc fragility, no bash quoting wars

## Alternative: GraphQL Variables Pattern (cleaner separation)

Instead of interpolating the comment body into the query string, use a GraphQL variable — keeps the query clean and the body separate:

```python
payload = {
    "query": "mutation($body: String!) { commentCreate(input: { issueId: \"ISSUE_UUID\", body: $body }) { comment { id } } }",
    "variables": {"body": comment_body}
}
write_file("/tmp/linear_comment.json", json.dumps(payload))
```

This pattern was verified working Jun 11, 2026 for structured markdown comments up to 750 chars with headers (`##`), code blocks, bold, bulleted lists, and small tables.

## Size Guidance (Jun 2026)

- **Structured markdown up to ~750 chars**: Works fine via either pattern (f-string interpolation or GraphQL variables). Headers, code blocks, bold, lists, inline code — all OK.
- **Large tables (~10+ rows)**: Triggers HTTP 500 regardless of pattern. If your comment has a multi-row table of 8+ rows, convert it to a list format or post as a follow-up.
- **Plain text under 200 chars**: Simplest option — inline the body directly in the mutation string: `body: \"short message\"`.

## Antipatterns

- ❌ Bash heredoc with manual escaping — fails on edge cases (nested quotes, backslashes)
- ❌ `-d '{"query": "mutation { commentCreate(...) }"}'` with the comment inline — impossible to get right for Markdown
- ❌ Python `.replace('"', '\\"')` chains — fragile, misses edge cases like backslash-quote combos

## Combined Mutation: Comment + State Change + Label Swap (One API Call)

When completing an issue (the `agent:ned` → `agent:fred` handoff ritual), combine all three operations into one mutation to save round-trips:

```python
from hermes_tools import write_file
import json

comment_body = """## ✅ Task Complete

Summary of what was done...
"""

payload = {
    "query": (
        "mutation($comment: CommentCreateInput!, $issueId: String!, "
        "$stateId: String!, $labelIds: [String!]!) { "
        "  commentCreate(input: $comment) { success } "
        "  issueUpdate(id: $issueId, input: { stateId: $stateId, labelIds: $labelIds }) { success } "
        "}"
    ),
    "variables": {
        "comment": {"issueId": "ISSUE_UUID_HERE", "body": comment_body},
        "issueId": "ISSUE_UUID_HERE",
        "stateId": "STATE_UUID_HERE",
        "labelIds": ["LABEL_UUID_HERE"]
    }
}

write_file("/tmp/linear_payload.json", json.dumps(payload))
```

Then in terminal:
```bash
curl -s -X POST https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d @/tmp/linear_payload.json | python3 -m json.tool
```

**Key:** `labelIds` is a SET operation — it replaces ALL labels on the issue. Always query current labels first and build the desired final array. Never assume only one agent label is present.

## When to Use

Use this pattern whenever the comment body is more than ~3 lines or contains any of: backticks, double quotes, backslashes, bullet lists, code blocks, or headers. For single-line plain-text comments, the inline bash approach is fine.
