#!/usr/bin/env python3
"""Batch-close verified Linear issues: move to Done, swap agent:fred→agent:done, post comments.

Usage pattern (Ned cron / execution agent):
  1. Query agent:fred issues, filter out AGY/human-approval/Done
  2. Verify each fix exists on master via source inspection
  3. Populate ISSUES dict below with verified commit + summary
  4. Run this script → all issues closed with structured verification comments

Requires: LINEAR_API_KEY env var, urllib (stdlib only).
"""

import json, os, urllib.request, time

KEY = os.environ['LINEAR_API_KEY']

# --- CONFIGURE THESE PER-RUN ---
DONE_STATE_ID = 'bbf71b3e-9a05-48ce-9418-df8b9c0b8fec'  # GRO team Done state
FRED_LABEL_ID = 'a43efb77-534a-4e39-8ff3-76f0e42019d1'
DONE_LABEL_ID = 'a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b'

# {identifier: {uuid, commit, summary}}
ISSUES = {
    # 'GRO-NNN': {
    #     'uuid': '...',
    #     'commit': 'abcdef1',
    #     'summary': 'One-line description of what the commit fixed'
    # },
}

def gql(query, variables=None):
    req = urllib.request.Request('https://api.linear.app/graphql',
        data=json.dumps({'query': query, 'variables': variables or {}}).encode(),
        headers={'Authorization': KEY, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def get_labels(issue_uuid):
    r = gql('query($id: String!) { issue(id: $id) { labels { nodes { id name } } } }',
            {'id': issue_uuid})
    return r['data']['issue']['labels']['nodes']

def update_issue(issue_uuid):
    """Move to Done + swap agent:fred→agent:done labels."""
    current_labels = get_labels(issue_uuid)
    current_ids = [l['id'] for l in current_labels]
    
    # Build new label set: remove fred, add done
    new_ids = [lid for lid in current_ids if lid != FRED_LABEL_ID]
    if DONE_LABEL_ID not in new_ids:
        new_ids.append(DONE_LABEL_ID)
    
    payload = 'mutation { issueUpdate(id: "' + issue_uuid + '", input: { stateId: "' + DONE_STATE_ID + '", labelIds: ' + json.dumps(new_ids) + ' }) { success issue { identifier state { name } labels { nodes { name } } } } }'
    r = gql(payload)
    result = r['data']['issueUpdate']
    if not result['success']:
        time.sleep(1)
        r = gql(payload)
        result = r['data']['issueUpdate']
    return result['success'], result.get('issue', {})

def post_comment(issue_uuid, identifier, info):
    body = f"""## ✅ Ned — {identifier}: Fix Verified & Closed

**Commit:** `{info['commit']}`

**Fix verified:** {info['summary']}

**Verification method:** Source code inspection + git log confirmation on master branch. All changes confirmed present and correct.

**Status:** → Done. `agent:fred` → `agent:done`."""
    
    mutation = '''mutation($body: String!) {
        commentCreate(input: { issueId: "''' + issue_uuid + '''", body: $body }) {
            success
            comment { id }
        }
    }'''
    r = gql(mutation, {'body': body})
    return r['data']['commentCreate']['success']

if __name__ == '__main__':
    if not ISSUES:
        print("ERROR: Populate ISSUES dict before running.")
        exit(1)
    
    results = []
    for identifier, info in ISSUES.items():
        uuid = info['uuid']
        print(f"Processing {identifier}...")
        
        comment_ok = post_comment(uuid, identifier, info)
        print(f"  Comment: {'OK' if comment_ok else 'FAIL'}")
        
        success, issue_data = update_issue(uuid)
        if success:
            state = issue_data.get('state', {}).get('name', '?')
            labels = [l['name'] for l in issue_data.get('labels', {}).get('nodes', [])]
            print(f"  State: {state}, Labels: {labels}")
        else:
            print(f"  State update: FAIL")
        results.append((identifier, comment_ok, success))
        time.sleep(0.15)  # Rate limit breathing room
    
    print("\n=== RESULTS ===")
    for ident, c, s in results:
        status = "✅" if (c and s) else "⚠️"
        print(f"{status} {ident}: comment={c}, state={s}")
