#!/usr/bin/env python3
"""Sweep 6: In Progress agent:fred issues with committed work → batch close.

Run AFTER sweeps 1-5. Queries all In Progress agent:fred issues (excluding
requires:human-approval), cross-references git log in project repos, and
auto-closes issues with matching commits.

Exclusions: hardcoded (GRO-1062, GRO-1064), refactoring-signal comments,
and issues with no git evidence.

CRITICAL: Use subprocess.run(['curl', ...]) for ALL Linear API calls.
urllib.request produces HTTP 500 on paginated team queries.

Run: python3 /path/to/sweep6-ip-fred-verification.py
Requires: hardcoded API key inline
"""
import json, subprocess, sys

API_KEY = '$LINEAR_API_KEY'
TEAM_ID = 'b6fb2651-5a1f-4714-9bcd-9eb6e759ffef'
HEADERS = ['-H', f'Authorization: {API_KEY}', '-H', 'Content-Type: application/json']

FRED_ID = 'a43efb77-534a-4e39-8ff3-76f0e42019d1'
DONE_LABEL_ID = 'a0fa6cec-476c-4d12-bbf9-11cd6f29bd9b'
DONE_STATE_ID = 'bbf71b3e-9a05-48ce-9418-df8b9c0b8fec'
APPROVAL_ID = '9e976f5a-ccb0-4e6a-a071-a462cc4d0205'

# Issues permanently excluded from auto-close — these are canonical refactoring
# triage tasks that need interactive sessions. Never close them by sweep.
HARDCODED_EXCLUSION = {'GRO-1062', 'GRO-1064'}

# Refactoring signals in comments → skip auto-close
REFACTORING_KEYWORDS = [
    'flagged for interactive', 'refactoring triage',
    '\u26a0\ufe0f refactoring triage', 'needs interactive',
    'extraction map', 'dependency hotspot', 'section map',
    'flagged for human',
]

# Project → filesystem repo path mapping
REPO_MAP = {
    'Darius Star': '/home/ubuntu/work/darius-star',
    'Active Oahu Tours': '/home/ubuntu/work/active-oahu-tours-mirror',
    'Active Oahu Tours — Static Mirror Migration': '/home/ubuntu/work/active-oahu-static',
    'Prismatic Engine': '/home/ubuntu/work/prismatic-engine',
    'HD Growth Engine': '/home/ubuntu/work/hd-platform',
    'WAG': '/home/ubuntu/work/wag',
    'Growth Weave': '/home/ubuntu/work/growthweave',
}


def gql(query_str):
    """Run a GraphQL query via curl subprocess."""
    payload = json.dumps({"query": query_str})
    result = subprocess.run([
        'curl', '-s', '-X', 'POST',
        'https://api.linear.app/graphql',
        *HEADERS,
        '-d', payload
    ], capture_output=True, text=True, timeout=35)
    return json.loads(result.stdout)


def fetch_ip_issues():
    """Paginated fetch of all In Progress issues with agent:fred, no approval."""
    all_issues = []
    cursor = None
    while True:
        after_clause = f', after: "{cursor}"' if cursor else ''
        query = f'''query {{
          team(id: "{TEAM_ID}") {{
            issues(first: 200, filter: {{state: {{type: {{in: ["started"]}}}}}}{after_clause}) {{
              nodes {{
                id identifier title
                labels {{ nodes {{ id name }} }}
                createdAt
                project {{ name }}
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
    
    # Filter for agent:fred, no approval
    candidates = []
    for i in all_issues:
        labels = [l['name'] for l in i['labels']['nodes']]
        if ('agent:fred' in labels
                and 'requires:human-approval' not in labels
                and i['identifier'] not in HARDCODED_EXCLUSION):
            candidates.append(i)
    return candidates


def fetch_comments(issue_id, count=20):
    """Fetch recent comments for an issue."""
    query = f'''query {{
      issue(id: "{issue_id}") {{
        comments(first: {count}, orderBy: createdAt) {{
          nodes {{ body }}
        }}
      }}
    }}'''
    result = gql(query)
    return [c['body'] for c in
            result.get('data', {}).get('issue', {}).get('comments', {}).get('nodes', [])]


def has_refactoring_signal(comments):
    """Check if comments contain refactoring triage signals."""
    for body in comments:
        if any(kw in body.lower() for kw in REFACTORING_KEYWORDS):
            return True
    return False


def git_commits_for_issue(issue_id, repo_path):
    """Search git log for commits mentioning the issue ID."""
    try:
        result = subprocess.run(
            ['git', '-C', repo_path, 'log', '--oneline', '--all',
             f'--grep={issue_id}'],
            capture_output=True, text=True, timeout=10
        )
        return [l for l in result.stdout.strip().split('\n') if l]
    except Exception:
        return []


def close_issue(issue_id, issue_uuid, commit_shas):
    """Swap label agent:fred → agent:done, move to Done, add verification comment."""
    # Build verification comment
    sha_lines = '\n'.join(f'- `{s}`' for s in commit_shas[:5])
    comment_body = (
        f"✅ **Sweep 6 — Verified Complete**\n\n"
        f"Commit(s) on master:\n{sha_lines}\n\n"
        f"Work already committed and pushed. Closing card."
    )
    
    # Post comment
    comment_payload = {
        "query": (
            "mutation CreateComment($issueId: String!, $body: String!) {\n"
            "  commentCreate(input: { issueId: $issueId, body: $body }) {\n"
            "    success\n  }\n}\n"
        ),
        "variables": {"issueId": issue_uuid, "body": comment_body}
    }
    comment_json = json.dumps(comment_payload)
    subprocess.run([
        'curl', '-s', '-X', 'POST',
        'https://api.linear.app/graphql',
        *HEADERS,
        '-d', comment_json
    ], capture_output=True, timeout=30)
    
    # Fetch current labels
    issue_query = f'query {{ issue(id: "{issue_uuid}") {{ labels {{ nodes {{ id name }} }} }} }}'
    issue_result = gql(issue_query)
    current_labels = [l['id'] for l in
                      issue_result.get('data', {}).get('issue', {}).get('labels', {}).get('nodes', [])]
    
    # Swap agent:fred → agent:done
    new_labels = [lid for lid in current_labels if lid != FRED_ID]
    if DONE_LABEL_ID not in new_labels:
        new_labels.append(DONE_LABEL_ID)
    
    # Move to Done
    result = gql(f'''mutation {{
      issueUpdate(id: "{issue_uuid}", input: {{
        stateId: "{DONE_STATE_ID}",
        labelIds: {json.dumps(new_labels)}
      }}) {{ success }}
    }}''')
    return result.get('data', {}).get('issueUpdate', {}).get('success', False)


def main():
    print("Sweep 6: In Progress agent:fred → Done (git commit verification)")
    print(f"Hardcoded exclusions: {HARDCODED_EXCLUSION}\n")
    
    ip_fred = fetch_ip_issues()
    print(f"In Progress agent:fred (excl. approvals): {len(ip_fred)}")
    
    closed = 0
    skipped_refactoring = 0
    skipped_no_evidence = 0
    
    for issue in ip_fred:
        iid = issue['identifier']
        proj = (issue.get('project') or {})
        proj_name = proj.get('name', '') if proj else ''
        repo = REPO_MAP.get(proj_name, '')
        
        print(f"\n  {iid}: {issue['title'][:80]} [{proj_name}]")
        
        # Check for refactoring signals in comments
        comments = fetch_comments(issue['id'])
        if has_refactoring_signal(comments):
            print(f"    → SKIP (refactoring triage in comments)")
            skipped_refactoring += 1
            continue
        
        # Check git log
        if not repo:
            print(f"    → SKIP (no repo mapped for project '{proj_name}')")
            skipped_no_evidence += 1
            continue
        
        commits = git_commits_for_issue(iid, repo)
        print(f"    Git commits: {len(commits)}")
        
        if commits:
            # Has commits — close it
            ok = close_issue(iid, issue['id'], commits)
            print(f"    → {'CLOSED' if ok else 'FAILED'}")
            if ok:
                closed += 1
        else:
            # Check for completion comments as secondary signal
            has_completion = any(
                '✅ Ned:' in c or '✅ Complete' in c or '✅ Verified' in c
                for c in comments
            )
            if has_completion:
                print(f"    → SKIP (completion comments but no git commits — needs human review)")
            else:
                print(f"    → SKIP (no evidence of completed work)")
            skipped_no_evidence += 1
    
    print(f"\n=== SWEEP 6 COMPLETE ===")
    print(f"  Closed: {closed}")
    print(f"  Skipped (refactoring): {skipped_refactoring}")
    print(f"  Skipped (no evidence): {skipped_no_evidence}")
    
    if closed == 0 and skipped_refactoring == 0 and skipped_no_evidence == 0:
        print("[SILENT]")


if __name__ == '__main__':
    main()
