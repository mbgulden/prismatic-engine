# WordPress Mirror Filename Cleanup

After a `wget --mirror` of a WordPress site, the static files contain broken filenames that Cloudflare Pages rejects silently during build validation. These must be cleaned before deploying. This reference covers filename-level issues only; for content-level cleanup (link conversion, email obfuscation, CDN proxy stripping), see `references/static-mirror-lift-and-shift.md`.

## Category 1: Query-String Artifacts (`?ver=` files)

WP plugins and core enqueue assets with cache-busting version strings: `style.css?ver=3.7.2`. When wget downloads these, it saves them as literal filenames containing `?` — e.g., `style.css?ver=3.7.2.css`. These are **duplicates** of the actual files (which wget also downloads without the query string). They bloat the repo and may cause CF Pages validation failures.

**Detection:**
```bash
find . -type f -name '*\?*' | head -20
```

**Fix — delete them all:**
```python
import os
for root, dirs, files in os.walk('.'):
    for f in files:
        if '?' in f:
            os.remove(os.path.join(root, f))
```

Common sources: Gravity Forms, Weglot, Kadence Blocks, SVG Support, Akismet, WordPress core JS/CSS blocks, Job Board Manager. Expect 50-60 files.

## Category 2: Unicode Thin Space (U+202F)

macOS screenshots taken at certain times of day use a thin space (U+202F, Unicode `\u202f`) instead of a regular space between the time and "PM" — e.g., `Screenshot-2025-05-01-at-2.08.42 PM.png`. This invisible character causes CF Pages to reject the file during asset validation.

**Detection:**
```bash
find . -type f -name '* *'  # Copy the thin space from an actual filename
# Or programmatically:
python3 -c "
import os
for root, dirs, files in os.walk('.'):
    for f in files:
        if '\u202f' in f:
            print(os.path.join(root, f))
"
```

**Fix — replace with hyphen:**
```python
import os
for root, dirs, files in os.walk('.'):
    for f in files:
        if '\u202f' in f:
            old = os.path.join(root, f)
            new = os.path.join(root, f.replace('\u202f', '-'))
            os.rename(old, new)
```

## Category 3: Leading/Trailing Spaces in Filenames

Some scraped WordPress pages produce filenames with leading spaces: ` .html` instead of `.html`. These are invisible in most file listings and cause 404s or validation failures.

**Detection:**
```bash
find . -type f -name ' *' -o -name '* ' | head -10
```

**Fix — strip and normalize:**
```python
import os
for root, dirs, files in os.walk('.'):
    for f in files:
        stripped = f.strip()
        if f != stripped or '  ' in f:
            old = os.path.join(root, f)
            new_name = stripped.replace('  ', ' ').replace(' ', '-')
            new = os.path.join(root, new_name)
            if old != new:
                os.rename(old, new)
```

Real example from Active Oahu mirror: `activities/kailua-bay-mokulua-island-self-guided-kayak-tour/ .html` → `activities/kailua-bay-mokulua-island-self-guided-kayak-tour/.html`.

## Category 4: `@` in Filenames

Some WP plugin assets include `@` in filenames: `chosen-sprite@2x.png`. These are generally safe (CF Pages accepts them) but worth noting. No action needed unless CF changes its policy.

## Post-Fix Verification

After cleaning all categories:

```bash
# Verify no remaining weird filenames
find . -type f -name '*[^a-zA-Z0-9._/-]*' 2>/dev/null

# Verify no empty files
find . -type f -empty

# Verify no files over 25MB
find . -type f -size +25M

# All three should return nothing before deploying
```

## ⚠️ CRITICAL: Category 1 Deletion Can Nuke the Main Stylesheet

The query-string cleanup script (`if '?' in f: os.remove(...)`) is a blunt instrument. WordPress themes are enqueued as `style.css?ver=X.Y.Z` — and when wget downloads these, it saves the file WITH the query string as the literal filename: `style.css?ver=1743021094.css`. Unlike plugin assets (where a clean copy also exists), **the theme's main stylesheet often exists ONLY as the `?ver=` variant** — wget doesn't download a separate clean copy.

**If you blindly delete all `?ver=` files, you delete the site's entire CSS.** The css/ directory will be empty, and every page will lose all styling.

**Fix — check before deleting:**
```bash
# BEFORE running the deletion, find theme CSS files with ?ver= in the name
find . -path "*/themes/*" -name "*\?*" 
```
If any theme CSS files match, **extract them first** — rename to clean path, THEN delete the rest:
```bash
# Save the theme CSS before cleanup
mv site/wp-content/themes/activeoahu/css/style.css?ver=1743021094.css \
   site/wp-content/themes/activeoahu/css/style.css
```
Then update all HTML files to reference the clean path (the scraped HTML has `%3Fver=...` in the href — replace with the clean filename).

**Recovery if already deleted:** The CSS file is in git history. Find the last commit that had it, extract it, and restore:
```bash
git log --all --oneline -- "site/wp-content/themes/*/css/style.css*"
git show <commit>:site/wp-content/themes/activeoahu/css/style.css?ver=... > site/wp-content/themes/activeoahu/css/style.css
```
Then fix the HTML references from `style.css%3Fver=1743021094.css` → `style.css`.

**Affected page count:** The Active Oahu session had this happen across 196 HTML files. Recovery was: `git show 613ed59:path > restore.css` + Python find-replace on all HTML files.

## Why This Matters

CF Pages build failures from filename issues are **silent** — the Git-connected auto-deploy appears to succeed (repo clones, no build step runs) but then fails at asset validation with no clear error about the specific filename. The site serves stale cached content from the last successful deploy. You'll see 200 OK responses but with old content (old title tags, old canonical URLs) because CF is serving the previous build.

The symptom: pages return HTTP 200 but with the **wrong title** (often the homepage title) and old canonical URLs. This means the current build failed validation and CF fell back to the last successful deploy.
