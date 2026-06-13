#!/usr/bin/env python3
"""Ned maintenance sweep — run when no agent:ned tasks found.

Combines all 5 sweeps using curl subprocess (NOT urllib.request — HTTP 500 on
paginated team queries). Two-stage: minimal bulk fetch → targeted detail fetch.

  1. AGY mislabel (fred→agy) — scans all non-closed states
  2. agent:done → Done state
  3. Stale agent:ned In Progress → agent:fred
  4. Done issues with agent:ned → agent:done
  5. agent:fred on Done/Canceled → agent:done (excludes AGY-relabeled)

CRITICAL: Bulk queries MUST NOT include `description` or `comments` fields —
these cause HTTP 500 from Linear's GraphQL API on paginated team scans.
Fetch those fields individually per-issue only when needed.

CRITICAL: Use subprocess.run(['curl', ...]) for ALL Linear API calls.
urllib.request produces HTTP 500 on paginated team queries even WITHOUT
description/comments fields. Curl subprocess is the only reliable transport.

Run: python3 /path/to/maintenance-sweep.py
Requires: hardcoded API key inline
"""
import json, subprocess

API_KEY = '$LINEAR_API_KEY'
TEAM_ID = 'b6fb2651-5a1f-4714-9bcd-9eb6e759ffef'
HEADERS = ['-H', f'Authorization: {API_KEY}', '-H', 'Content-Type: application/json']

# Label IDs
NED_ID = '6e0400c9-fc04-4868-86e3-f3156821f413'
FRED_ID = 'a43efb77-534a-4e39-8ff3-76f0e42019d1'
AGY_ID = '1b69d9c0-20a8-45b3-a594-771b8cba75a7'
DONE_LABEL_ID = 'a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b'

# State IDs
DONE_STATE_ID = 'bbf71b3e-9a05-48ce-9418-df8b9c0b8fec'

# AGY signals (title-only for bulk pass; description checked per-candidate)
AGY_TITLE_SIGNALS = [
    'agy', 'sprite', 'visual qa', 'qa pass', 'state frames',
    'frame generation', 'redispatch',
]
NED_DOMAIN_KEYWORDS = [
    'lyria', 'music track', 'music generation', 'audio generation',
    'generate_audio.py', 'music_catalog',
]
INFRA_KEYWORDS = [
    'dispatcher', 'auto-recovery', 'watchdog', 'stall detection',
    'dispatcher tracking',
]
AGY_DESC_SIGNALS = [
    'imagen 3', 'google flow beta', 'ubersuggest', 'ga4', 'search console',
    '_seo/reports/',
]

# Coding-task exclusion: titles containing "sprite" alongside coding keywords
# are implementation work (sprite sheets, rendering, drawImage), not AGY asset
# generation (Imagen 3 sprite generation, visual QA). False-positive example:
#   "[DARIUS] [P0] Slice 1024x1024 sprite sheets into individual frames"
#   → matches 'sprite' signal but is sprite sheet slicing code, not AGY image gen.
SPRITE_CODING_KEYWORDS = [
    'slice', 'drawimage', 'rendering', 'render method', 'implement',
    'refactor', 'extract', 'draw', 'ctx.drawimage', '9-param',
    'sheet slicing', 'sprite sheet', 'drawing code',
]
NED_TRIAGE_SIGNALS = [
    '\u26a0\ufe0f Refactoring Triage',
    '\u2705 Ned:',
    'FLAGGED FOR INTERACTIVE',
    '\u26a0\ufe0f FLAGGED FOR INTERACTIVE',
]


def gql(query_str):
    """Run a GraphQL query via curl subprocess (NOT urllib.request)."""
    payload = json.dumps({"query": query_str})
    result = subprocess.run([
        'curl', '-s', '-X', 'POST',
        'https://api.linear.app/graphql',
        *HEADERS,
        '-d', payload
    ], capture_output=True, text=True, timeout=35)
    return json.loads(result.stdout)


def gql_filter_str(status_list, operator='nin'):
    """Build GraphQL filter string — NOT JSON. Uses unquoted field names.
    
    GraphQL requires unquoted field names: { state: { type: { nin: [...] } } }
    json.dumps() produces quoted names: {"state": ...} which GraphQL rejects.
    """
    items = ', '.join(f'"{s}"' for s in status_list)
    return f'{{ state: {{ type: {{ {operator}: [{items}] }} }} }}'

def fetch_all_issues(nin_states=None, in_states=None):
    """Paginated fetch — MINIMAL fields: no description, no comments.
    
    CRITICAL: Uses GraphQL-native filter syntax, NOT json.dumps().
    json.dumps produces quoted field names ("state") that GraphQL rejects
    with 'Syntax Error: Expected Name, found String "state"'.
    """
    all_issues = []
    cursor = None
    
    if nin_states:
        filter_str = gql_filter_str(nin_states, 'nin')
    elif in_states:
        filter_str = gql_filter_str(in_states, 'in')
    else:
        filter_str = None
    
    while True:
        after_clause = f', after: "{cursor}"' if cursor else ''
        filter_clause = f', filter: {filter_str}' if filter_str else ''
        query = f'''query {{
          team(id: "{TEAM_ID}") {{
            issues(first: 200{filter_clause}{after_clause}) {{
              nodes {{
                id identifier title state {{ name }}
                labels {{ nodes {{ id name }} }}
              }}
              pageInfo {{ hasNextPage endCursor }}
            }}
          }}
        }}'''
        result = gql(query)
        nodes = result['data']['team']['issues']['nodes']
        all_issues.extend(nodes)
        if not result['data']['team']['issues']['pageInfo']['hasNextPage']:
            break
        cursor = result['data']['team']['issues']['pageInfo']['endCursor']
    return all_issues


def fetch_description(issue_id):
    """Fetch description for a single issue — only called for candidates."""
    query = f'query {{ issue(id: "{issue_id}") {{ description }} }}'
    result = gql(query)
    return (result.get('data', {}).get('issue', {}).get('description') or '')


def fetch_comments(issue_id):
    """Fetch last 5 comments for a single issue — only called for sweep 3."""
    query = f'''query {{
      issue(id: "{issue_id}") {{
        comments(first: 5, orderBy: createdAt) {{
          nodes {{ body }}
        }}
      }}
    }}'''
    result = gql(query)
    comments = result.get('data', {}).get('issue', {}).get('comments', {}).get('nodes', [])
    return [c['body'] for c in comments]


def should_be_agy_bulk(issue):
    """Title-only fast check — no description fetch needed."""
    title = (issue.get('title') or '')
    labels = [l['name'] for l in issue['labels']['nodes']]
    if 'agent:fred' not in labels or 'agent:agy' in labels:
        return False
    
    tl = title.lower()
    # Ned domain exclusion
    if any(kw in tl for kw in NED_DOMAIN_KEYWORDS):
        return False
    # Jules prefix
    if tl.startswith('jules:'):
        return False
    # Title signals
    if any(sig in tl for sig in AGY_TITLE_SIGNALS):
        return 'check_desc'
    # background + generate/sprite compound
    if 'background' in tl and ('generate' in tl or 'sprite' in tl):
        return 'check_desc'
    return False


def should_be_agy_full(issue):
    """Full check with description — slower, call only for candidates."""
    title = (issue.get('title') or '')
    tl = title.lower()
    
    # Ned domain exclusion (re-check with desc)
    desc = fetch_description(issue['id'])
    dl = desc.lower()
    if any(kw in tl or kw in dl for kw in NED_DOMAIN_KEYWORDS):
        return False
    
    # Infrastructure exclusion
    has_agy = 'agy' in tl or 'agy' in dl
    has_infra = any(kw in dl for kw in INFRA_KEYWORDS)
    if has_agy and has_infra:
        return False
    
    # Title signals (already passed bulk check, but re-verify)
    if any(sig in tl for sig in AGY_TITLE_SIGNALS):
        # Coding-task exclusion: "sprite" + coding keywords = implementation, not AGY
        if 'sprite' in tl and any(kw in tl or kw in dl for kw in SPRITE_CODING_KEYWORDS):
            return False
        return True
    if 'background' in tl and ('generate' in tl or 'sprite' in tl):
        return True
    
    # Description signals (the ones title-only can't catch)
    if any(sig in dl for sig in AGY_DESC_SIGNALS):
        return True
    
    return False


total_fixes = 0

# ── FETCH ──
print("Fetching non-done issues...")
non_done = fetch_all_issues(nin_states=["completed", "canceled"])
print(f"  Total: {len(non_done)}")

# ── SWEEP 1: AGY Mislabel ──
print("\n--- SWEEP 1: AGY Mislabel (fred→agy) ---")
agy_candidates = [i for i in non_done if should_be_agy_bulk(i)]
print(f"  Title candidates: {len(agy_candidates)}")
agy_confirmed = [i for i in agy_candidates if should_be_agy_full(i)]
print(f"  Confirmed (full check): {len(agy_confirmed)}")
for issue in agy_confirmed:
    current_labels = [l['id'] for l in issue['labels']['nodes']]
    new_labels = [lid for lid in current_labels if lid != FRED_ID]
    if AGY_ID not in new_labels:
        new_labels.append(AGY_ID)
    result = gql(f'mutation {{ issueUpdate(id: "{issue["id"]}", input: {{ labelIds: {json.dumps(new_labels)} }}) {{ success }} }}')
    ok = result.get('data', {}).get('issueUpdate', {}).get('success')
    print(f"    {issue['identifier']}: {'OK' if ok else 'FAIL'}")
    if ok:
        total_fixes += 1

# ── SWEEP 2: agent:done → Done state ──
print("\n--- SWEEP 2: agent:done State Cleanup ---")
done_cleanup = []
for i in non_done:
    labels = [l['name'] for l in i['labels']['nodes']]
    if 'agent:done' in labels and i['state']['name'] not in ('Done', 'Canceled', 'Duplicate'):
        done_cleanup.append(i)
print(f"  Found: {len(done_cleanup)}")
for issue in done_cleanup:
    current_labels = [l['id'] for l in issue['labels']['nodes']]
    result = gql(f'mutation {{ issueUpdate(id: "{issue["id"]}", input: {{ stateId: "{DONE_STATE_ID}", labelIds: {json.dumps(current_labels)} }}) {{ success }} }}')
    ok = result.get('data', {}).get('issueUpdate', {}).get('success')
    print(f"    {issue['identifier']}: {'OK' if ok else 'FAIL'} [{issue['state']['name']}]")
    if ok:
        total_fixes += 1

# ── SWEEP 3: Stale agent:ned In Progress → agent:fred ──
print("\n--- SWEEP 3: Stale agent:ned In Progress ---")
ned_ip = [i for i in non_done if 'agent:ned' in [l['name'] for l in i['labels']['nodes']] and i['state']['name'] == 'In Progress']
print(f"  agent:ned In Progress: {len(ned_ip)}")
stale_ned = []
for i in ned_ip:
    comments = fetch_comments(i['id'])
    for body in comments:
        if any(sig in body for sig in NED_TRIAGE_SIGNALS):
            stale_ned.append(i)
            break
print(f"  Stale (triage signals): {len(stale_ned)}")
for issue in stale_ned:
    current_labels = [l['id'] for l in issue['labels']['nodes']]
    new_labels = [lid for lid in current_labels if lid != NED_ID]
    if FRED_ID not in new_labels:
        new_labels.append(FRED_ID)
    result = gql(f'mutation {{ issueUpdate(id: "{issue["id"]}", input: {{ labelIds: {json.dumps(new_labels)} }}) {{ success }} }}')
    ok = result.get('data', {}).get('issueUpdate', {}).get('success')
    print(f"    {issue['identifier']}: {'OK' if ok else 'FAIL'}")
    if ok:
        total_fixes += 1

# ── SWEEP 4: Done issues with agent:ned → agent:done ──
print("\n--- SWEEP 4: Done issues with agent:ned ---")
done_issues = fetch_all_issues(in_states=["completed", "canceled"])
print(f"  Total Done+Canceled: {len(done_issues)}")
stale_done_ned = [i for i in done_issues if 'agent:ned' in [l['name'] for l in i['labels']['nodes']]]
print(f"  Found: {len(stale_done_ned)}")
for issue in stale_done_ned:
    current_labels = [l['id'] for l in issue['labels']['nodes']]
    new_labels = [lid for lid in current_labels if lid != NED_ID]
    if DONE_LABEL_ID not in new_labels:
        new_labels.append(DONE_LABEL_ID)
    result = gql(f'mutation {{ issueUpdate(id: "{issue["id"]}", input: {{ labelIds: {json.dumps(new_labels)} }}) {{ success }} }}')
    ok = result.get('data', {}).get('issueUpdate', {}).get('success')
    print(f"    {issue['identifier']}: {'OK' if ok else 'FAIL'}")
    if ok:
        total_fixes += 1

# ── SWEEP 5: agent:fred on Done/Canceled → agent:done ──
print("\n--- SWEEP 5: agent:fred on Done/Canceled → agent:done ---")
fred_on_done = [i for i in done_issues if 'agent:fred' in [l['name'] for l in i['labels']['nodes']] and 'agent:agy' not in [l['name'] for l in i['labels']['nodes']]]
print(f"  Found: {len(fred_on_done)}")
for issue in fred_on_done:
    current_labels = [l['id'] for l in issue['labels']['nodes']]
    new_labels = [lid for lid in current_labels if lid != FRED_ID]
    if DONE_LABEL_ID not in new_labels:
        new_labels.append(DONE_LABEL_ID)
    result = gql(f'mutation {{ issueUpdate(id: "{issue["id"]}", input: {{ labelIds: {json.dumps(new_labels)} }}) {{ success }} }}')
    ok = result.get('data', {}).get('issueUpdate', {}).get('success')
    print(f"    {issue['identifier']}: {'OK' if ok else 'FAIL'}")
    if ok:
        total_fixes += 1

# ── Summary ──
print(f"\n=== COMPLETE: {total_fixes} fixes applied ===")
if total_fixes == 0:
    print("[SILENT]")
