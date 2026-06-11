# AGY Asset Generation Pipeline — Parallel Subagent Pattern

Proven June 11, 2026 on darius-star. AGY can generate game assets (sprites, portraits, background strips) 
via `delegate_task` subagents running AGY in `--print` mode.

## When to Use
- AGY needs to generate multiple image assets (sprites, portraits, background layers)
- Assets are independent — no shared state between generations
- Each asset has a clear spec (dimensions, style, palette, reference files)

## Pattern

### 1. Create Linear Issues
One issue per asset batch. AGY posts Implementation Plan → Summary → Walkthrough comments, 
then re-labels to `agent:fred`. Use priority to signal urgency.

### 2. Launch via delegate_task (parallel)
```python
delegate_task(tasks=[
    {"goal": "Launch AGY for GRO-XXXX: generate <asset>. Run: agy --print '/goal ...' 
              --dangerously-skip-permissions --print-timeout 12m --add-dir <repo> 2>&1 | tail -25",
     "toolsets": ["terminal", "file"]},
    # Up to 3 parallel subagents
])
```

### 3. Chunk Size Limits
- **1-3 images per AGY session**: reliable, finishes in 2-5 minutes
- **4-6 images**: works with 12m timeout, may push 8-10 minutes
- **10+ images**: WILL timeout at 600s — break into 3-4 image chunks

### 4. Verification
After subagents complete:
```bash
ls -la <output_dir>/  # Verify all files on disk
python3 -c "from PIL import Image; ..."  # Verify dimensions + format
```

### 5. Close Issues
Once all assets verified: swap label to `agent:done` on Linear.

## Example: darius-star asset generation (June 11, 2026)

| Task | Chunk Size | Result |
|------|-----------|--------|
| GRO-1164 (boss minion) | 1 image | ✅ 2.5 min |
| GRO-1165 (portraits) | 11 images | ❌ timeout at 600s first attempt; ✅ split into 3-image chunk |
| GRO-1166 (backgrounds) | 2 images each | ✅ ~8 min each |

## Pitfalls
- **Never** use `terminal(background=true)` for AGY — causes SIGTERM 143
- AGY in `--print` mode with `--dangerously-skip-permissions` is the only reliable invocation
- Subagent context must include LINEAR_API_KEY and label IDs for the BOOK END protocol
- AGY sometimes generates MORE than requested (bonus assets) — verify and accept them
