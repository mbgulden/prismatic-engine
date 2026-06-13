# Hermes Dashboard Plugin — Build & Deploy Workflow

When modifying a Hermes dashboard plugin (e.g., `hermes-plugin-swarm-manager`), follow this workflow:

## 1. Install Dependencies
```bash
cd ${PRISMATIC_HOME}/work/prismatic-engine/plugins/hermes-plugin-<name>
npm install
```
Plugins typically depend on `webpack`, `webpack-cli`, `babel-loader`, and `@babel/preset-react`. First install may take 5-10 seconds.

## 2. Build
```bash
npm run build
```
This runs `webpack --mode production` and outputs to `dashboard/dist/index.js`. Watch for:
- **Babel parse errors** (SyntaxError): check JSX nesting, missing closing parens in `h()` calls
- **Success with warnings only** (e.g., asset size > 244 KiB): these are performance warnings, not errors — the build succeeded

## 3. Deploy to Active Profile
```bash
# Copy the built bundle
cp dashboard/dist/index.js ${PRISMATIC_HOME}/.hermes/profiles/orchestrator/plugins/hermes-plugin-<name>/dashboard/dist/index.js

# Copy the API module (if created/updated)
cp dashboard/plugin_api.py ${PRISMATIC_HOME}/.hermes/profiles/orchestrator/plugins/hermes-plugin-<name>/dashboard/plugin_api.py
```

## 4. Plugin API File Pattern
Dashboard plugins can expose FastAPI routes via `plugin_api.py`:

```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/locks")
async def get_locks():
    return {"locks": [...]}
```

The dashboard mounts these automatically. The frontend calls them via the plugin SDK's `api()` function:
```javascript
const api = sdk.fetchJSON || sdk.api;
api('/api/dashboard/locks').then(data => ...)
```

## 5. Manifest Requirements
`dashboard/manifest.json` must include `"api": "plugin_api.py"` if the plugin has API routes:
```json
{
  "name": "hermes-plugin-swarm-manager",
  "entry": "dist/index.js",
  "api": "plugin_api.py",
  "slots": ["global-injector"]
}
```

## Pitfalls
- **node_modules must NOT be committed.** After `npm install`, only stage source files + dist + manifest — not the entire node_modules tree.
- **The orchestrator profile is at** `$PRISMATIC_HOME/.hermes/profiles/orchestrator/` — deploy plugins here for them to be active.
- **Plugin source lives in** `$PRISMATIC_HOME/work/prismatic-engine/plugins/` — modify source here, build, then copy to orchestrator.
