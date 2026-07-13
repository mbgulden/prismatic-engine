# Task GRO-3047: Create prismatic/interface/ui.py + 4 UI hooks + manifest modes/ui validation

We have completed the implementation of the UI system interface, hooks, and manifest validation according to the `opus-plugin-3-ui-ux-surface.md` specification. All acceptance criteria are successfully met.

---

## 1. Summary of Changes

### A. Created UI Handle and Event Bus Structure
We created the new file [ui.py](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/ui.py) defining:
* **`NoUIHostError`**: Exception raised when input is requested but no UI host is attached to handle it.
* **`UIHandle`**: Base interaction handle defining interface methods `emit` and `request_input`.
* **`NullUIHandle`**: Default no-op handle used in headless modes.
* **`UIBusHandle`**: Interactive handle used when a UI host is attached. It publishes events to the bus and blocks synchronous stages using `threading.Event` until user feedback is received.
* **`UIBus`**: Thread-safe event queue wrapper facilitating communication between executing plugins and the UI host.
* **`UIBusEvent`**: Dataclass representing a serialized event sent over the `UIBus`.

### B. Defined 4 New Hook Constants
We updated [hooks.py](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/hooks.py) to declare the 4 new constants, append them to `HOOK_NAMES`, and add type stubs:
* **`HOOK_REGISTER_UI_SURFACES`** (`"register_ui_surfaces"`)
* **`HOOK_ON_HUMAN_INPUT_RECEIVED`** (`"on_human_input_received"`)
* **`HOOK_ON_UI_EVENT`** (`"on_ui_event"`)
* **`HOOK_REGISTER_COMMANDS`** (`"register_commands"`)

### C. Added UI hooks to `PrismaticPlugin` and `PluginContext`
We updated [plugin.py](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/plugin.py) to:
* Set `ui` inside `PluginContext` (defaulting to `NullUIHandle` for safety and backward compatibility).
* Define the 4 optional UI hooks as no-ops on the `PrismaticPlugin` abstract base class.

### D. Upgraded Manifest Validation for v1.1.0
We updated [manifest_schema.py](file:///home/ubuntu/work/prismatic-engine/prismatic/interface/manifest_schema.py) to:
* Validate the `modes` field, accepting either a string (`"headless"`, `"interactive"`, `"both"`) or a list of strings (`["headless", "interactive"]`). It rejects any unknown modes correctly.
* Validate the `ui` field block, permitting rich types (lists of dicts/strings) for `surfaces` and `interrupt_points`.
* Keep the `modes` field optional, meaning old 1.0.0 manifests continue to load seamlessly (backward compatibility).

---

## 2. Testing and Verification

### A. Created Test Suite
We wrote a comprehensive unit test suite in [test_ui_surface.py](file:///home/ubuntu/work/prismatic-engine/tests/test_ui_surface.py) that asserts:
* Correct behavior of `NullUIHandle` (emit is a no-op, request_input raises `NoUIHostError`).
* Correct behavior of `UIBusHandle` (emit puts events into the `UIBus`, request_input publishes the request, blocks, and receives the response successfully via thread synchronization).
* Timeout handling and default values for `UIBusHandle.request_input`.
* Manifest validation checks for list values of `modes`, validation of rich `ui` elements, and rejection of unknown mode values.

### B. Test Execution Results
All test suites passed successfully:

```text
$ pytest tests/test_ui_surface.py
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.0, pluggy-1.6.0
rootdir: /home/ubuntu/work/prismatic-engine
configfile: pyproject.toml
plugins: anyio-4.13.0
collecting ... collected 7 items

tests/test_ui_surface.py .......                                         [100%]

============================== 7 passed in 2.38s ===============================
```

Additionally, the existing manifest schema tests in [test_manifest_schema.py](file:///home/ubuntu/work/prismatic-engine/tests/test_manifest_schema.py) all pass:

```text
$ pytest tests/test_manifest_schema.py
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.0, pluggy-1.6.0
rootdir: /home/ubuntu/work/prismatic-engine
configfile: pyproject.toml
plugins: anyio-4.13.0
collecting ... collected 17 items

tests/test_manifest_schema.py .................                          [100%]

============================== 17 passed in 0.22s ==============================
```
