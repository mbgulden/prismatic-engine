# WordPress Static Export Artifacts

## Corrupted Meta Description Tags

Seen on: `active-oahu-tours-mirror` (Simply Static export from WordPress + WooCommerce)

### Pattern

Meta description tags get corrupted with WooCommerce/plugin attributes injected:

```html
<meta add="" at="" checkout." code"="" content="[ACTUAL_DESCRIPTION]" discount="" name="description" or="" promo=""/>
```

The garbage attributes: `add=""`, `at=""`, `checkout."`, `code"=""`, `discount=""`, `or=""`, `promo=""`

The actual description content is embedded between `content="..."` and is usually correct — just the tag structure is broken.

### Detection

```python
import re

# Match any meta tag with name="description" that also has the telltale add="" attribute
broken = re.search(r'<meta\s+add=""[^>]*name="description"[^>]*>', content, re.IGNORECASE)
```

### Fix

```python
if broken:
    tag = broken.group(0)
    desc = re.search(r'content="([^"]*)"', tag).group(1)
    clean = f'<meta content="{desc}" name="description"/>'
    content = content.replace(tag, clean)
```

### Files affected (June 2026 session)

Six Japanese pages had this artifact on `active-oahu-tours-mirror`:
- `ja/oahu-equipment-rentals/how-to-transport-kayaks-and-sups-from-our-shop-in-kailua-to-the-beach/index.html`
- `ja/join-the-team/index.html`
- `ja/oahu-kayaking-and-beach-adventures/chinamans-hat-kayak-adventure/index.html`
- `ja/multi-day-kayak-and-beach-gear-rentals/kayak-beach-gear-rental-partners/become-a-partner/index.html`
- `ja/multi-day-kayak-and-beach-gear-rentals/kayak-beach-gear-rental-partners/index.html`
- `ja/multi-day-kayak-and-beach-gear-rentals/index.html`

All English pages were fine — the artifact only appeared on JA (Japanese) pages, suggesting a WPML or translation-plugin interaction triggered the corruption.
