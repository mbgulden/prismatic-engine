# Static Mirror Page Generation Pattern

## Template Extraction
```python
SITE = "/path/to/mirror/site"

with open(f"{SITE}/index.html", 'r') as f:
    lines = f.read().split('\n')

head_end = body_start = content_start = content_end = footer_end = body_end = None

for i, line in enumerate(lines):
    ln = i + 1
    if '</head>' in line and head_end is None: head_end = ln
    if '<body' in line and body_start is None: body_start = ln
    if 'class="entry-content"' in line and content_start is None: content_start = ln
    if '<!-- .entry-content -->' in line and content_end is None: content_end = ln
    if '</footer>' in line and footer_end is None: footer_end = ln
    if '</body>' in line and body_end is None: body_end = ln

head_template = '\n'.join(lines[:head_end])
body_top = '\n'.join(lines[body_start-1:content_start-1])
body_bottom = '\n'.join(lines[content_end:body_end])

os.makedirs(f"{SITE}/_templates", exist_ok=True)
with open(f"{SITE}/_templates/head.html", 'w') as f: f.write(head_template)
with open(f"{SITE}/_templates/body_top.html", 'w') as f: f.write(body_top)
with open(f"{SITE}/_templates/body_bottom.html", 'w') as f: f.write(body_bottom)
```

## Page Generation
```python
head = head_template
head = re.sub(r'<title>[^<]+</title>', f"<title>{title}</title>", head)
head = re.sub(r'<meta name="description" content="[^"]*"', f'<meta name="description" content="{desc}"', head)
head = re.sub(r'<meta property="og:title" content="[^"]*"', f'<meta property="og:title" content="{title}"', head)
head = re.sub(r'<meta property="og:description" content="[^"]*"', f'<meta property="og:description" content="{desc}"', head)
head = re.sub(r'<meta property="og:url" content="[^"]*"', f'<meta property="og:url" content="https://domain.com/{slug}/"', head)
head = re.sub(r'<meta name="twitter:title" content="[^"]*"', f'<meta name="twitter:title" content="{title}"', head)
head = re.sub(r'<meta name="twitter:description" content="[^"]*"', f'<meta name="twitter:description" content="{desc}"', head)
head = re.sub(r'<link rel="canonical" href="[^"]*"', f'<link rel="canonical" href="https://domain.com/{slug}/"', head)

schema_ld = f"<script type='application/ld+json'>{json.dumps(schema)}</script>"
head = head.replace('</head>', f'{schema_ld}\n</head>')

content_block = f"""    <div id="content" class="site-content">
        <div class="entry-content">
            <h1>{h1}</h1>
            {body_html}
        </div><!-- .entry-content -->
    </div>"""

page = head + '\n' + body_top + '\n' + content_block + '\n' + body_bottom + '\n</body>\n</html>'
```

## Schema Types (per page category)
- **Tour/activity pages**: TouristTrip (if commercial transaction at shop), Article (if informational guide)
- **Rental pages**: Product with AggregateOffer (lowPrice/highPrice)
- **Hub pages**: ItemList
- **Contact**: ContactPage
- **About**: Organization
- **Informational guides**: Article with author Person + publisher Organization
- **Blog/adventure**: Article (Yoast already provides on WP pages)

## Pitfalls
- Don't use `read_file()` for the homepage — it's 180K+ chars and truncates. Use `terminal()` with Python file I/O instead.
- The `&` in HTML entities (`&amp;`, `&mdash;`) causes shell backgrounding errors in heredocs. Write scripts to `.py` files first, then execute.
- CSS paths are relative (`wp-content/themes/...`). Pages at root level resolve correctly. If creating pages at subdirectory levels, use `../../wp-content/...` (but the mirror's existing deep pages already handle this via wget rewriting).
- Don't use regex to replace schema blocks — use string `find()` to locate script boundaries, then slice-and-replace.
