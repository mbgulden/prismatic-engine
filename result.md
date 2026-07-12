# PWP Marketing: RSS/Atom Feed Generator (GRO-3076)

## Requirement Overview
We implemented an RSS/Atom and JSON feed generator for content marketing on PWP sites based on Markdown posts in `content/blog/*.md`. The generated feeds validate correctly, support per-tenant customization, and are auto-discovered from site pages.

Specifically:
- **Atom 1.0 Feed**: Generated at `/feed.xml` with namespaces, authors, published/updated dates, excerpts, and HTML contents.
- **JSON Feed 1.1**: Generated at `/feed.json` with correct spec version, home page / feed URLs, descriptions, and list of items including full authors lists and published dates.
- **Auto-Discovery Links**: Statically injected in `<head>` of the three templates (`saas`, `corporate`, `portfolio` `index.html` templates):
  `<link rel="alternate" type="application/atom+xml" href="/feed.xml" title="Atom Feed">`
- **Per-Tenant Customization**: Key customization attributes (title, subtitle, URL, description, language, author, public path configuration) are dynamically parsed from `config/marketing.yaml`.
- **Test Coverage**: Added `tests/test_pwp_rss.py` validating Atom 1.0 tags, JSON Feed 1.1 structure, chronological sort ordering, entry authors/dates, and auto-discovery rendering.

---

## Technical Details

### 1. Feed Generator Script (`scripts/generate_feeds.py`)
Parses Markdown frontmatter + body using `yaml` and `markdown` libraries, converts body content to HTML, dynamically reads customizations, and writes:
- `/feed.xml` using `xml.etree.ElementTree` with registered Atom namespace.
- `/feed.json` using JSON serialization for JSON Feed 1.1 format.

```python
# scripts/generate_feeds.py excerpt (generate_atom & generate_json_feed)
def generate_atom(posts, config, output_path):
    site_cfg = config['site']
    blog_cfg = config['blog']
    NS = 'http://www.w3.org/2005/Atom'
    ET.register_namespace('', NS)
    atom = ET.Element(f'{{{NS}}}feed')
    # ... Injects feed metadata & entry elements (published, title, link, summary, author, content) ...
    # Prettifies and writes to feed.xml

def generate_json_feed(posts, config, output_path):
    # Generates compliant JSON Feed 1.1 dictionary and dumps to feed.json
```

### 2. Auto-Discovery Integration
Injected into `plugins/pwp/templates/{saas,corporate,portfolio}/index.html`:
```html
<head>
  ...
  <link rel="alternate" type="application/atom+xml" href="/feed.xml" title="Atom Feed">
</head>
```

---

## Testing & Verification

### Unit/Integration Test (`tests/test_pwp_rss.py`)
We created a comprehensive test suite to run in the CI/CD pipeline using Pytest. The test runs in a temporary workspace directory to prevent file pollution:
```python
def test_feed_generation_and_metadata(tmp_path, monkeypatch):
    # Verifies config parsing, feed.xml tag/namespace structure, feed.json properties,
    # pubDate / date_published parsing, and author structures.

def test_templates_contain_auto_discovery():
    # Verifies all 3 templates render html with the alternate link in the head.
```

### Test Results
Executing the test suite shows all tests are passing:
```bash
.venv_dev/bin/pytest tests/test_pwp_rss.py -v
```
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0 -- /home/ubuntu/work/prismatic-engine/.venv_dev/bin/python3.12
cachedir: .pytest_cache
rootdir: /home/ubuntu/work/prismatic-engine
configfile: pyproject.toml
plugins: anyio-4.14.1
collecting ... collected 2 items

tests/test_pwp_rss.py::test_feed_generation_and_metadata PASSED          [ 50%]
tests/test_pwp_rss.py::test_templates_contain_auto_discovery PASSED      [100%]

============================== 2 passed in 0.39s ===============================
```
