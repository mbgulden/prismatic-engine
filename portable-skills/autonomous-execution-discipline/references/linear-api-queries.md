# Linear API GraphQL Query Patterns

Quick-reference for querying Linear issues via the GraphQL API. Keep this handy when writing Ned cron job query scripts.

## Endpoint

```
POST https://api.linear.app/graphql
Authorization: $LINEAR_API_KEY
Content-Type: application/json
```

## Common Queries

### Find issues by label + state (primary Ned pattern)

```graphql
query {
  issues(
    filter: {
      labels: { name: { eq: "agent:fred" } }
      state: { name: { eq: "Todo" } }
    }
    orderBy: createdAt   # NOTE: use orderBy, NOT sort
    first: 1
  ) {
    nodes { id identifier title description }
  }
}
```

### Get all workflow states (to find "Done" state ID)

```graphql
query {
  workflowStates {
    nodes { id name }
  }
}
```

### Move issue to Done

```graphql
mutation {
  issueUpdate(id: "ISSUE_ID", input: { stateId: "DONE_STATE_ID" }) {
    success
  }
}
```

### Post comment on issue

```graphql
mutation {
  commentCreate(input: { issueId: "ISSUE_ID", body: "Ned executing this." }) {
    success
  }
}
```

### Find issue by identifier (e.g. "GRO-847")

```graphql
# Use searchIssues, NOT issueSearch (deprecated)
# identifier is NOT valid in IssueFilter — use searchIssues with term:
query {
  searchIssues(term: "GRO-847", first: 1) {
    nodes {
      id identifier title description
      state { id name }
      labels { nodes { name } }
    }
  }
}
```

### Get label by name (find label ID)

```graphql
query {
  issueLabels(filter: { name: { eq: "agent:fred" } }) {
    nodes { id name }
  }
}
```

### Filter by label ID (more reliable than name)

```graphql
query {
  issues(filter: {
    labels: { id: { eq: "LABEL_UUID" } }
    state: { name: { eq: "Todo" } }
  }) {
    nodes { id identifier title state { name } }
  }
}
```

## Pitfalls

| Don't | Do |
|---|---|
| `sort: createdAt` | `orderBy: createdAt` |
| `issueSearch` | `searchIssues` (deprecated) |
| `filter: { identifier: { eq: "..." } }` | `searchIssues(term: "...")` — identifier not valid in IssueFilter |
| Filter by label name alone | Prefer label ID for exact matching |
| Assume issues exist in Todo | Check — respond `[SILENT]` when none found |
