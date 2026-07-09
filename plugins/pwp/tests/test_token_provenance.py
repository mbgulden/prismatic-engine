import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.compiler import (  # noqa: E402
    build_token_provenance,
    canonical_token_json,
    compile_tokens_to_css,
    get_token_provenance_for_tenant,
    get_tokens_for_tenant,
    hash_tokens,
    set_tenant_tokens,
    sha256_text,
)
from plugins.pwp.plugin import PWPDesignTokenPlugin  # noqa: E402


def test_token_hashes_are_deterministic() -> None:
    tokens = get_tokens_for_tenant()
    reordered = json.loads(json.dumps(tokens, sort_keys=True))

    assert canonical_token_json(tokens) == canonical_token_json(reordered)
    assert hash_tokens(tokens) == hash_tokens(reordered)
    assert hash_tokens(tokens) == sha256_text(canonical_token_json(tokens))


def test_token_provenance_for_tenant_records_sources(tmp_path, monkeypatch) -> None:
    temp_tenants_dir = tmp_path / "tenants"
    monkeypatch.setattr("plugins.pwp.compiler.TENANTS_DIR", temp_tenants_dir)

    default_tokens = get_tokens_for_tenant()
    override = json.loads(json.dumps(default_tokens))
    override["colors"]["primary"] = "#abcdef"
    set_tenant_tokens("client-123", override)

    merged_tokens = get_tokens_for_tenant("client-123")
    provenance = get_token_provenance_for_tenant("client-123")

    assert provenance["schemaVersion"] == "pwp.token-provenance.v1"
    assert provenance["tenantId"] == "client-123"
    assert provenance["algorithm"] == "sha256"
    assert provenance["tokenHash"] == hash_tokens(merged_tokens)
    assert provenance["cssHash"] == sha256_text(compile_tokens_to_css(merged_tokens))
    assert provenance["sections"] == sorted(merged_tokens.keys())
    assert [source["kind"] for source in provenance["sources"]] == [
        "pwp_default",
        "tenant_override",
    ]
    assert provenance["sources"][0]["path"] == "templates/tokens.json"
    assert provenance["sources"][1]["path"].endswith("client-123/tokens.json")


def test_build_token_provenance_is_deploy_metadata_friendly() -> None:
    tokens = get_tokens_for_tenant()
    provenance = build_token_provenance(
        tokens,
        tenant_id=None,
        source_records=[
            {
                "kind": "pwp_default",
                "path": "tokens/tokens.json",
                "hash": hash_tokens(tokens),
            }
        ],
    )

    assert set(provenance) == {
        "schemaVersion",
        "tenantId",
        "algorithm",
        "canonicalFormat",
        "tokenHash",
        "cssHash",
        "sections",
        "sources",
    }
    encoded = json.dumps(provenance)
    assert "created" not in encoded.lower()
    assert "/home/" not in encoded


def test_plugin_exposes_token_provenance(tmp_path, monkeypatch) -> None:
    temp_tenants_dir = tmp_path / "tenants"
    monkeypatch.setattr("plugins.pwp.compiler.TENANTS_DIR", temp_tenants_dir)

    plugin = PWPDesignTokenPlugin()
    tokens = plugin.get_tokens()
    override = json.loads(json.dumps(tokens))
    override["colors"]["primary"] = "#0000ff"
    plugin.set_tokens("tenant-abc", override)

    tenant_tokens = plugin.get_tokens("tenant-abc")
    provenance = plugin.get_token_provenance("tenant-abc")

    assert provenance["tenantId"] == "tenant-abc"
    assert provenance["tokenHash"] == hash_tokens(tenant_tokens)
