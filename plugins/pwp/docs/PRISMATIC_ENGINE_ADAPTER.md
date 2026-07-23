# Prismatic Engine adapter boundary

## Scope

PWP domain behavior is portable. The Prismatic Engine (PE) integration is an
optional adapter, not a dependency of the theme, compiler, validator, diff, or
credential-provider domain modules. This document records the contract for the
separate-repository extraction; it does **not** authorize PE Core changes,
monorepo removal, deployment, or a production cutover.

## Owned layers

| Layer | Code | Dependency rule |
| --- | --- | --- |
| PWP domain | `domain.py`, compiler, theme validation/diff, OAuth provider helpers | Must not import `prismatic.*`; independently package and test it. |
| PE adapter | `prismatic_adapter.py` | The only PWP module permitted to import the PE plugin protocol. |
| PE Core consumers | `prismatic.interface.plugin`, `prismatic.capability_router`, loader/runtime | Remain PE-owned and are never copied into the PWP repository. |

`plugin.py` remains a compatibility re-export for the existing monorepo loader.
A standalone PWP package should expose its domain API directly and make the PE
adapter an optional extra.

## Versioned interface contract

The adapter targets this supported contract:

```text
prismatic.interface.plugin >=0.2.0,<2.0.0
```

It requires only:

- `PrismaticPlugin` with `on_init(context)` and `register_tools()` lifecycle
  hooks;
- `PluginContext` supplied by PE at load time;
- PWP capability metadata returned as plain JSON-compatible dictionaries.

`prismatic.capability_router` is explicitly a **PE-owned consumer** of that
metadata. PWP does not import it, call it, or copy any of its implementation.
That keeps routing policy under PE's control and prevents a standalone PWP wheel
from acquiring a sibling-checkout dependency.

## Later integration sequence

1. Build and publish an immutable PWP artifact containing the domain package and
   package resources. The PE adapter may be distributed as an optional extra.
2. PE installs a pinned version (or explicitly configured external plugin
   directory) and loads the adapter through its existing plugin loader.
3. Run read-only compatibility, rollback, dashboard/API, packaging, and
   production proofs in a separate reviewed task before any monorepo removal.

A mutable developer checkout and a Git submodule are not supported production
contracts.

## Minimal follow-up Core contract, if standalone distribution needs it

PE currently exposes the protocol from the monorepo namespace. To make the
adapter independently installable without importing PE Core implementation, a
future Core-owned slice should publish a small, versioned plugin-API
artifact containing only `PluginContext`, `PrismaticPlugin`, `AgentContract`,
and compatibility tests. It must not move or vendor `capability_router`, the
loader, dispatcher, runtime state, or production services. Until then, the
standalone package remains domain-only by default and the adapter is installed
only alongside a compatible PE runtime.

## Boundary proof

`plugins/pwp/tests/test_prismatic_adapter_boundary.py` asserts that the domain
layer has no `prismatic.*` imports and that the adapter declares both the plugin
protocol and the PE capability-router consumer. This is a focused source-level
boundary proof, not a claim that PE has cut over to an extracted repository.
