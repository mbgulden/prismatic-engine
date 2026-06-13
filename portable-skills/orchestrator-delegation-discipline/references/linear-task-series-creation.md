# Linear Task Series Creation via GraphQL API

Proven pattern for programmatically creating parent tasks and subtasks on Linear.

## When to Use

- Sprint planning: create a feature series with parent + 4-8 subtasks
- Assigning work across agent lanes (AGY designs → Fred builds → Jules reviews)
- Building out a project backlog from a user's stream of consciousness

## The Pattern

**Do NOT use `execute_code` for this.** The nested JSON escaping in GraphQL mutations causes silent failures. Use Python `subprocess.run` + curl from terminal instead.

### Step 1: Read the API key from .env

```python
import json, subprocess

with open(os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "/.hermes/profiles/orchestrator/.env") as f:
    for line in f:
        if line.startswith("LINEAR_API_KEY="):
            api_key = line.split("=", 1)[1].strip()
            break
```

### Step 2: Create the parent task

```python
team = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"  # GrowthWebDev

parent_mutation = {
    "query": f"""mutation {{
        issueCreate(input: {{
            teamId: "{team}",
            title: "Feature Name: Description",
            description: "Multi-line description...",
            priority: 2
        }}) {{
            issue {{ id identifier title }}
        }}
    }}"""
}

result = subprocess.run([
    "curl", "-s", "-X", "POST", "https://api.linear.app/graphql",
    "-H", f"Authorization: {api_key}",
    "-H", "Content-Type: application/json",
    "-d", json.dumps(mutation)
], capture_output=True, text=True)

data = json.loads(result.stdout)
parent_id = data["data"]["issueCreate"]["issue"]["id"]
```

### Step 3: Create subtasks with parentId

```python
tasks = [
    ("AGY: Design X", "Design brief for X..."),
    ("Fred: Build X", "Implementation..."),
    ("Jules: Review X", "Review criteria..."),
]

for title, desc in tasks:
    mutation = {
        "query": f"""mutation {{
            issueCreate(input: {{
                teamId: "{team}",
                title: {json.dumps(title)},
                description: {json.dumps(desc)},
                priority: 2,
                parentId: "{parent_id}"
            }}) {{
                issue {{ id identifier title }}
            }}
        }}"""
    }
    
    result = subprocess.run([
        "curl", "-s", "-X", "POST", "https://api.linear.app/graphql",
        "-H", f"Authorization: {api_key}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(mutation)
    ], capture_output=True, text=True)
    
    iss = json.loads(result.stdout)["data"]["issueCreate"]["issue"]
    print(f"{iss['identifier']} - {iss['title']}")
```

## Pitfalls

- **`execute_code` JSON escaping fails silently**: The triple-nested braces in GraphQL mutations (`"query": f"""mutation {{{...}}}"""`) break JSON parsing in `execute_code`. Always use `terminal()` with Python subprocess instead.
- **Label IDs are UUIDs, not names**: `labelIds` expects UUIDs like `"a1b2c3d4-..."`, not `"agent:agy"`. Skip labels during creation and add them via dashboard or a separate API call.
- **`$LINEAR_API_KEY` may not be in subprocess env**: Read from the .env file explicitly instead of relying on `os.environ`.
- **Description length**: Linear descriptions have no hard limit, but keep them concise. The API accepts multi-line strings with escaped newlines.
- **Team ID is hardcoded**: The GrowthWebDev team ID is `b6fb2651-5a1f-4714-9bcd-9eb6e759ffef`. Verify before creating tasks in other teams.

## Verification

After creation, check Linear dashboard or query:

```graphql
query {
  issues(filter: { parent: { id: { eq: "<parent_id>" } } }) {
    nodes { identifier title state { name } }
  }
}
```
