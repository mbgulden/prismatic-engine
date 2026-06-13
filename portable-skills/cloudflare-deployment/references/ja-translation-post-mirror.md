# Japanese Translation for WordPress Static Mirrors

When a WordPress site using Weglot is scraped to static HTML, the Japanese (`/ja/`) pages retain `lang="ja"` in their `<html>` tag but contain **English content**. Weglot translates dynamically on the WordPress server side — the static export captures the language shell but not the translated text.

This reference covers translating those pages from English to Japanese after the mirror is complete.

## Scope

All HTML files under the `/ja/` directory. In the Active Oahu mirror: 83 pages across nested directories.

## Tool: `deep-translator` (NOT `googletrans`)

```bash
pip3 install deep-translator --break-system-packages
```

**`deep-translator` is mandatory.** `googletrans` (4.0.0rc1) hangs indefinitely on its first API call when run in background or subprocess mode — zero output, process stuck in sleep state. `deep-translator` wraps the same Google Translate API but returns in <1s.

```python
from deep_translator import GoogleTranslator
translator = GoogleTranslator(source='en', target='ja')
result = translator.translate("Experience the best kayaking on Oahu.")
# → "オアフ島で最高のカヤックを体験してください。"
```

## What to Translate

Translate these elements per page:

- **Meta tags**: `description`, `og:title`, `og:description`, `twitter:description`
- **Title tag**: `<title>...</title>`
- **Content text**: `h1`-`h4`, `p`, `li`, `td`, `th`, `figcaption`, `blockquote`

Skip: phone numbers (`(808)498-1894`), pure numbers, URLs, `<script>`, `<style>`, `<pre>`, `<code>`, empty nodes.

## Rate Limiting

Insert `time.sleep(0.15)` between individual translations, `time.sleep(0.3)` between pages. Google's free API has no documented hard limit but will throttle if you fire requests without delay.

## Batch Script Pattern

```python
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from pathlib import Path
import time, re

JA_DIR = Path("site/ja")

def translate_page(path):
    with open(path) as f:
        html = f.read()
    soup = BeautifulSoup(html, 'lxml')  # lxml is 10x faster than html.parser
    translator = GoogleTranslator(source='en', target='ja')
    
    texts = []
    # ... collect from meta, title, headings, paragraphs, list items ...
    
    for elem, attr, orig in texts:
        result = translator.translate(orig)
        if attr == 'string':
            elem.string.replace_with(result)
        else:
            elem[attr] = result
        time.sleep(0.15)
    
    with open(path, 'w') as f:
        f.write(str(soup))
```

## Background Execution

Use `terminal(background=true, notify_on_complete=true)` with `tee` to capture progress:

```bash
cd /home/ubuntu/work && python3 -u translate-ja-batch.py 2>&1 | tee /tmp/translate-ja.log
```

Add `-u` flag for unbuffered stdout so progress is visible in the log file. Check progress with `cat /tmp/translate-ja.log`.

**CRITICAL: Library-level buffering.** `python3 -u` and `flush=True` only fix CPython's own buffering. `deep-translator` (and many HTTP-based libraries) has internal buffering that can make a background process show ZERO output for 5+ minutes even while it's actively translating files. Do NOT kill the process based on empty stdout alone. Instead, verify progress by checking file modification timestamps:

```bash
# Create a timestamp marker before starting
touch /tmp/translate_start

# While the script runs, check how many files have been modified:
find site/ja -name "*.html" -newer /tmp/translate_start | wc -l

# Check specific files for Japanese characters:
grep -cP '[\x{4E00}-\x{9FFF}\x{3040}-\x{309F}\x{30A0}-\x{30FF}]' site/ja/index.html
```

If the file count is increasing, the script is working regardless of terminal output. Only kill the process if BOTH stdout is empty AND no files are being modified.

## Avoiding Double-Translation of Already-Japanese Content

Some pages may already have Japanese text (from manual edits, partial Weglot captures, or prior translation runs). Translating already-Japanese text through Google Translate produces garbled results. Add a detection check before translating:

```python
JAPANESE_RE = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]')

def is_already_japanese(text):
    jp_chars = len(JAPANESE_RE.findall(text))
    total_alpha = len(re.findall(r'[a-zA-Z]', text))
    return jp_chars > total_alpha and jp_chars > 5
```

Check each text before translating:

## Verify

After translation, each `/ja/` page should have Japanese characters in its content:

```bash
grep -cP '[\x{4E00}-\x{9FFF}\x{3040}-\x{309F}\x{30A0}-\x{30FF}]' site/ja/index.html
# Should return > 20 (Japanese characters found)
```

## Pre-Translation Fix

Before translating, remove the `translate="no"` attribute from `/ja/` page `<html>` tags — Weglot adds this to prevent double-translation, but it blocks browser auto-translate as a fallback:

```python
if 'translate="no"' in content:
    content = content.replace(' translate="no"', '')
```

## Estimated Time

83 pages × ~10-15s each = ~15-20 minutes for the full batch. The homepage (largest, ~84 translatable elements) takes ~30s; small pages (3-5 elements) take ~5s.
