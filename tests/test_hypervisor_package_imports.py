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

    assert set(hv.__all__) == {
        "PrismaticHypervisor",
        "HypervisorTransactionContext",
        "HypervisorLedger",
        "LedgerEntry",
        "get_hypervisor_ledger",
    }
