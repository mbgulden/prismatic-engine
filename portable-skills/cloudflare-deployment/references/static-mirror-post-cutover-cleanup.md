# Static Mirror Post-Cutover Cleanup Pitfalls

## Batch HTML Regex: Test on 5 pages before 240

**Session:** 2026-06-04 — Active Oahu Tours mirror SEO sprint.

**What happened:** A Python regex script was applied to 240 HTML pages to fix broken links (unicode spaces, smart quotes, WP-encoded anchors, dead author links). The script used `re.sub` patterns operating on raw HTML content.

**Result:** Broken links INCREASED from 947 to 5,212. The regexes introduced new issues:
- Smart quote replacement converted legitimate quote characters
- Anchor rewriting (`#content` → `#main`) broke navigation links
- Removing `/ja/author/*` links created orphaned `<a>` tags
- URL encoding normalization collided with intentionally encoded characters

**Fix — use targeted surgical edits, not blanket regex:**

1. **Identify specific broken link patterns** before writing cleanup code
2. **Test on 5 representative pages first**, not all 240
3. **Verify broken link count decreased** after the test run
4. **Commit between each fix type** so individual regexes can be reverted
5. **Run the broken link checker after each commit** to confirm improvement

**Safe cleanup approaches:**
- Remove specific known-broken `<script>` tags (closed pattern, no ambiguity): ✅ safe
- Add missing canonical/schema tags (injection only, no existing content modified): ✅ safe  
- Fix unicode spaces in hrefs: ⚠️ test first — may be intentional encoding
- Rewrite anchor fragments: ❌ dangerous — too many edge cases
- Remove links by pattern match: ❌ dangerous — orphaned tags, broken DOM

## WP Artifact Cleanup: Script tags only

When removing WordPress artifacts from a static mirror, limit mass edits to `<script>` tags referencing `wp-includes/` or `wp-content/plugins/`. These are self-contained elements — removing them doesn't affect surrounding DOM. Do NOT touch inline CSS, link tags, or `<a href>` elements with batch regex.
