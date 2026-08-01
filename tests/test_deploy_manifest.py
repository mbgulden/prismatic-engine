"""Unit tests for pe.deploy.manifest DeployManifestStore & DeployRecord (WB-6).
"""

from pe.deploy.manifest import DeployManifestStore, DeployRecord


def test_deploy_manifest_store_lifecycle(tmp_path):
    db_file = tmp_path / "deploy_records.json"
    store = DeployManifestStore(db_path=db_file)

    rec1 = DeployRecord(pr_sha="11111", pr_title="PR 1", success=True)
    rec2 = DeployRecord(pr_sha="22222", pr_title="PR 2", success=True)

    store.record_deploy(rec1)
    store.record_deploy(rec2)

    deploys = store.list_deploys()
    assert len(deploys) == 2

    latest = store.get_latest()
    assert latest is not None
    assert latest.pr_sha in ("11111", "22222")
