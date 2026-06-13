# Dynamic Import for Graceful Pre-Conversion Build Scripts

## Problem

A build script (e.g., `build.js`) imports a heavy dependency like `esbuild` at the top level:

```js
import * as esbuild from 'esbuild';
import { existsSync } from 'fs';

const entryPoint = 'js/main.js';

if (!existsSync(entryPoint)) {
  console.log('Pre-conversion state — nothing to build.');
  process.exit(0);
}

// ... use esbuild ...
```

In the **pre-conversion state**, `esbuild` isn't installed (`npm install` hasn't been run yet), so the top-level `import` crashes immediately — before the `existsSync` check ever runs. The graceful exit code is unreachable.

## Fix: Defer the Import

Move the heavy import to a dynamic `await import()` AFTER the pre-condition check:

```js
import { existsSync } from 'fs';  // stdlib imports stay at top level

const entryPoint = 'js/main.js';

if (!existsSync(entryPoint)) {
  console.log('⚠️  js/main.js not found — ES module conversion not yet complete.');
  console.log('   Run `npm run dev` and serve files directly.');
  process.exit(0);
}

// Only load heavy deps after confirming we need them
const esbuild = await import('esbuild');

// ... use esbuild.default.build(...) or esbuild.build(...) ...
```

**Key change:** `import * as X from 'Y'` → `const X = await import('Y')`. Access members as `X.default` or `X.memberName` (dynamic imports return a module namespace object — default exports are at `.default`).

## Requirements

- `package.json` must have `"type": "module"` for top-level `await` to work
- Node.js ≥ 14.8 (top-level `await` in ESM)

## Verification

```bash
# In pre-conversion state (no esbuild installed, no js/main.js):
node build.js
# Expected: "⚠️  js/main.js not found..." + exit 0

# In post-conversion state (esbuild installed, js/main.js exists):
npm install && node build.js
# Expected: "✅ Built dist/game.js (XX KB, dev)"
```

## Real Example (Jun 2026)

GRO-1064 — `darius-star/build.js`:

```diff
-import * as esbuild from 'esbuild';
 import { readFileSync, existsSync } from 'fs';
 
 const entryPoint = 'js/main.js';
 if (!existsSync(entryPoint)) {
   console.log('⚠️  js/main.js not found...');
   process.exit(0);
 }
 
+// Only load esbuild after confirming we're in the post-conversion state
+const esbuild = await import('esbuild');
```

The original crashed with `ERR_MODULE_NOT_FOUND` because esbuild wasn't installed, despite the `existsSync` check being written to handle precisely that case. The fix makes the check actually reachable. Commit `84f84a3`.
