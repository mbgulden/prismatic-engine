# GRO-713: Pipeline Router — Buggy Partial Implementation Pattern

## Context

GRO-713 asked Fred to "Build the Agent Pipeline Router: auto-chain review workflows by task type." When the nudge executor pre-verified artifacts (Step 0.5), the `pipeline-templates.json` and router functions in `agent_dispatcher.py` already existed with all 6 pipeline templates fully defined. The code compiled, ran, and appeared complete.

## The Bug

Pipeline keyword auto-detection used substring matching (`kw in text`), which caused the keyword `"ui"` (from visual-design keywords) to match inside the word `"b**ui**ld"` in backend-api task titles.

**Example of the false positive:**
- Title: "Add new API endpoint for user profiles"
- Description: "Build a REST endpoint"
- `"ui" in "build"` → `True` → incorrectly routed to `visual-design` pipeline
- Expected: `backend-api` pipeline (due to "api" and "endpoint" keywords)

## The Fix

Two changes to `detect_pipeline_type()` in `agent_dispatcher.py`:

1. **Word-boundary regex** instead of substring matching:
   - Before: `if kw in text: return pipe_name`
   - After: `if re.search(r'\b' + re.escape(kw) + r'\b', text): score += 1`

2. **Scored resolution** instead of first-match-wins:
   - Before: first keyword match returned immediately
   - After: all pipelines scored by number of keyword matches, highest score wins

## Verification

A 115-test test suite was created (`tests/pipeline_router_test.py`) covering:
- Template structure validation (all 6 templates)
- Chain continuity (handoff matches next step in chain)
- Label-based detection (all 6 trigger labels)
- Keyword auto-detection (8 real-world examples)
- Edge case verification (no false positives from substring matches)

Results: 115/115 ✅

## Key Takeaway for Future Nudge Executors

When the issue says "build X" and X already exists on disk:
1. Run the existing code with test data to verify correctness
2. Look for subtle bugs — the code may work for the happy path but fail on edge cases
3. Create a test suite if none exists (catches regressions from the fix)
4. The fix may be smaller than it seems (2 lines changed in this case), but the test suite is the durable deliverable
