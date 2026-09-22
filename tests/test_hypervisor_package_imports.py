"""The hypervisor package must export its real classes, never silent Nones.

A fail-open ``try/except`` around the kernel import once hid missing
dependencies by exporting ``PrismaticHypervisor = None`` ("don't trust,
verify": an import failure must surface, not masquerade as a working API).
"""


def test_hypervisor_package_exports_real_classes():
    import prismatic.hypervisor as hv

    assert isinstance(hv.PrismaticHypervisor, type)
    assert isinstance(hv.HypervisorTransactionContext, type)
    assert isinstance(hv.HypervisorLedger, type)
    assert isinstance(hv.LedgerEntry, type)
    assert callable(hv.get_hypervisor_ledger)

    # Containment Phase A: guest spawn path exports (additive).
    assert isinstance(hv.GuestManager, type)
    assert isinstance(hv.GuestHandle, type)
    assert isinstance(hv.GuestSpec, type)
    assert isinstance(hv.GuestResult, type)
    assert isinstance(hv.GuestSpecError, type)
    assert isinstance(hv.TrustTier, type)
    assert callable(hv.scrub_environment)
    assert callable(hv.build_guest_env)
    assert callable(hv.redact_secrets)

    assert set(hv.__all__) == {
        "PrismaticHypervisor",
        "HypervisorTransactionContext",
        "HypervisorLedger",
        "LedgerEntry",
        "get_hypervisor_ledger",
        "TrustTier",
        "GuestSpec",
        "GuestSpecError",
        "GuestResult",
        "GuestHandle",
        "GuestManager",
        "scrub_environment",
        "build_guest_env",
        "redact_secrets",
    }
