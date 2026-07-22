# Runtime services source contract

`config/runtime-services.json` is the versioned, machine-readable **desired source
contract** for the bounded runtime inventory. It is not a snapshot of systemd,
processes, an active checkout, or any host's current deployment. The current
runtime topology is split: most listed components are owned by Prismatic Engine,
while `merge-daemon` is independently owned and versioned. A passing validator
proves only that the source document obeys this contract; it does not prove that
a running service uses any listed path.

## Inventory and ownership

The schema contains exactly these stable component IDs:

- `gateway`
- `consumer`
- `curator`
- `supervisor`
- `watchdog`
- `webhook-drain`
- `merge-daemon`

The first six bind to an immutable Engine release template below
`/home/ubuntu/.prismatic/releases/{release_id}`. The placeholder is resolved by
a separately authorized release procedure; a current commit or mutable `active`
link is deliberately not embedded in permanent source. Executables, source,
working directories, and import roots must resolve inside that same release.
Where Python is used, the virtual environment is release-specific too.

`merge-daemon` has a separate owner, project, release tree, and version lifecycle.
It is explicitly excluded from Engine release convergence. Its presence in the
inventory must never be read as a claim that it runs the same Engine revision.
The desired separate source contract uses `prismatic_merge.cli` and the known
`prismatic_merge/cli.py` package path; this still does not prove the current
non-git live daemon has been packaged or repointed.

Entrypoint declarations are internally consistent: a dotted Python module must
map to the corresponding source path, while a non-Python absolute entrypoint
(such as `scripts/watchdog.sh`) must equal its source path. The consumer contract
names `prismatic.gateway.event_handlers.dispatch_consumer_v3`, matching the
source-owned event consumer rather than substituting a different dispatcher.
These are desired release bindings, not assertions about current process CWDs.

## Immutable code and mutable state

Code and dependency paths are immutable release inputs. Mutable state remains
external and is declared only in `state_paths`; environment-file references are
declared only in `environment_files`. Environment entries are absolute `.env`
filenames below `/home/ubuntu/.prismatic/env.d/`. The manifest contains filenames,
not assignments or values. State roots needed by this inventory include managed
paths below `/home/ubuntu/.prismatic/`, active sandbox storage at
`/archive/agy_sandboxes`, and archival sandbox storage under the NAS mount. Those
state and archive roots are never valid executable or import roots.

The validator rejects executable, source, working-directory, and import/PYTHONPATH
dependencies under the mutable work checkout, `.prismatic/runtime`, or Hermes
profile trees. It also rejects state inside release or execution trees, traversal,
inline environment assignments, unexpected fields, and credential/private-key
markers. Direct helper validation also rejects mapping/string subclasses and custom
mapping keys before invoking their iteration, hashing, equality, or comparison
hooks. Validation uses only the Python standard library:

```bash
python3 scripts/validate_runtime_services.py
python3 scripts/validate_runtime_services.py /path/to/candidate.json
```

Success and failure are one-line, deterministic messages; failure exits nonzero.
The optional path is for reviewing a candidate or a temporary test fixture. The
validator does not inspect services, read referenced environment files, open
state databases, or mutate the host.

## Preservation and porting workflow

A dirty runtime checkout and assets in a Hermes profile may contain unique work.
They are preservation and path-by-path port sources, **not** reset targets and not
directories to copy wholesale into a release. Preserve them before analysis.
Inventory each required file, compare it with source at an exact reviewed Git
head, and port only the understood change into a source-owned path. Reviewers
must record the exact source head and release identifier rather than relying on a
moving branch, editable install, or `active` symlink.

Convergence is a staged, separately authorized operation:

1. Back up mutable state and every preservation source without exposing
   environment-file contents.
2. Build from an exact reviewed head into a new immutable release directory and
   a release-specific virtual environment.
3. Prove imports, migrations, and service behavior offline or on an alternate
   port. Keep this proof isolated from production state where possible.
4. Compare every component path against the manifest and explicitly account for
   intentionally separate components such as `merge-daemon`.
5. Obtain separate authorization before repointing a unit/symlink or restarting
   any process. Source review alone grants no operational authority.
6. Define rollback before repointing: retain the previous immutable release and
   state backup, identify incompatible state changes, and specify the exact
   repoint/restart sequence needed to restore the prior release.

Backups do not authorize deployment, and an alternate-port proof does not prove
production parity. Any state migration, unit edit, daemon reload, repoint, or
restart belongs to a different change with its own evidence and rollback review.

## Explicit non-claims

This manifest and its tests do **not** claim:

- live runtime parity or convergence;
- deployment, repointing, daemon reload, or restart;
- clean-room portability or reproducibility;
- package/release publishability;
- that preserved dirty-checkout or Hermes assets are safe to delete;
- that `merge-daemon` is converged with Prismatic Engine.

Those claims require independent evidence from separately authorized work. No
secret contents, live process state, or machine-generated deployment state belong
in this document or the manifest.
