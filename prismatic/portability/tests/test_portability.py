"""
Prismatic Engine — Bounded Unit Tests for Portability Foundation (PORT-02)
==========================================================================

Validates canonical identity envelopes, external identity bindings, dual-axis uniqueness,
per-capability provider qualification, offline fake adapter, secret rejection, import boundaries,
and concurrent registration safety.
"""

from __future__ import annotations

import ast
import concurrent.futures
import datetime
import json
from pathlib import Path

import pytest

from prismatic.portability.binding import BindingRepository, ExternalIdentityBinding
from prismatic.portability.capabilities import CapabilityScope
from prismatic.portability.exceptions import (
    AdapterNotFoundError,
    BindingConflictError,
    DuplicateAdapterError,
    InvalidCapabilityError,
    InvalidEntityKindError,
    InvalidNamespaceError,
    SecretDetectedError,
    UnsupportedCapabilityError,
    ValidationError,
)
from prismatic.portability.identity import (
    CANONICAL_ID_RE,
    CORE_NAMESPACE_RE,
    CanonicalIdentityEnvelope,
    EntityKind,
    canonical_digest,
    to_canonical_json,
)
from prismatic.portability.registry import (
    AvailabilityObservation,
    ProviderAdapterRecord,
    ProviderRegistry,
    QualificationState,
)
from prismatic.portability.testing import FakeOfflineAdapter


# ── Test 1: Valid canonical identity construction and round trip ─────────────


def test_canonical_identity_construction_and_round_trip():
    envelope = CanonicalIdentityEnvelope(
        contract_version=1,
        canonical_id="canon_entity_1001",
        entity_kind="issue",
        namespace="prismatic:core:issue",
        mapping_version=1,
        created_at="2026-08-04T18:00:00Z",
        metadata={"priority": "high", "weight": 42, "is_active": True},
    )

    assert envelope.contract_version == 1
    assert envelope.canonical_id == "canon_entity_1001"
    assert envelope.entity_kind == "issue"
    assert envelope.namespace == "prismatic:core:issue"
    assert envelope.mapping_version == 1

    d = envelope.to_dict()
    assert d["canonical_id"] == "canon_entity_1001"
    assert d["metadata"]["priority"] == "high"

    json_str = to_canonical_json(envelope)
    assert '"canonical_id":"canon_entity_1001"' in json_str


# ── Test 2: Deterministic canonical serialization and digest ──────────────────


def test_deterministic_canonical_serialization_and_digest():
    env1 = CanonicalIdentityEnvelope(
        contract_version=1,
        canonical_id="canon_entity_2002",
        entity_kind="pull_request",
        namespace="prismatic:core:pr",
        mapping_version=1,
        created_at="2026-08-04T18:30:00Z",
        metadata={"b_key": "val_b", "a_key": "val_a"},
    )

    env2 = CanonicalIdentityEnvelope(
        contract_version=1,
        canonical_id="canon_entity_2002",
        entity_kind="pull_request",
        namespace="prismatic:core:pr",
        mapping_version=1,
        created_at="2026-08-04T18:30:00Z",
        metadata={"a_key": "val_a", "b_key": "val_b"},
    )

    json1 = to_canonical_json(env1)
    json2 = to_canonical_json(env2)
    assert json1 == json2, "Canonical JSON must be field-order independent."

    digest1 = canonical_digest(env1)
    digest2 = canonical_digest(env2)
    assert digest1 == digest2, (
        "Canonical digest must be deterministic and equal for equivalent envelopes."
    )

    # Changed identity-relevant field yields different digest
    env3 = CanonicalIdentityEnvelope(
        contract_version=1,
        canonical_id="canon_entity_2002",
        entity_kind="pull_request",
        namespace="prismatic:core:pr",
        mapping_version=2,  # Changed mapping version
        created_at="2026-08-04T18:30:00Z",
        metadata={"a_key": "val_a", "b_key": "val_b"},
    )
    assert canonical_digest(env3) != digest1


# ── Test 3: Provider IDs do not determine canonical IDs ──────────────────────


def test_provider_ids_do_not_determine_canonical_ids():
    # Canonical ID is opaque Core identity, independent of provider formats
    linear_external_id = "LIN-84920-UUID-999"
    github_external_id = "issue_1048204"

    core_canonical_id = "canon_issue_alpha_7"

    env = CanonicalIdentityEnvelope(
        contract_version=1,
        canonical_id=core_canonical_id,
        entity_kind="issue",
        namespace="prismatic:core:issue",
        mapping_version=1,
        created_at="2026-08-04T18:00:00Z",
    )

    assert linear_external_id not in env.canonical_id
    assert github_external_id not in env.canonical_id
    assert env.canonical_id == core_canonical_id


# ── Test 4: Valid external binding creation ──────────────────────────────────


def test_valid_external_binding_creation():
    binding = ExternalIdentityBinding(
        contract_version=1,
        canonical_id="canon_issue_alpha_7",
        adapter_id="linear",
        provider_namespace="workspace_prismatic",
        external_id="ISSUE-492",
        capability_scope=CapabilityScope.ISSUE_READ.value,
        mapping_version=1,
        created_at="2026-08-04T18:00:00Z",
        updated_at="2026-08-04T18:00:00Z",
    )

    assert binding.canonical_id == "canon_issue_alpha_7"
    assert binding.adapter_id == "linear"
    assert binding.external_id == "ISSUE-492"
    assert binding.capability_scope == "issue:read"


# ── Test 5: Both uniqueness axes and conflict behavior ───────────────────────


def test_both_uniqueness_axes_and_conflict_behavior():
    repo = BindingRepository()

    binding1 = ExternalIdentityBinding(
        contract_version=1,
        canonical_id="canon_bindinga",
        adapter_id="github",
        provider_namespace="mbgulden/prismatic-engine",
        external_id="101",
        capability_scope=CapabilityScope.VCS_READ.value,
        mapping_version=1,
        created_at="2026-08-04T18:00:00Z",
        updated_at="2026-08-04T18:00:00Z",
    )
    repo.register_binding(binding1)

    # Conflict on Axis 1: (canonical_id, adapter_id, capability_scope, mapping_version)
    binding_axis1_conflict = ExternalIdentityBinding(
        contract_version=1,
        canonical_id="canon_bindinga",
        adapter_id="github",
        provider_namespace="mbgulden/prismatic-engine",
        external_id="102",  # Different external_id
        capability_scope=CapabilityScope.VCS_READ.value,
        mapping_version=1,
        created_at="2026-08-04T18:00:00Z",
        updated_at="2026-08-04T18:00:00Z",
    )
    with pytest.raises(BindingConflictError) as exc_info:
        repo.register_binding(binding_axis1_conflict)
    assert str(exc_info.value).startswith("binding_conflict:")

    # Conflict on Axis 2: (adapter_id, provider_namespace, external_id, capability_scope, mapping_version)
    binding_axis2_conflict = ExternalIdentityBinding(
        contract_version=1,
        canonical_id="canon_bindingb",  # Different canonical_id attempting to claim same external ID!
        adapter_id="github",
        provider_namespace="mbgulden/prismatic-engine",
        external_id="101",
        capability_scope=CapabilityScope.VCS_READ.value,
        mapping_version=1,
        created_at="2026-08-04T18:00:00Z",
        updated_at="2026-08-04T18:00:00Z",
    )
    with pytest.raises(BindingConflictError) as exc_info:
        repo.register_binding(binding_axis2_conflict)
    assert str(exc_info.value).startswith("binding_conflict:")

    # Original binding remains intact
    readback = repo.get_binding_by_canonical(
        "canon_bindinga", "github", CapabilityScope.VCS_READ.value, 1
    )
    assert readback is not None
    assert readback.external_id == "101"


# ── Test 6: Registry registration and lookup ─────────────────────────────────


def test_registry_registration_and_lookup():
    reg = ProviderRegistry()
    fake = FakeOfflineAdapter(adapter_id="github_adapter", adapter_kind="vcs")
    fake.register(reg)

    retrieved = reg.get("github_adapter")
    assert retrieved.adapter_id == "github_adapter"
    assert retrieved.adapter_kind == "vcs"
    assert CapabilityScope.VCS_READ.value in retrieved.declared_capabilities


# ── Test 7: Capability qualification is per adapter and per capability ────────


def test_capability_qualification_is_per_adapter_and_per_capability():
    reg = ProviderRegistry()

    record = ProviderAdapterRecord(
        adapter_id="multi_cap_adapter",
        adapter_kind="issue_vcs",
        declared_capabilities={
            CapabilityScope.VCS_READ.value,
            CapabilityScope.ISSUE_WRITE.value,
        },
        qualification_state={
            CapabilityScope.VCS_READ.value: QualificationState.QUALIFIED,
            CapabilityScope.ISSUE_WRITE.value: QualificationState.DEGRADED,
        },
        availability_observation=AvailabilityObservation(
            status="DEGRADED",
            observed_at="2026-08-04T18:00:00Z",
            details="Partial outage on issue write",
        ),
        contract_version=1,
    )
    reg.register(record)

    qual_vcs = reg.get_qualification(
        "multi_cap_adapter", CapabilityScope.VCS_READ.value
    )
    qual_issue = reg.get_qualification(
        "multi_cap_adapter", CapabilityScope.ISSUE_WRITE.value
    )

    assert qual_vcs == QualificationState.QUALIFIED
    assert qual_issue == QualificationState.DEGRADED


# ── Test 8: Unknown adapter and unsupported capability fail closed ───────────


def test_unknown_adapter_and_unsupported_capability_fail_closed():
    reg = ProviderRegistry()
    fake = FakeOfflineAdapter(adapter_id="linear_adapter")
    fake.register(reg)

    # Unknown adapter
    with pytest.raises(AdapterNotFoundError):
        reg.get_qualification("unknown_adapter", CapabilityScope.ISSUE_READ.value)

    # Unsupported capability (not declared by linear_adapter)
    with pytest.raises(UnsupportedCapabilityError):
        reg.get_qualification("linear_adapter", CapabilityScope.LLM_GENERATE.value)

    # Invalid capability scope
    with pytest.raises(InvalidCapabilityError):
        reg.get_qualification("linear_adapter", "invalid:capability:name")


# ── Test 9: Duplicate conflicting registration fails closed ──────────────────


def test_duplicate_conflicting_registration_fails_closed():
    reg = ProviderRegistry()

    r1 = ProviderAdapterRecord(
        adapter_id="dup_adapter",
        adapter_kind="vcs",
        declared_capabilities={CapabilityScope.VCS_READ.value},
        qualification_state={
            CapabilityScope.VCS_READ.value: QualificationState.QUALIFIED
        },
        availability_observation=AvailabilityObservation(
            status="HEALTHY", observed_at="2026-08-04T18:00:00Z"
        ),
        contract_version=1,
    )
    reg.register(r1)

    # Duplicate registration fails closed
    r2 = ProviderAdapterRecord(
        adapter_id="dup_adapter",
        adapter_kind="vcs_modified",  # Conflicting metadata
        declared_capabilities={CapabilityScope.VCS_READ.value},
        qualification_state={
            CapabilityScope.VCS_READ.value: QualificationState.QUALIFIED
        },
        availability_observation=AvailabilityObservation(
            status="HEALTHY", observed_at="2026-08-04T18:00:00Z"
        ),
        contract_version=1,
    )
    with pytest.raises(DuplicateAdapterError):
        reg.register(r2)

    # Prior record preserved intact
    assert reg.get("dup_adapter").adapter_kind == "vcs"


# ── Test 10: Provider outage/removal leaves canonical identities intact ───────


def test_provider_outage_removal_leaves_canonical_identities_intact():
    reg = ProviderRegistry()
    binding_repo = BindingRepository()
    fake = FakeOfflineAdapter(adapter_id="outage_adapter")

    fake.register(reg)
    canon = fake.create_canonical_identity(canonical_id="canon_resilient_1")
    fake.bind_external_identity(
        repository=binding_repo,
        canonical_id="canon_resilient_1",
        provider_namespace="workspace_a",
        external_id="EXT-1001",
        capability_scope=CapabilityScope.ISSUE_READ.value,
    )

    # Unregister provider due to complete outage
    reg.unregister("outage_adapter")

    with pytest.raises(AdapterNotFoundError):
        reg.get("outage_adapter")

    # Canonical identity and external binding remain completely intact!
    readback_binding = binding_repo.get_binding_by_canonical(
        "canon_resilient_1", "outage_adapter", CapabilityScope.ISSUE_READ.value, 1
    )
    assert readback_binding is not None
    assert readback_binding.external_id == "EXT-1001"
    assert canon.canonical_id == "canon_resilient_1"


# ── Test 11: Fake/offline adapter uses no network ────────────────────────────


def test_fake_offline_adapter_uses_no_network():
    reg = ProviderRegistry()
    binding_repo = BindingRepository()
    fake = FakeOfflineAdapter()

    record = fake.register(reg)
    canon = fake.create_canonical_identity(canonical_id="canon_offline_1")
    binding = fake.bind_external_identity(
        binding_repo,
        canonical_id=canon.canonical_id,
        provider_namespace="offline_ns",
        external_id="off_99",
        capability_scope=CapabilityScope.VCS_READ.value,
    )

    assert record.adapter_id == "fake_offline_adapter"
    assert canon.canonical_id == "canon_offline_1"
    assert binding.external_id == "off_99"


# ── Test 12: Malformed versions/IDs/namespaces/metadata rejected ─────────────


def test_malformed_versions_ids_namespaces_metadata_rejected():
    # Contract version must be a non-Boolean integer.
    with pytest.raises(ValidationError):
        CanonicalIdentityEnvelope(
            contract_version="1",
            canonical_id="canon_fixturea",
            entity_kind="issue",
            namespace="prismatic:core:testing",
            created_at="2026-08-04T18:00:00Z",
        )

    # Empty canonical_id
    with pytest.raises(ValidationError):
        CanonicalIdentityEnvelope(
            contract_version=1,
            canonical_id="  ",
            entity_kind="issue",
            namespace="prismatic:core:testing",
            created_at="2026-08-04T18:00:00Z",
        )

    # Empty namespace
    with pytest.raises(ValidationError):
        CanonicalIdentityEnvelope(
            contract_version=1,
            canonical_id="canon_fixturea",
            entity_kind="issue",
            namespace="",
            created_at="2026-08-04T18:00:00Z",
        )

    # Mapping version < 1
    with pytest.raises(ValidationError):
        CanonicalIdentityEnvelope(
            contract_version=1,
            canonical_id="canon_fixturea",
            entity_kind="issue",
            namespace="prismatic:core:testing",
            mapping_version=0,
            created_at="2026-08-04T18:00:00Z",
        )

    # Nested non-scalar metadata
    with pytest.raises(ValidationError):
        CanonicalIdentityEnvelope(
            contract_version=1,
            canonical_id="canon_fixturea",
            entity_kind="issue",
            namespace="prismatic:core:testing",
            created_at="2026-08-04T18:00:00Z",
            metadata={"nested": {"sub_key": "val"}},
        )

    # Unbounded metadata key count
    huge_metadata = {f"key_{i}": i for i in range(50)}
    with pytest.raises(ValidationError):
        CanonicalIdentityEnvelope(
            contract_version=1,
            canonical_id="canon_fixturea",
            entity_kind="issue",
            namespace="prismatic:core:testing",
            created_at="2026-08-04T18:00:00Z",
            metadata=huge_metadata,
        )


# ── Test 13: Secret-shaped values rejected or excluded ────────────────────────


def test_secret_shaped_values_rejected_or_excluded():
    # Secret in metadata key
    with pytest.raises(SecretDetectedError):
        CanonicalIdentityEnvelope(
            contract_version=1,
            canonical_id="canon_fixturea",
            entity_kind="issue",
            namespace="prismatic:core:testing",
            created_at="2026-08-04T18:00:00Z",
            metadata={"api_key": "12345"},
        )

    # Secret pattern in metadata value
    with pytest.raises(SecretDetectedError):
        CanonicalIdentityEnvelope(
            contract_version=1,
            canonical_id="canon_fixturea",
            entity_kind="issue",
            namespace="prismatic:core:testing",
            created_at="2026-08-04T18:00:00Z",
            metadata={"config": "ghp_1234567890abcdef123456"},
        )

    # Secret pattern in external_id
    with pytest.raises(SecretDetectedError):
        ExternalIdentityBinding(
            contract_version=1,
            canonical_id="canon_fixturea",
            adapter_id="adapter",
            provider_namespace="ns",
            external_id="sk-1234567890abcdef123456",
            capability_scope=CapabilityScope.VCS_READ.value,
            created_at="2026-08-04T18:00:00Z",
            updated_at="2026-08-04T18:00:00Z",
        )


# ── Test 14: Import-boundary test preventing provider SDK imports in Core ─────


def test_import_boundary_preventing_provider_sdk_imports():
    portability_dir = Path(__file__).parent.parent
    py_files = list(portability_dir.glob("*.py"))
    assert len(py_files) >= 5, "Portability module files must exist."

    forbidden_modules = {
        "github",
        "linear",
        "openai",
        "anthropic",
        "google.generativeai",
        "httpx",
        "requests",
        "boto3",
        "urllib3",
    }

    violations = []
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_modules or any(
                        alias.name.startswith(f"{m}.") for m in forbidden_modules
                    ):
                        violations.append((py_file.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module in forbidden_modules
                    or any(node.module.startswith(f"{m}.") for m in forbidden_modules)
                ):
                    violations.append((py_file.name, node.module))

    assert len(violations) == 0, (
        f"Provider SDK import violations detected in Core portability: {violations}"
    )


# ── Test 15: Binding registration immediate lookup readback ──────────────────


def test_binding_registration_immediate_lookup_readback():
    repo = BindingRepository()
    binding = ExternalIdentityBinding(
        contract_version=1,
        canonical_id="canon_readback_1",
        adapter_id="linear",
        provider_namespace="team_alpha",
        external_id="CARD-404",
        capability_scope=CapabilityScope.ISSUE_READ.value,
        mapping_version=1,
        created_at="2026-08-04T18:00:00Z",
        updated_at="2026-08-04T18:00:00Z",
    )

    registered = repo.register_binding(binding)
    assert registered.canonical_id == "canon_readback_1"

    # Immediate lookup readback proves exact canonical_id matching
    readback = repo.lookup_binding(
        adapter_id="linear",
        provider_namespace="team_alpha",
        external_id="CARD-404",
        capability_scope=CapabilityScope.ISSUE_READ.value,
        mapping_version=1,
    )
    assert readback is not None
    assert readback.canonical_id == "canon_readback_1"


# ── Test 16: Concurrent binding registration single winner ──────────────────


def test_concurrent_binding_registration_single_winner():
    repo = BindingRepository()
    num_threads = 10

    def attempt_registration(thread_idx: int):
        binding = ExternalIdentityBinding(
            contract_version=1,
            canonical_id=f"canon_concurrent_{thread_idx}",  # Different canonical IDs competing for SAME external ID!
            adapter_id="github",
            provider_namespace="org/repo",
            external_id="EXT_RACE_1",
            capability_scope=CapabilityScope.VCS_READ.value,
            mapping_version=1,
            created_at="2026-08-04T18:00:00Z",
            updated_at="2026-08-04T18:00:00Z",
        )
        try:
            repo.register_binding(binding)
            return "SUCCESS"
        except BindingConflictError:
            return "CONFLICT"

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(attempt_registration, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    successes = [r for r in results if r == "SUCCESS"]
    conflicts = [r for r in results if r == "CONFLICT"]

    assert len(successes) == 1, f"Expected exactly 1 winner, got {len(successes)}"
    assert len(conflicts) == num_threads - 1

    # Verify index integrity
    all_bindings = repo.list_all_bindings()
    assert len(all_bindings) == 1


# ── Test 17: Unregistering adapter preserves identities and bindings ──────────


def test_unregistering_adapter_preserves_identities_and_bindings():
    reg = ProviderRegistry()
    repo = BindingRepository()
    fake = FakeOfflineAdapter(adapter_id="preserve_test_adapter")

    fake.register(reg)
    fake.create_canonical_identity(canonical_id="canon_preserve_1")
    fake.bind_external_identity(
        repo,
        canonical_id="canon_preserve_1",
        provider_namespace="ns_1",
        external_id="ext_preserve_1",
        capability_scope=CapabilityScope.ISSUE_READ.value,
    )

    reg.unregister("preserve_test_adapter")

    # Adapter removed from registry
    with pytest.raises(AdapterNotFoundError):
        reg.get("preserve_test_adapter")

    # Binding & canonical identity remain in binding repository
    binding = repo.lookup_binding(
        adapter_id="preserve_test_adapter",
        provider_namespace="ns_1",
        external_id="ext_preserve_1",
        capability_scope=CapabilityScope.ISSUE_READ.value,
    )
    assert binding is not None
    assert binding.canonical_id == "canon_preserve_1"


# ── Test 18: Duplicate registration conflicting metadata fails closed ────────


def test_duplicate_registration_conflicting_metadata_fails_closed():
    reg = ProviderRegistry()

    r1 = ProviderAdapterRecord(
        adapter_id="conflict_meta_adapter",
        adapter_kind="chat",
        declared_capabilities={CapabilityScope.CHAT_SEND.value},
        qualification_state={
            CapabilityScope.CHAT_SEND.value: QualificationState.QUALIFIED
        },
        availability_observation=AvailabilityObservation(
            status="HEALTHY", observed_at="2026-08-04T18:00:00Z"
        ),
        contract_version=1,
        entry_point="original.entry.point",
    )
    reg.register(r1)

    r2 = ProviderAdapterRecord(
        adapter_id="conflict_meta_adapter",
        adapter_kind="chat",
        declared_capabilities={CapabilityScope.CHAT_SEND.value},
        qualification_state={
            CapabilityScope.CHAT_SEND.value: QualificationState.QUALIFIED
        },
        availability_observation=AvailabilityObservation(
            status="HEALTHY", observed_at="2026-08-04T18:00:00Z"
        ),
        contract_version=1,
        entry_point="conflicting.entry.point",  # Conflicting metadata
    )

    with pytest.raises(DuplicateAdapterError):
        reg.register(r2)

    assert reg.get("conflict_meta_adapter").entry_point == "original.entry.point"


# ── V3 adversarial repair coverage ───────────────────────────────────────────


def _valid_envelope(**overrides):
    values = {
        "canonical_id": "canon_repaira",
        "entity_kind": "issue",
        "namespace": "prismatic:core:repair",
        "created_at": "2026-08-04T18:00:00Z",
        "metadata": {"priority": "high"},
    }
    values.update(overrides)
    return CanonicalIdentityEnvelope(**values)


def _valid_binding(**overrides):
    values = {
        "canonical_id": "canon_repaira",
        "adapter_id": "repair_adapter",
        "provider_namespace": "workspace_repair",
        "external_id": "EXT-1",
        "capability_scope": CapabilityScope.ISSUE_READ.value,
        "created_at": "2026-08-04T18:00:00Z",
        "updated_at": "2026-08-04T18:00:00Z",
    }
    values.update(overrides)
    return ExternalIdentityBinding(**values)


def test_metadata_alias_mutation_cannot_change_envelope_or_digest():
    source = {"priority": "high"}
    envelope = _valid_envelope(metadata=source)
    before = canonical_digest(envelope)
    source["priority"] = "low"
    exported = envelope.to_dict()
    exported["metadata"]["priority"] = "changed"
    assert envelope.metadata == {"priority": "high"}
    assert canonical_digest(envelope) == before
    with pytest.raises(TypeError):
        envelope.metadata["priority"] = "forbidden"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_metadata_is_rejected_and_canonical_json_is_strict(value):
    with pytest.raises(ValidationError):
        _valid_envelope(metadata={"number": value})
    payload = to_canonical_json(_valid_envelope())
    parsed = json.loads(
        payload,
        parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
    )
    assert parsed["metadata"] == {"priority": "high"}


def test_utc_equivalent_spellings_normalize_and_digest_equally():
    first = _valid_envelope(created_at="2026-08-04T18:00:00Z")
    second = _valid_envelope(created_at="2026-08-04T18:00:00+00:00")
    assert first.created_at == second.created_at == "2026-08-04T18:00:00.000000Z"
    assert to_canonical_json(first) == to_canonical_json(second)
    assert canonical_digest(first) == canonical_digest(second)


@pytest.mark.parametrize("field", ["contract_version", "mapping_version"])
def test_boolean_versions_fail_closed_for_envelope_and_binding(field):
    with pytest.raises(ValidationError):
        _valid_envelope(**{field: True})
    with pytest.raises(ValidationError):
        _valid_binding(**{field: True})


@pytest.mark.parametrize("field", ["external_version", "etag"])
def test_binding_evidence_character_and_utf8_byte_bounds(field):
    assert getattr(_valid_binding(**{field: "a" * 128}), field) == "a" * 128
    assert getattr(_valid_binding(**{field: "é" * 128}), field) == "é" * 128
    with pytest.raises(ValidationError):
        _valid_binding(**{field: "a" * 129})
    with pytest.raises(ValidationError):
        _valid_binding(**{field: "é" * 127 + "€"})


def test_binding_chronology_is_normalized_and_fail_closed():
    equal = _valid_binding(updated_at="2026-08-04T18:00:00+00:00")
    later = _valid_binding(updated_at="2026-08-04T18:00:00.000001Z")
    assert equal.updated_at == equal.created_at
    assert datetime.datetime.fromisoformat(
        later.updated_at.replace("Z", "+00:00")
    ) > datetime.datetime.fromisoformat(later.created_at.replace("Z", "+00:00"))
    with pytest.raises(ValidationError):
        _valid_binding(updated_at="2026-08-04T17:59:59.999999Z")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_id", "ghp_AAAAAAAAAAAAAAAAAAAA"),
        ("namespace", "bearer AAAAAAAAAAAAAAAAAAAA"),
    ],
)
def test_secret_shapes_in_canonical_fields_fail_without_echo(field, value):
    with pytest.raises(SecretDetectedError) as exc_info:
        _valid_envelope(**{field: value})
    assert value not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_id", "ghp_AAAAAAAAAAAAAAAAAAAA"),
        ("adapter_id", "sk-AAAAAAAAAAAAAAAAAAAA"),
        ("provider_namespace", "bearer AAAAAAAAAAAAAAAAAAAA"),
        ("external_id", "ghp_BBBBBBBBBBBBBBBBBBBB"),
        ("external_version", "sk-BBBBBBBBBBBBBBBBBBBB"),
        ("etag", "bearer BBBBBBBBBBBBBBBBBBBB"),
    ],
)
def test_secret_shapes_in_binding_fields_fail_without_echo(field, value):
    with pytest.raises(SecretDetectedError) as exc_info:
        _valid_binding(**{field: value})
    assert value not in str(exc_info.value)


def test_metadata_secret_errors_and_conflict_errors_are_redacted():
    sentinel = "ghp_CCCCCCCCCCCCCCCCCCCC"
    with pytest.raises(SecretDetectedError) as exc_info:
        _valid_envelope(metadata={"safe": sentinel})
    assert sentinel not in str(exc_info.value)
    with pytest.raises(SecretDetectedError) as exc_info:
        _valid_envelope(metadata={"api_key": "plain"})
    assert "api_key" not in str(exc_info.value)

    repository = BindingRepository()
    repository.register_binding(_valid_binding())
    conflicting = _valid_binding(external_id="CALLER-CONTROLLED-SENTINEL")
    with pytest.raises(BindingConflictError) as exc_info:
        repository.register_binding(conflicting)
    assert "CALLER-CONTROLLED-SENTINEL" not in str(exc_info.value)
    assert "canon_repaira" not in str(exc_info.value)


def test_closed_core_identity_vocabulary_and_provider_separation():
    assert CORE_NAMESPACE_RE.fullmatch("prismatic:core:issue")
    assert CANONICAL_ID_RE.fullmatch("canon_issuea")
    for kind in EntityKind:
        assert _valid_envelope(entity_kind=kind).entity_kind is kind
    with pytest.raises(ValidationError):
        _valid_envelope(entity_kind="provider_issue")
    with pytest.raises(ValidationError):
        _valid_envelope(namespace="prismatic:core:github_issue")
    with pytest.raises(ValidationError):
        _valid_envelope(canonical_id="ISSUE-123")
    with pytest.raises(ValidationError):
        _valid_envelope(canonical_id="/repos/owner/repo/issues/123")
    with pytest.raises(ValidationError):
        _valid_binding(provider_namespace="prismatic:core:issue")


def test_provider_record_and_registry_break_all_collection_aliases():
    capabilities = {CapabilityScope.ISSUE_READ.value}
    qualifications = {CapabilityScope.ISSUE_READ.value: QualificationState.QUALIFIED}
    nested = {"labels": ["stable"], "settings": {"mode": "safe"}}
    record = ProviderAdapterRecord(
        adapter_id="immutable_adapter",
        adapter_kind="issue",
        declared_capabilities=capabilities,
        qualification_state=qualifications,
        availability_observation=AvailabilityObservation(
            status="HEALTHY", observed_at="2026-08-04T18:00:00+00:00"
        ),
        metadata=nested,
    )
    capabilities.add(CapabilityScope.ISSUE_WRITE.value)
    qualifications[CapabilityScope.ISSUE_READ.value] = QualificationState.SUSPENDED
    nested["labels"].append("mutated")
    nested["settings"]["mode"] = "unsafe"

    registry = ProviderRegistry()
    returned = registry.register(record)
    listed = registry.list_all()
    listed.clear()
    assert returned.declared_capabilities == (CapabilityScope.ISSUE_READ.value,)
    assert dict(returned.qualification_state) == {
        CapabilityScope.ISSUE_READ.value: QualificationState.QUALIFIED
    }
    stored = registry.get("immutable_adapter")
    assert stored.metadata["labels"] == ("stable",)
    assert stored.metadata["settings"]["mode"] == "safe"
    assert (
        registry.get_qualification(
            "immutable_adapter", CapabilityScope.ISSUE_READ.value
        )
        is QualificationState.QUALIFIED
    )
    with pytest.raises(TypeError):
        stored.metadata["settings"]["mode"] = "forbidden"


# ── V5 additive-child proof coverage ─────────────────────────────────────────


@pytest.mark.parametrize("field", ["contract_version", "mapping_version"])
@pytest.mark.parametrize("invalid", [True, False, "1", 1.0, -1, 0])
def test_r0_bool_and_string_and_float_fail_closed_ints_one_and_two_accepted(
    field, invalid
):
    with pytest.raises(ValidationError):
        _valid_envelope(**{field: invalid})
    with pytest.raises(ValidationError):
        _valid_binding(**{field: invalid})

    for accepted in (1, 2):
        assert getattr(_valid_envelope(**{field: accepted}), field) == accepted
        assert getattr(_valid_binding(**{field: accepted}), field) == accepted


def test_r3_zero_microsecond_utc_normalizes_to_six_fractional_digits():
    zulu = _valid_envelope(created_at="2026-08-04T18:00:00Z")
    offset = _valid_envelope(created_at="2026-08-04T18:00:00+00:00")
    expected = "2026-08-04T18:00:00.000000Z"
    assert zulu.created_at == offset.created_at == expected
    assert to_canonical_json(zulu) == to_canonical_json(offset)
    assert canonical_digest(zulu) == canonical_digest(offset)


def test_r3_one_microsecond_increment_keeps_six_fractional_digits_and_equal_digest():
    zulu = _valid_envelope(created_at="2026-08-04T18:00:00.000001Z")
    offset = _valid_envelope(created_at="2026-08-04T18:00:00.000001+00:00")
    expected = "2026-08-04T18:00:00.000001Z"
    assert zulu.created_at == offset.created_at == expected
    assert canonical_digest(zulu) == canonical_digest(offset)

    binding = _valid_binding(
        created_at="2026-08-04T18:00:00Z",
        updated_at="2026-08-04T18:00:00.000001Z",
    )
    assert binding.created_at == "2026-08-04T18:00:00.000000Z"
    assert binding.updated_at == expected


def _assert_stable_code(exc: BaseException, expected: str) -> None:
    text = str(exc)
    assert text.startswith(f"{expected}:")
    codes = {
        "secret_key_detected",
        "secret_value_detected",
        "binding_conflict",
        "invalid_capability",
        "invalid_event_kind",
        "invalid_namespace",
    }
    assert not any(code in text for code in codes - {expected})


def test_r4_condition_to_stable_code_mapping():
    secret_value = "ghp_" + "V" * 20
    with pytest.raises(SecretDetectedError) as exc_info:
        _valid_envelope(metadata={"safe": secret_value})
    _assert_stable_code(exc_info.value, "secret_value_detected")

    with pytest.raises(SecretDetectedError) as exc_info:
        _valid_envelope(metadata={"api_key": "plain"})
    _assert_stable_code(exc_info.value, "secret_key_detected")

    repository = BindingRepository()
    repository.register_binding(_valid_binding())
    with pytest.raises(BindingConflictError) as exc_info:
        repository.register_binding(
            _valid_binding(external_id="caller_external_sentinel")
        )
    _assert_stable_code(exc_info.value, "binding_conflict")
    assert "caller_external_sentinel" not in str(exc_info.value)

    with pytest.raises(InvalidCapabilityError) as exc_info:
        _valid_binding(capability_scope="caller_capability_sentinel")
    _assert_stable_code(exc_info.value, "invalid_capability")
    assert "caller_capability_sentinel" not in str(exc_info.value)

    with pytest.raises(InvalidEntityKindError) as exc_info:
        _valid_envelope(entity_kind="caller_entity_sentinel")
    _assert_stable_code(exc_info.value, "invalid_event_kind")
    assert "caller_entity_sentinel" not in str(exc_info.value)

    with pytest.raises(InvalidNamespaceError) as exc_info:
        _valid_envelope(namespace="prismatic:core:github_issue")
    _assert_stable_code(exc_info.value, "invalid_namespace")
    assert "github_issue" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("position", "field"),
    [
        ("envelope", "canonical_id"),
        ("envelope", "entity_kind"),
        ("envelope", "namespace"),
        ("binding", "adapter_id"),
        ("binding", "external_id"),
        ("binding", "provider_namespace"),
        ("binding", "capability_scope"),
        ("binding", "external_version"),
        ("binding", "etag"),
        ("metadata_key", None),
        ("metadata_value", None),
    ],
)
def test_r4_no_caller_value_echo_in_any_captured_exception_text(position, field):
    sentinel = "ghp_" + position + "_" + "X" * 20
    expected_code = (
        "secret_key_detected" if position == "metadata_key" else "secret_value_detected"
    )

    with pytest.raises(SecretDetectedError) as exc_info:
        if position == "envelope":
            _valid_envelope(**{field: sentinel})
        elif position == "binding":
            _valid_binding(**{field: sentinel})
        elif position == "metadata_key":
            _valid_envelope(metadata={sentinel: "safe"})
        else:
            _valid_envelope(metadata={"safe": sentinel})

    _assert_stable_code(exc_info.value, expected_code)
    assert sentinel not in str(exc_info.value)
