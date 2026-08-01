"""Deep functional verification test suite for Workspace Tree (PR #418).
"""

from fastapi.testclient import TestClient

from prismatic.gateway.server import app


def test_deep_workspace_tree_verification():
    client = TestClient(app)

    # 1. Test tree endpoint
    res = client.get("/api/workspace/tree")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"

    tree_data = res.json()
    assert "by_category" in tree_data
    cats = list(tree_data["by_category"].keys())
    assert len(cats) > 0, "No categories found in tree"

    total_docs = sum(len(entries) for entries in tree_data["by_category"].values())
    assert total_docs > 0, "No cataloged documents found in tree"

    # Get sample doc
    first_cat = cats[0]
    first_doc = tree_data["by_category"][first_cat][0]
    doc_id = first_doc["id"]
    doc_path = first_doc["path"]

    # 2. Test entry content fetching
    res_entry = client.get(f"/api/workspace/entry/{doc_id}")
    assert res_entry.status_code == 200
    entry_data = res_entry.json()
    assert "entry" in entry_data
    assert "content" in entry_data
    assert entry_data["entry"]["id"] == doc_id

    # 3. Test signed share link generation (24h TTL)
    res_share = client.post(f"/api/workspace/share?doc_id={doc_id}")
    assert res_share.status_code == 200
    share_data = res_share.json()
    share_url = share_data["share_url"]
    token = share_data["token"]

    # 4. Test viewing document via share URL
    res_view = client.get(share_url)
    assert res_view.status_code == 200
    view_data = res_view.json()
    assert view_data["entry"]["id"] == doc_id

    # 5. Test revoking share token (DELETE /api/workspace/share/{token})
    res_revoke = client.delete(f"/api/workspace/share/{token}")
    assert res_revoke.status_code == 200

    # 6. Test revoked token access fails (403)
    res_revoked_view = client.get(share_url)
    assert res_revoked_view.status_code == 403

    # 7. Test manual acceptance override (POST /api/workspace/acceptance/override)
    res_override = client.post(f"/api/workspace/acceptance/override?path={doc_path}&reason=Verified_by_Operator")
    assert res_override.status_code == 200
    override_data = res_override.json()
    assert override_data["result"]["passed"] is True
