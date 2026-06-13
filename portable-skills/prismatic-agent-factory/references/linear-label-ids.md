# Linear Label IDs — GrowthWebDev Team
# Live-queried 2026-06-13. These change — ALWAYS query live before using.

agent:kai           c4d929be-8d15-4482-b6d7-a5ed85aa2e73
agent:kai-css       f246eb61-5e84-4594-8b31-249a588c5648
agent:kai-content   4da5ee04-607f-4188-b9e0-18af413ad62f
agent:kai-js        61180755-4f1f-48fb-bedf-ddd02f7cffaf
agent:agy           1b69d9c0-20a8-45b3-a594-771b8cba75a7
agent:ned           6e0400c9-fc04-4868-86e3-f3156821f413
agent:fred          a43efb77-534a-4e39-8ff3-76f0e42019d1
agent:done          a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b
agent:jules         5bc301fb-e4dc-404c-97fb-290c49ed2528
agent:codex         2a478990-b901-4f86-84d0-3a927a38293e
agent:autobot       a060a0cd-4702-46a1-8571-86ad19a13688
agent:orchestrator  ed1ec1a0-0e84-4578-9ca4-68901e9b38a1
agent:antigravity-cli 2fdf7706-b9ed-4d6e-87a5-04ffe882a0b4
agent:hermes        02684050-ad7d-412d-9562-0be2cfb77317
agent:qwen-local    ed38623e-5c2e-4868-af43-f00812556cca
agent:local-hermes  9fdbe2fe-42e9-45ff-992e-b619807ef60f

# State IDs
Todo         3d29ebe3-00cf-428b-b52a-bfecb5ae4410
In Progress  734901ee-58f0-457c-b9a0-f911c0da13a4
Done         bbf71b3e-9a05-48ce-9418-df8b9c0b8fec
Canceled     a19484ec-9752-4c31-8110-f5043312e328
Backlog      e5544f55-482e-49ac-b0f7-3dd2e1775dbb
In Review    6a5050ad-3386-4623-a404-7f2791047cd5

# Team ID
GrowthWebDev  b6fb2651-5a1f-4714-9bcd-9eb6e759ffef

# Live Query
export LINEAR_API_KEY=...
curl -s -X POST "https://api.linear.app/graphql" \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"query { issueLabels(first: 50, filter: {team: {id: {eq: \\\"b6fb2651-5a1f-4714-9bcd-9eb6e759ffef\\\"}}}) { nodes { id name } } }\"}"
