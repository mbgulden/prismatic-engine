# Extending the Prismatic Engine — Plugin Author Guide

This document explains how to write a plugin for the Prismatic Engine.
It covers the plugin manifest format, the Python contract, and the
hook system — including the four PWP pipeline hooks introduced in
GRO-2228.

For the design rationale behind the hook system, see
`specs/implementation-plans/GRO-1497-plugin-interface-plan.md` and
the PWP orchestration section of this document.

---

## 1. Quick start

A Prismatic plugin is a directory under `plugins/` that contains:

```
plugins/my_plugin/
├── plugin-manifest.yaml     # required: declarative metadata
└── plugin.py                # required: a class extending PrismaticPlugin
```

The minimum viable plugin looks like this:

```python
# plugins/my_plugin/plugin.py
from prismatic.interface.plugin import PrismaticPlugin, PluginContext

class MyPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        # Set up any resources this plugin needs.
        pass

    def register_tools(self):
        return []
```

```yaml
# plugins/my_plugin/plugin-manifest.yaml
schema_version: "1.0.0"
name: "my-plugin"
version: "1.0.0"
entry_point: "my_plugin.plugin:MyPlugin"
core_version_constraint: ">=1.0.0, <2.0.0"
hooks:
  - "on_init"
```

The engine discovers the plugin on startup and calls `on_init` once. If
you don't subscribe to a hook, the engine won't call it.

---

## 2. The hook system

The engine fires lifecycle events as **hooks**. A plugin opts in to a
hook by:

1. Declaring the hook name in its manifest's `hooks:` list.
2. Defining a method with the matching name on its `PrismaticPlugin`
   subclass.

All hook dispatches go through `PluginLoader.execute_hook`, which
wraps every call in try-catch isolation. **A crashing plugin can
never abort the dispatcher event loop or a pipeline run.**

### 2.1 Core lifecycle hooks

| Hook                       | When it fires                                    |
| :------------------------- | :----------------------------------------------- |
| `on_init`                  | Once, when the dispatcher starts.                |
| `before_task_execution`    | Before an agent worker is spawned.               |
| `after_task_execution`     | After an agent worker exits.                     |
| `on_state_transition`      | When a Linear ticket changes status.             |

### 2.2 GRO-1497 dispatcher hooks

| Hook                       | When it fires                                    |
| :------------------------- | :----------------------------------------------- |
| `on_issue_dispatch`        | After an issue is dispatched to a provider.      |
| `on_review_complete`       | After a peer-review cycle finishes.              |
| `on_pipeline_stage`        | When a pipeline stage starts / fails / completes.|
| `on_credit_threshold`      | When credit consumption crosses a safety limit.  |

### 2.3 PWP pipeline hooks (GRO-2228)

The PWP (Prismatic Web Plugin) set is the four-hook contract for
multi-stage pipelines such as the web-publishing pipeline. The
`PWPPluginRunner` class in `prismatic.core.registry` orchestrates
these hooks around a list of stage callables.

The four hooks, in the order they fire on a successful run:

```
  on_pre_pipeline  ──►  stages…  ──►  on_post_pipeline  ──►  on_deploy
                              │
                              └─ on_error (replaces on_post_pipeline)
```

| Hook                | Fires                                                  | Times per run |
| :------------------ | :----------------------------------------------------- | :------------ |
| `on_pre_pipeline`   | Once, *before* any stage runs.                         | 1             |
| `on_post_pipeline`  | Once, *after* all stages succeed.                      | 0 or 1        |
| `on_error`          | Once, when any stage raises (re-raises after firing).  | 0 or 1        |
| `on_deploy`         | Once, after `on_post_pipeline` if a deploy was queued. | 0 or 1        |

`on_post_pipeline` and `on_error` are **mutually exclusive** — a
pipeline run fires exactly one of them. `on_deploy` only fires on
success; a failed deploy is treated as best-effort and does NOT
re-trigger `on_error`.

#### Hook signatures

```python
def on_pre_pipeline(
    self, pipeline_id: str, context: Dict[str, Any]
) -> None: ...

def on_post_pipeline(
    self, pipeline_id: str, result: Dict[str, Any]
) -> None: ...

def on_error(
    self, pipeline_id: str, exc: BaseException, stage: str
) -> None: ...

def on_deploy(
    self, pipeline_id: str, target: str, artifact: Dict[str, Any]
) -> None: ...
```

#### Using `PWPPluginRunner` directly

```python
from prismatic.core.registry import PWPPluginRunner

runner = PWPPluginRunner(loader)  # loader is the engine's PluginLoader

def build_site(ctx): return "built"
def run_tests(ctx): return "passed"

result = runner.run(
    pipeline_id="GRO-2228-001",
    context={"issue_id": "GRO-2228", "branch": "main"},
    stages=[
        ("build", build_site),
        ("test",  run_tests),
    ],
    deploy_target="cloudflare-pages",
    deploy_artifact_provider=lambda r: {
        "url": f"https://example.test/build/{r['stages'][0]['output']}",
    },
)
# result == {"status": "succeeded", "stages": [...]}
```

`PWPPluginRunner.run()` re-raises the first exception after firing
`on_error`, so callers can still handle the failure with a normal
`try`/`except`.

---

## 3. Writing a manifest

Every plugin must declare a `plugin-manifest.yaml` (or the legacy
`plugin-manifest.yaml` — both are supported) at its root. The full
schema is documented in §1.1 of
`specs/implementation-plans/GRO-1497-plugin-interface-plan.md`. The
most commonly-overridden fields are:

| Field                      | Required | Notes                                      |
| :------------------------- | :------- | :----------------------------------------- |
| `name`                     | yes      | Unique plugin id.                          |
| `version`                  | yes      | SemVer.                                    |
| `entry_point`              | yes      | `module.path:ClassName`.                   |
| `core_version_constraint`  | yes      | SemVer range of the engine.                |
| `hooks`                    | yes      | List of hook names to subscribe to.        |
| `required_capabilities`    | no       | `["gpu", "git", "network"]` etc.           |
| `provider_constraints`     | no       | Per-provider SemVer or "blocked"/"supported". |
| `personas`                 | no       | List of persona definitions.               |
| `dependencies.pip`         | no       | Pip packages required at load time.        |

The engine validates the manifest on load and skips the plugin (with
an error log) if any check fails.

---

## 4. Testing a plugin

The engine ships a fixture plugin at
`plugins/pwp_hook_test_plugin/` that records every hook invocation on
a class-level list. The accompanying test suite
`tests/test_pwp_hooks.py` shows how to:

* Copy the fixture into a temp directory.
* Build a `PluginLoader` pointed at it.
* Run a `PWPPluginRunner` and assert hook ordering.

Use the same pattern in your own plugin's tests. The hook isolation
guarantee means you can crash a hook handler freely in tests without
breaking the runner.
