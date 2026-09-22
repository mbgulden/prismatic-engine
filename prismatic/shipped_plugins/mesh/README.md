# prismatic-mesh

Mesh capability plugin for the Prismatic Engine — **guest addressing +
presence as infrastructure**.

Guests (harness-side agents, workers, edge nodes) announce themselves and
heartbeat their presence; the plugin keeps the peer table, persists it
across disable/enable, and exposes it through its own API routes.
Presence changes emit audit events.

Addressing and presence are **infrastructure, not an agent tool**: the
plugin registers no agent tools and exposes status only through
plugin-registered routes. It never edits `prismatic/gateway/server.py` or
any other core file.

> **Disabled by default.** `auto_enable: false` in the manifest: the plugin
> registers on attach but starts DISABLED. An operator must explicitly
> enable it (`PluginLoader.enable("prismatic-mesh")`) before any peer can
> announce.

## Attach

Drop (or symlink) this directory on the plugin discovery path as `mesh/`
(the directory name must match the first segment of `entry_point`:
`mesh.plugin:MeshPlugin`), then attach:

```
prismatic plugins attach ./prismatic/shipped_plugins/mesh --config ./mesh-config.yaml
prismatic plugins enable prismatic-mesh
```

`swarmmesh>=0.1.0` is declared in `dependencies.pip`. The primitive owns
*address syntax*: announce-time addresses are normalized through
`swarmmesh.addressing.normalize_address`, and announcing while the
primitive is missing fails with a clear `RuntimeError` (a use-time
dependency, not a load-time one — loader scans never crash on base
installs).

## Config syntax

```yaml
# mesh-config.yaml
max_peers: 1000          # peer-table capacity; new peers rejected when full
heartbeat_ttl_sec: 0     # >0: peers listed as `stale` this many seconds
                         # after last-seen (evaluated at read time; 0 = off)
default_trust_tier: untrusted   # untrusted | trusted; applied when an
                                # announce carries no tier
```

Trust tiers are stated plainly: `untrusted` makes no claims about the
guest; `trusted` means the operator attested the guest out of band.

## Operator API

Plugin-registered routes (declared in `register_api_routes()`, no core
edits):

- `GET /api/mesh/peers` → `[{peer_id, address, trust_tier, first_seen, last_seen, announce_count, stale}]`
- `POST /api/mesh/announce` — body `{peer_id, address, trust_tier?}` → `{peer, registered}`; re-announcing the same `peer_id` is a heartbeat (bumps `last_seen`/`announce_count`, emits `peer_heartbeat`).
- `DELETE /api/mesh/peers/{id}` → `{peer_id, removed}`

Audit events: `peer_registered`, `peer_heartbeat`, `peer_deregistered`,
`suspend`, `resume` (declared in the manifest; presence events also kept
in a bounded in-memory log exposed via `MeshPlugin.presence_events()`).

## State & lifecycle

- State root: `$PRISMATIC_HOME/plugin-state/prismatic-mesh/`
  (`Path(os.environ.get("PRISMATIC_HOME") or Path.home())`).
  - `state.json` — suspend snapshot (`{"peers": [...], "presence_events_tail": [...]}`), written belt-and-braces by `on_suspend()` alongside the loader's own persistence; re-read by `on_init()` if no suspend snapshot flows through.
- `on_suspend()` returns the JSON-serializable snapshot (peer ids,
  addresses, timestamps only — never secrets); `on_resume(state)`
  restores it.
- Event-driven: heartbeats/announcements are the event source. **No
  polling loop, no background threads.** Staleness (`heartbeat_ttl_sec`)
  is computed at read time from `last_seen`.

## Boundary

Per `docs/infrastructure-capabilities.md`: this plugin records **who is
present** — never *what anyone should do next*. It does not route
workloads. It does not decide placement. It does not sequence agent
steps. It does not choreograph anything. Classification check:

1. Does it decide what an agent does next (pick tools, sequence steps, hold conversation state, call an LLM)? **No.**
2. Must every engine install have it for the transaction path to work? **No** — the ledger, locking, and policy gate work fine without it.
3. → **Capability plugin.** Disabled by default, operator-enabled, state preserved across disable/enable.

## Tests

```
python3 -m pytest tests/test_plugin_mesh.py -q
```

Covers: manifest parses with schema 1.0.0 fields, `auto_enable:
false`, tags, importable entry point; `config_schema` accepts a valid
config and rejects bad ones; `on_init` valid/invalid configs (invalid
raises `PluginValidationError`); announce happy path (register +
heartbeat) and error paths; suspend/resume round-trip preserves the peer
table; disable→enable through the real loader preserves state; loader
scan registers-but-disables the plugin; missing-primitive degradation
(clear `RuntimeError` at announce time, loader unaffected); the boundary
test (no workload-routing / placement / choreography surface anywhere in
the plugin or its routes); `register_tools() == []`.
