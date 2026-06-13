# Linear Workflow State IDs — GrowthWebDev Team

Stable state IDs for the GrowthWebDev team (`b6fb2651-5a1f-4714-9bcd-9eb6e759ffef`).
Use these in `issueUpdate` mutations when moving issues between states.

## States

| State | ID | Type |
|-------|----|------|
| Backlog | `e5544f55-482e-49ac-b0f7-3dd2e1775dbb` | backlog |
| Todo | `3d29ebe3-00cf-428b-b52a-bfecb5ae4410` | unstarted |
| In Progress | `734901ee-58f0-457c-b9a0-f911c0da13a4` | started |
| In Review | `6a5050ad-3386-4623-a404-7f2791047cd5` | started |
| Done | `bbf71b3e-9a05-48ce-9418-df8b9c0b8fec` | completed |
| Canceled | `a19484ec-9752-4c31-8110-f5043312e328` | canceled |
| Duplicate | `8a67aa62-ee98-4d67-a513-64217d8859c3` | duplicate |

## Agent Label IDs

| Label | ID |
|-------|----|
| agent:ned | `6e0400c9-fc04-4868-86e3-f3156821f413` |
| agent:fred | `a43efb77-534a-4e39-8ff3-76f0e42019d1` |
| agent:agy | `1b69d9c0-20a8-45b3-a594-771b8cba75a7` |
| agent:done | `a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b` |
| requires:human-approval | `9e976f5a-ccb0-4e6a-a071-a462cc4d0205` |

## Closing an Issue (Move to Done)

```python
done_label_id = 'a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b'
done_state_id = 'bbf71b3e-9a05-48ce-9418-df8b9c0b8fec'

mutation = f'''mutation {{ issueUpdate(id: "{uuid}", input: {{ labelIds: ["{done_label_id}"], stateId: "{done_state_id}" }}) {{ success }} }}'''
```

**Important:** `stateId` is REQUIRED to move to Done — setting `labelIds` alone won't change the state.

## Querying Live States

To refresh these IDs (they rarely change but can be verified):

```graphql
query { team(id: "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef") { states { nodes { id name type } } } }
```
