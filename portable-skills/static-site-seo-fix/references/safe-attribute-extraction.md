# Safe HTML Attribute Extraction

## The Core Rule

**Match only the delimiter character, not both quote types.**

When an HTML attribute uses double quotes (`"`), the content can (and often does) contain single quotes (`'`). Using `[^"\']` in a regex character class stops at either quote, truncating content at the first apostrophe.

## Wrong vs. Right

### Wrong — breaks on any content with apostrophes

```python
# ❌ Stops at the first single quote, truncating "Oahu's best..." → "Oahu"
re.search(r'content="([^"\']*)"', html)
```

### Right — only stops at the matching delimiter

```python
# ✅ Double-quoted attributes: only stop at double quotes
re.search(r'content="([^"]*)"', html)

# ✅ Single-quoted attributes: only stop at single quotes
re.search(r"content='([^']*)'", html)

# ✅ Order-agnostic meta tag with double quotes
re.search(r'<meta\s+content="([^"]*)"\s+name="description"', html)
re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html)
```

## Canonical Tags — Order-Agnostic

WordPress/Simply Static exports use `href` before `rel`:
```html
<link href="https://activeoahutours.com/contact-us.html" rel="canonical"/>
```

Don't assume `rel=` comes first. Use patterns that don't care about order:
```python
# ✅ Just check presence — order doesn't matter
re.search(r'<link\s[^>]*rel="canonical"', content)

# ❌ Assumes rel comes before href
re.search(r'<link\s+rel="canonical"\s+href="([^"]*)"', content)
```

## Verification Script Pattern

When verifying fixes, use the SAME regex patterns used for the fix. A common failure mode is fixing with one pattern and verifying with another, producing false positives.

```python
# CONSISTENT: fix pattern matches verify pattern
desc_match = re.search(r'<meta\s+content="([^"]*)"\s+name="description"', content)
if not desc_match:
    desc_match = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', content)
```
