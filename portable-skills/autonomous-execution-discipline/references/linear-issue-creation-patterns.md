# Linear GraphQL API — Issue Creation Patterns

Used in June 2026 to create GRO-802 through GRO-809 for the AGY/Jules interview project.

## Prerequisites

```bash
export LINEAR_API_KEY="lin_api_..."  # Must be set in environment
```

## Query Structure

All queries use `POST https://api.linear.app/graphql` with `Authorization: $LINEAR_API_KEY` header.

## Get Team ID

```graphql
{ teams { nodes { id name key } } }
```

## Get Workflow States

```graphql
query { workflowStates(filter: { team: { id: { eq: "TEAM_ID" } } }) { nodes { id name type } } }
```

Common types: `backlog`, `unstarted`, `started`, `completed`, `canceled`

## Get Labels

```graphql
{ issueLabels { nodes { id name } } }
```

## Create Issue

```graphql
mutation {
  issueCreate(input: {
    title: "Issue Title",
    description: "Description text",
    teamId: "TEAM_ID",
    stateId: "STATE_ID",
    labelIds: ["LABEL_ID_1", "LABEL_ID_2"]
  }) {
    issue { id title identifier url }
  }
}
```

## Create Child Issue (sub-task)

Add `parentId: "PARENT_ISSUE_ID"` to the input.

## Important Labels

| Label | ID |
|-------|-----|
| agent:fred | a43efb77-534a-4e39-8ff3-76f0e42019d1 |
| agent:agy | 1b69d9c0-20a8-45b3-a594-771b8cba75a7 |
| agent:jules | 5bc301fb-e4dc-404c-97fb-290c49ed2528 |
| type:research | b721e7a8-68e0-46fa-aeb1-7dc007cfe80a |
| type:docs | d24a4a88-00d8-40e7-9e58-6fdfc8a1a6b6 |
| pipeline:research-strategy | f7f6e8f7-abe9-4b9b-a73a-b1d391c551f6 |

## Python Helper

```python
import json, os, urllib.request

key = os.environ['LINEAR_API_KEY']
headers = {'Authorization': key, 'Content-Type': 'application/json'}

def graphql(query):
    data = json.dumps({'query': query}).encode()
    req = urllib.request.Request('https://api.linear.app/graphql', data, headers)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())

# Get all teams
teams = graphql("{ teams { nodes { id name key } } }")

# Create an issue
mutation = """mutation {
  issueCreate(input: {
    title: "My Task",
    description: "Task description",
    teamId: "TEAM_ID",
    stateId: "STATE_ID",
    labelIds: ["LABEL_ID"]
  }) {
    issue { identifier title url }
  }
}"""
result = graphql(mutation)
print(result['data']['issueCreate']['issue']['identifier'])
```

## Pitfalls

- **URL-encoding breaks nested JSON**: Always use `Content-Type: application/json`, not form-urlencoded.
- **Description escaping**: Replace newlines with `\n` and escape double quotes with `\"` in mutation strings.
- **Issue may not be immediately queryable by identifier**: After creation, the issue may take a moment to appear in queries by `identifier`. Use the returned `id` for immediate follow-up operations.
- **Env vars not available in execute_code**: Use `terminal()` with shell interpolation for API calls, not `execute_code`.
