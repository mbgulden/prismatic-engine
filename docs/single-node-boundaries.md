# Single-Node Operational Boundaries

Prismatic Engine is a **single-node system** (direction B: infrastructure for
real business workloads, not a distributed platform). The boundaries below
are deliberate, documented, and enforced by loud warnings — never silent
assumptions. Each one names what is true today, what the operational rule
is, and what would have to change to lift it.

## 1. Locks are per-instance, in-memory

`PrismaticHypervisor` defaults to an in-memory `HierarchyLockEngine`
(`swarmlock`). Two engine instances — or two processes — **do not share
lock state**. A lock acquired in one process is invisible to another.

The gateway lease mirror (`mirror_to_gateway=True`) is best-effort
**observability**: it copies lease events to the shared file registry so the
dashboard can show them. It never gates correctness — a denied or failed
mirror does not block the transaction, and a mirrored lease is not an
enforcement point.

**Operational rule:** run exactly one engine instance per journal/ledger
path set. If you need a second instance, give it its own paths (or pass a
shared `lock_engine` you operate yourself).

**Loudness:** the constructor logs a `WARNING` the first time the default
in-memory engine is used in a process.

**To lift:** a shared lock backend (the swarmlock daemon/Redis paths exist
but are not wired into the kernel). Not on the roadmap for v1.

## 2. Fencing tokens are process-local

The kernel mints fence tokens with `time.monotonic_ns()`: strictly
monotonic *within a process* (immune to NTP adjustments), but reset on
restart. There is **no shared allocator and no sink-side stale-token
rejection** across processes — a token from a previous process lifetime is
meaningless to a new one.

**Operational rule:** fence tokens order events within one engine lifetime.
Do not treat them as globally unique or persistent identifiers.

**To lift:** a shared token allocator plus sink-side rejection of stale
tokens (compare-and-reject on write). Roadmap, not v1. Note that swarmlock
already ships `DurableFencingTokenGenerator` — restart-durable, still
single-node — which is the natural first step if this boundary ever needs
to move. (Separately, the removed `prismatic/cron_authority.py` ran its own
fencing with SQL-level stale-token rejection; that subsystem was independent
of the kernel path described here. See ADR-0004.)

## 3. Commit semantics (ratified 2026-09-19)

The journal and the ledger are separate stores, so a cross-store commit
cannot be atomic. The ratified semantic, matching standard
write-ahead-logging practice:

- **`JournalEngine.finalize_saga(tx_id, "COMMITTED")` is the commit
  point.** Once it returns, the transaction is committed and must **not**
  be compensated.
- The version bump and the ledger `COMMIT` node *seal* the commit. If
  sealing fails after the commit point, the engine records a
  `COMMIT_SEAL_FAILED` abort node (never a compensation) and raises, so
  the failure is visible.
- **Reconciliation:** a `COMMIT_SEAL_FAILED` node means "committed in the
  journal, seal incomplete in the ledger." The operator replays the seal:
  re-run the version bump and append the missing `COMMIT` node for the
  recorded `tx_id`, then verify the journal still reads `COMMITTED`.
  Forward effects were already applied and must not be run twice.

This is a deliberate trade-off: the journal (the source of truth for
*what happened*) is favored over the ledger (the proof of it). A missing
seal is recoverable; a wrongly-compensated commit is not.
