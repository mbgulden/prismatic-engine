# Linear GraphQL Filter Cookbook

Practical filter patterns used in the wild. The [official filtering
reference](https://developers.linear.app/docs/graphql/working-with-the-graphql-api/filtering)
is comprehensive; this is the short cheat-sheet.

## Filter by label name

```graphql
issues(filter: { labels: { name: { eq: "agent:fred" } } }, first: 20) {
  nodes { identifier title state { name } }
}
```

`name` matches the display name including colons (`agent:fred`, `bug`).

## Filter by state name

```graphql
issues(filter: { state: { name: { eq: "Todo" } } }, first: 20) ...
```

Works but fragile — state names are per-team. Prefer `state: { type: { eq: "unstarted" } }` when possible.

## Filter by title substring

```graphql
issues(filter: { title: { contains: "lazy" } }, first: 5) ...
```

## Pitfall: `identifier` is NOT an IssueFilter field

This **does not work**:

```graphql
issues(filter: { identifier: { in: ["GRO-847", "GRO-848"] } })  # ❌
```

Use `issue(id: "GRO-847")` for single issues, or `id: { in: [...] }` with full UUIDs:

```graphql
issues(filter: { id: { in: ["uuid-1", "uuid-2"] } }, first: 10)  # ✅
```

The short identifier (`GRO-847`) works for `issue(id:)` and `issueUpdate(id:)` but **not** in `issues(filter:)`.

## Combine label + state

```graphql
issues(filter: {
  labels: { name: { eq: "agent:fred" } }
  state: { name: { eq: "Todo" } }
}, first: 5) ...
```
