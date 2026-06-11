# API Encoding: Always Prefer JSON over Form-URLEncoded

## The Bug

The Stripe payment server used `urllib.parse.urlencode()` on nested dictionary payloads:

```python
# BROKEN — urlencode doesn't handle nested dicts/lists
data = {"line_items": [{"price_data": {...}, "quantity": 1}]}
urllib.parse.urlencode(data).encode()
# Produces: line_items=[{'price_data': ...}] — Stripe returns 400
```

## The Fix

Switch to JSON encoding. Most modern REST APIs (Stripe, GitHub, Linear) accept `application/json`:

```python
# FIXED
req.add_header("Content-Type", "application/json")
req.data = json.dumps(data).encode()
```

## When to Use Each

| Encoding | Use When |
|----------|----------|
| JSON (`application/json`) | **Default choice.** Supports nested objects, arrays, all modern APIs. |
| Form-URLEncoded | Legacy APIs, OAuth token endpoints, simple flat key=value pairs only |

## Detection

If an API returns 400 Bad Request on what looks like valid data, check:
1. Is the Content-Type correct?
2. Is the encoding handling nested structures?
3. Does the API accept JSON? (Most do — try it first.)

This bug was discovered by AGY during a project audit of the HD Engine payment server. The fix was a 2-line change.
