# Verification Glossary

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-07-24

- **Agent evidence:** output produced by an agent, including self-review; untrusted until independently reproduced.
- **Canonical suite:** the repository-defined test/check command required by verification policy; distinct from an ad-hoc targeted run and independent of which approved backend executes it.
- **Exact artifact:** immutable content identified by full commit or cryptographic digest.
- **Guide:** executable context, constraints, invariants, allowed scope, and evidence requirements supplied before generation.
- **Merge-complete:** reviewed exact SHA, valid exact-head clean-room receipt from an approved backend, merge recorded, and post-merge verification complete.
- **OKF:** Objective → Key Result → Function → Evidence.
- **Proof class:** ad-hoc targeted, canonical clean-room policy execution, installed artifact, production/API, browser/rendered, media/semantic, or business-outcome proof.
- **Source adapter:** provider/local/bundle transport that binds source identity and retrieval; it is not verification authority by itself.
- **Verifier backend:** approved independent execution environment that runs policy in a clean room and emits a receipt for validation.
- **Read model:** dashboard/API/chat presentation derived from durable systems of record.
- **Solve:** bounded remediation that adds regression evidence and updates knowledge rather than hiding a signal.
- **Source of truth:** the highest-precedence durable authority for a specific claim.
- **Systemic verification:** verification of orchestration transitions, leases, retries, evidence, runtime, recovery, and drift—not only a task’s final output.
- **Verify:** independent, diverse checks bound to exact inputs and artifacts.
- **Verifier independence:** separation of producer and acceptance authority plus diversity of failure modes; two prompts to one model are not assumed independent.
