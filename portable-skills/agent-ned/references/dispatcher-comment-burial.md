# Dispatcher Comment Burial — Finding Substantive Comments Under Route Noise

## Problem

The Linear dispatcher fires every 5 minutes on issues that match agent labels. Each fire posts a comment:
```
📡 Dispatcher: task `GRO-1062` routed to Fred.
```

Over the course of an hour (or across multiple cron ticks), these accumulate and bury the substantive Ned triage comments beneath 10-20 dispatcher route markers. If you only fetch the last 5 comments (default `first: 5`), you'll see nothing but dispatcher noise and incorrectly conclude the issue hasn't been triaged.

## Detection Pattern

```python
def fetch_comments(issue_id, limit=20):
    """Fetch enough comments to get past dispatcher noise."""
    result = gql(f'''
query {{
  issue(id: "{issue_id}") {{
    comments(first: {limit}, orderBy: createdAt) {{
      nodes {{
        body
        createdAt
      }}
    }}
  }}
}}
''')
    return result['data']['issue']['comments']['nodes']

def find_substantive_comment(comments):
    """Return the most recent non-dispatcher comment, or None."""
    for c in reversed(comments):  # newest first
        body = c['body'] or ''
        if body.startswith('📡 Dispatcher:'):
            continue
        return c
    return None
```

## Triage Signal Keywords

When scanning the substantive comment body, look for:
- `⚠️ Refactoring Triage` — Ned flagged for interactive
- `✅ Ned:` — Ned completed or partially completed work
- `FLAGGED FOR INTERACTIVE` — explicit flag
- `Status: Keep agent:fred` — Ned determined the label is correct as-is

## Real Example (Jun 12, 2026)

GRO-1062 and GRO-1064: last 5 comments were ALL dispatcher routes. The substantive triage comments (from `2026-06-12T14:00:43Z`) were at position ~15 in the 20-comment list. Fetching 20 and filtering yielded:
- GRO-1062: `⚠️ Refactoring Triage — FLAGGED FOR INTERACTIVE` (dialogue extraction done, UI components need browser)
- GRO-1064: `⚠️ Refactoring Triage — FLAGGED FOR INTERACTIVE` (ES module conversion prep done, needs browser)

## Escalation (Jun 13, 2026)

By the next day, dispatcher noise had completely buried the triage comments beyond retrieval. Fetching 40 oldest comments (sort order `createdAt`) on each issue returned ONLY dispatcher route markers — zero triage content found. The dispatcher fires every 5 minutes, producing ~288 comments/day on each active issue. At that rate, triage comments from 24+ hours ago are at position 50+ and require multiple paginated fetches to reach.

**Implication:** Comment scanning is a DEGRADING signal. After 24+ hours of dispatcher noise on an active issue, it becomes impractical to find triage content through Linear API comment fetches. The HARDCODED_EXCLUSION set in the sweep-6 reference is the reliable detection mechanism — issues known to be triaged should be added to the exclusion set in code, not re-verified via comments each tick.

**When to add to HARDCODED_EXCLUSION:** Any `agent:fred` In Progress issue that Ned has personally triaged (posted section maps, dependency analyses, FLAGGED FOR INTERACTIVE) should be added to the HARDCODED_EXCLUSION set during the same session. This prevents the next cron tick from needing to rediscover the triage through an ever-deepening pile of dispatcher comments.

## Integration with Maintenance Sweep

The PHASE 0 fallback check in the cron sweep script should always:
1. Fetch 20+ comments per candidate issue
2. Filter out `📡 Dispatcher:` comments
3. Check the remaining most-recent comment for triage signals
4. Skip if already triaged (same day or yesterday)

This check runs BEFORE fetching issue descriptions, reading repos, or doing any work — it's the cheapest gate.
