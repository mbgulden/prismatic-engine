# Family JSON Multi-Profile Pattern

Use this pattern when a bot or service needs to switch between multiple user
profiles (family members, team members, contexts) without a database.

## File Structure

```json
{
  "family": {
    "alias": {
      "name": "Full Name",
      "year": 1989, "month": 12, "day": 10, "hour": 17.1167,
      "location": "City ST",
      "lat": 34.27, "lon": -118.78,
      "timezone": "America/Los_Angeles",
      "hd_type": "Projector",
      "profile": "3/5",
      "authority": "Splenic"
    }
  },
  "active": "alias"
}
```

## Python Loader Pattern

```python
_family_data = {}
_active_profile = "default"

def _load_family():
    global _family_data, _active_profile
    with open("family.json") as f:
        data = json.load(f)
        _family_data = data.get("family", {})
        _active_profile = data.get("active", "default")

def _get_active_birth():
    _load_family()
    member = _family_data.get(_active_profile, {})
    return { ... } if member else FALLBACK

def _set_active_profile(profile: str) -> bool:
    if profile in _family_data:
        _active_profile = profile
        # Persist to file
        data["active"] = profile
        json.dump(data, open("family.json", "w"), indent=2)
        return True
    return False
```

## Why No Database

- Zero dependencies, zero migrations
- Human-readable and hand-editable
- Gitignored (`.env` sibling) — no birth data in version control
- Single source of truth, no sync needed

## Pitfalls

- Always gitignore the JSON file if it contains personal data
- The `active` field must be persisted to survive bot restarts
- Order of loading matters: `_load_family()` before `_get_active_birth()`
