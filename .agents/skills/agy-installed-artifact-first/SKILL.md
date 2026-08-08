---
name: agy-installed-artifact-first
description: "Tier 2 Packaging Skill: Enforces building python wheel packages from an immutable archive, installing in clean isolated venvs, clearing PYTHONPATH, and proving imports and app startup succeed outside the source tree."
tags: [packaging, wheel, venv, distribution, tier2]
related_skills:
  - agy-runtime-contract-closure
  - prismatic-full-feature-delivery-gate
---

# agy-installed-artifact-first

## Purpose

Prevent distribution failures where imports succeed during local source-tree development (`PYTHONPATH=.`) but fail when deployed as an installed package. Guarantee that all required dependencies and sub-packages are correctly declared, packaged, and runnable from an isolated virtual environment outside the source tree.

---

## Trigger

Loaded on-demand when modifying dependencies, package imports, entry points, `pyproject.toml`, `setup.py`, `MANIFEST.in`, or adding new sub-packages.

---

## Product Outcome

The built wheel package installs cleanly in a uniquely named isolated virtual environment with zero source-checkout fallback, and the canonical application starts, enumerates exact route surfaces, and handles representative HTTP/WS requests from an unrelated empty working directory.

---

## Workflow Protocol

1. **Immutable Wheel Build**: Build wheel from clean source tree or immutable archive into an isolated output directory:
   ```bash
   python -m build --wheel --outdir /tmp/build_out_$(date +%s)
   ```
2. **Single Wheel & Digest Audit**: Assert exactly ONE `.whl` package is built, compute its SHA-256 digest, and inspect zip contents:
   ```python
   wheels = glob.glob("/tmp/build_out_*/ *.whl")
   assert len(wheels) == 1, f"Expected 1 wheel, found {len(wheels)}"
   wheel_sha256 = hashlib.sha256(Path(wheels[0]).read_bytes()).hexdigest()
   ```
3. **Dynamic Clean Venv Creation**: Create a temporary virtual environment using a unique dynamic directory (`mktemp -d` / `tempfile.TemporaryDirectory`):
   ```bash
   VENV_DIR=$(mktemp -d /tmp/clean_venv_XXXXXX)
   python -m venv "$VENV_DIR"
   ```
4. **Force-Install Wheel**: Install the exact wheel without cache:
   ```bash
   "$VENV_DIR/bin/pip" install --no-cache-dir "$WHEEL_PATH"
   ```
5. **Isolated Context Execution**: Change working directory to an isolated empty temporary directory (`mktemp -d /tmp/empty_work_XXXXXX`) and clear `PYTHONPATH`:
   ```bash
   EMPTY_DIR=$(mktemp -d /tmp/empty_work_XXXXXX)
   cd "$EMPTY_DIR" && env -u PYTHONPATH "$VENV_DIR/bin/python" -c "..."
   ```
6. **Import Location Assertion**: Assert imported modules resolve directly from the virtual environment's `site-packages` directory, not local source paths:
   ```python
   import prismatic
   assert "site-packages" in prismatic.__file__, f"Imported from source tree: {prismatic.__file__}"
   ```
7. **Route Surface Enumeration & Request Execution**: Instantiate canonical `app`, enumerate exact method/path pairs, and execute representative requests:
   ```python
   from prismatic.gateway.server import app
   routes = {(r.methods, r.path) for r in app.routes if hasattr(r, 'methods')}
   assert any("/api/review-factory" in path for _, path in routes)
   assert any("/api/workspace" in path for _, path in routes)
   assert any("/api/deploy" in path for _, path in routes)
   assert any("/ws" in path for _, path in routes)

   # Execute request via FastAPI TestClient
   from fastapi.testclient import TestClient
   client = TestClient(app)
   resp = client.get("/api/review-factory/healthz")
   assert resp.status_code == 200
   ```

---

## Anti-Stub Gate

Block completion if:
- Wheel test imports succeed only when `PYTHONPATH` points to the repository root.
- A missing sub-package dependency is resolved by adding a local `sys.path` hack instead of fixing package metadata.
- Route checks rely solely on static route count (`assert len(routes) > 0`) without method/path enumeration and request execution.

---

## Standardized 11-Field Proof Packet

```text
COMMAND=<exact wheel build and venv test command>
RESULT=<PASS|FAIL|BLOCKED>
LOG=<absolute path to log>
SCOPE=agy-installed-artifact-first
AD_HOC_OR_CANONICAL=<ad-hoc targeted|canonical suite>
NOT_CLAIMING=<explicit non-claims>
HEAD=<exact 40-char commit sha>
TREE=<exact 40-char tree sha>
ARTIFACT_SHA256=<wheel file sha256 digest>
ACTIVATION_TRACE=<which tiers loaded and why>
MARKER=AGY_INSTALLED_ARTIFACT_OK
```
