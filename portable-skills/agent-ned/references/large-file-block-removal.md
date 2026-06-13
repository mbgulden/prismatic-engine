# Large File Block Removal — Python Line Slicing

When `patch` tool can't handle 200+ line deletions, use Python list slicing. This is the most reliable approach for surgical removal of large contiguous blocks.

## Pattern

```python
with open('path/to/file.js', 'r') as f:
    all_lines = f.readlines()

# Keep lines 0-1801 (first 1802) + lines 2522-2589 (last 68)
# Remove lines 1803-2522 (720-line duplicate)
kept = all_lines[0:1802] + all_lines[2522:2590]

with open('path/to/file.js', 'w') as f:
    f.writelines(kept)
```

## Verification Steps

1. **Print the join point** to ensure clean boundaries:
   ```python
   print(f"Last kept from first section: {repr(all_lines[1801])}")
   print(f"First kept from last section: {repr(all_lines[2522])}")
   ```

2. **Check for removed symbols** — search the new file for class/function names that should be gone:
   ```python
   content = ''.join(kept)
   for cls in ['class Foo', 'class Bar']:
       assert cls not in content, f"{cls} still present!"
   ```

3. **Verify critical cross-references** — code that referenced the removed block (e.g., `typeof` guards):
   ```python
   if 'typeof removedVar' in content:
       print("Guard preserved")
   ```

4. **Syntax check** immediately after write:
   ```bash
   timeout 10 node -e "
   const fs = require('fs');
   const code = fs.readFileSync('path/to/file.js', 'utf8');
   try { new Function(code); console.log('SYNTAX OK'); }
   catch(e) { console.log('SYNTAX ERROR:', e.message); }
   "
   ```

## When to Use

- Block removal of 200+ lines (patch tool fuzzy matching unreliable)
- Removing a duplicate that was extracted to another file
- When the block's start/end boundaries are at clean line breaks (no partial-line surgery needed)

## When NOT to Use

- When you need to modify content mid-line → use `patch` or `sed`
- When the block spans a partial function/class → may leave dangling braces
- When the surrounding code has non-obvious closure dependencies on the removed block

## Real Example (Jun 2026)

GRO-1062: Removed 720-line dialogue duplicate from `js/ui.js` (lines 1803-2522). The duplicate contained `DialogueSequence`, `DialogueBox`, `PortraitRenderer`, `CommsOverlay` classes already extracted to `js/ui/dialogue.js`. Kept post-dialogue UI code (lines 2523-2590): status panel toggle, DOMContentLoaded handler, canvas click handler. Result: 2,590 → 1,870 lines (-28%), syntax verified.
