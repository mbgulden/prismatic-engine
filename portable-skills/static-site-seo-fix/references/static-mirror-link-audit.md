# Static Mirror Link Audit & Fix Workflow

Systematic approach for finding and fixing broken internal links on a static HTML site mirror (WordPress → wget mirror, SSG output, etc.). Used for GRO-945 (Japanese page link paths) on the Active Oahu Tours mirror.

## Trigger Conditions

- Linear issue mentions "fix link paths," "broken internal links," ".html extension mismatch," or "path convention mismatch"
- A mirrored site has `<a href>` values that don't resolve against the actual filesystem
- Language-switcher links (Weglot, WPML, Polylang) pointing to non-existent translated pages

## Step 1: Collect All Internal Links

Extract all internal `<a href="...">` links for the target path prefix, EXCLUDING `<link>` tags (canonical, hreflang alternates):

```bash
cd <site_dir>
grep -roh 'href="<base-url>/<prefix>/[^"]*"' --include="*.html" . | sort -u
```

**Crucial filter**: Exclude `<link>` tags that use `rel="alternate"` or `hreflang` — those are SEO metadata, not user-clickable links. Focus on actual `<a>` tags.

## Step 2: Cross-Reference Against Filesystem

For each unique href, convert the URL path to a filesystem path and check if it exists:

```python
# Strip the base URL and trailing slash
path_part = href.replace('https://domain.com/ja/', '').rstrip('/')

# Check both conventions:
# Convention A: /ja/some-page/index.html (directory with index)
dir_path = os.path.join(site_root, 'ja', path_part)
if os.path.isdir(dir_path) and os.path.isfile(os.path.join(dir_path, 'index.html')):
    # OK

# Convention B: /ja/some-page.html (direct .html file)
file_path = os.path.join(site_root, 'ja', path_part + '.html')
if os.path.isfile(file_path):
    # OK

# Neither → BROKEN
```

## Step 3: Classify Broken Links

| Category | Pattern | Fix Strategy |
|---|---|---|
| **Trailing space** | `href="...tour/ /"` | Remove space → `href="...tour/"` |
| **Encoding garbage** | `%E2%80%9C` (left quote char) | Replace with proper URL or Google Maps link |
| **WP query leftover** | `?post_type=activities&p=2396` | Find the correct static page, replace with that URL |
| **No translation exists** | `/ja/some-page/` → no `ja/some-page/` dir | Point to English version or remove the link |
| **Directory missing index** | `/ja/rentals/` exists but no `index.html` | Create index.html or point to English equivalent |

## Step 4: Find Affected Files

```bash
grep -rln '<broken-pattern>' --include="*.html" <site_dir>
```

## Step 5: Batch-Fix with `patch()` Tool

Apply fixes one at a time, using the `patch()` tool with exact `old_string` / `new_string` pairs. Verify each fix immediately:

```bash
grep '<old-pattern>' <file> || echo "CLEAN"
```

## Step 6: Commit to Staging Branch

```bash
git add -A
git commit -m "GRO-NNN: Fix broken link paths — [summary]"
git push origin <staging-branch>
```

**CRITICAL**: Never push to `main`. All link fixes go to the staging branch (`deploy-fresh` for AOT mirror).

## Step 7: Verify on Staging Preview URL

Cloudflare Pages auto-creates a preview URL for every branch:

```bash
curl -s "https://<branch>.<project>.pages.dev/<fixed-page-path>" | grep '<expected-content>'
```

## Common Pitfalls

- **`grep -c` on no-match returns exit code 1**: Use `|| echo "CLEAN"` to handle the "no matches" case gracefully
- **Replacing only one occurrence when both ja + en versions need fixing**: Check for the same broken pattern in both the `/ja/` and root directories
- **WP query params in different URL forms**: `/ja/?post_type=...` vs `/?post_type=...` — check both prefixes
- **`hreflang` links look like broken `<a>` links**: Always exclude `<link>` tags from the scan — they're SEO alternates, not user-facing links, and pointing to a non-existent translation is correct behavior (tells search engines "no translation available")
- **Weglot language switcher links are `<a>` tags inside `<aside>`**: These ARE user-clickable and must be fixed if the target page doesn't exist

## Reference: GRO-945 Session (Jun 2026)

8 files changed, 12 fixes applied across 6 categories. Full commit: `cb97991b` on `deploy-fresh` branch of `active-oahu-tours-mirror`.
