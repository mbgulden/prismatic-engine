# Japanese SEO Schema Localization Patterns

## Common Machine-Translation Errors

### Geo-name mistranslation
```
WRONG: テーブル 3 つ (three dining tables)
RIGHT: スリー・テーブルズ (Three Tables — the actual beach location)
```

### Duplication
```
WRONG: ドライバッグ、ドライバッグ (dry bag, dry bag)
RIGHT: ドライバッグ (dry bag — once)
```

### Keyword-stuffed titles
```
WRONG: オアフ ビーチ チェア レンタル、ノース ショア オアフ、ライエ近く、PCC、ハウウラ、ハワイ
RIGHT: オアフ島ビーチチェア・レンタル | アクティブ・オアフ・ツアーズ
```

### English-left touristType
```
WRONG: "touristType": ["Adventure Travelers", "Families"]
RIGHT: "touristType": ["アドベンチャー旅行者", "ファミリー"]
```

### Wrong language in twitter:title
Japanese pages should have Japanese og:title but English twitter:title (Twitter doesn't render Japanese well).

## Schema Templates

### TouristTrip (Tour/Activity)
```json
{
  "@context": "https://schema.org",
  "@type": "TouristTrip",
  "name": "[CLEAN JAPANESE TITLE — no keyword stuffing]",
  "description": "[150-200 char polite Japanese description in です/ます調]",
  "url": "https://activeoahutours.com/ja/[path]/",
  "tourOperator": {
    "@type": "TravelAgency",
    "name": "Active Oahu Tours",
    "url": "https://activeoahutours.com",
    "telephone": "+1-808-123-4567"
  },
  "touristType": [
    "アドベンチャー旅行者",
    "ファミリー",
    "カップル",
    "グループ"
  ],
  "offers": {
    "@type": "Offer",
    "priceCurrency": "USD",
    "price": "[PRICE]",
    "availability": "https://schema.org/InStock",
    "url": "https://activeoahutours.com/ja/[path]/"
  }
}
```

### Product (Equipment Rental)
```json
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "[CLEAN JAPANESE PRODUCT NAME]",
  "description": "[Polite Japanese description]",
  "url": "https://activeoahutours.com/ja/[path]/",
  "brand": {
    "@type": "Brand",
    "name": "Active Oahu Tours"
  },
  "offers": {
    "@type": "Offer",
    "priceCurrency": "USD",
    "price": "[PRICE]",
    "availability": "https://schema.org/InStock",
    "url": "https://activeoahutours.com/ja/[path]/"
  }
}
```

### TravelAgency (Homepage)
```json
{
  "@context": "https://schema.org",
  "@type": "TravelAgency",
  "name": "アクティブ・オアフ・ツアーズ（Active Oahu Tours）",
  "description": "[Japanese description of services]",
  "url": "https://activeoahutours.com/ja/",
  "telephone": "+1-808-123-4567",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "134B Hamakua Drive",
    "addressLocality": "Kailua",
    "addressRegion": "HI",
    "postalCode": "96734",
    "addressCountry": "US"
  },
  "sameAs": [
    "https://www.facebook.com/activeoahutours/",
    "https://www.instagram.com/activeoahu/",
    "https://twitter.com/activeoahutours"
  ],
  "openingHoursSpecification": {
    "@type": "OpeningHoursSpecification",
    "dayOfWeek": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
    "opens": "08:00",
    "closes": "17:00"
  }
}
```

## TouristType Mappings
| English | Japanese |
|---|---|
| Adventure Travelers | アドベンチャー旅行者 |
| Families | ファミリー |
| Couples | カップル |
| Groups | グループ・団体 |

## Geo-name Glossary (Hawaii-specific)
| English | Correct Katakana | Wrong MT |
|---|---|---|
| Sharks Cove | シャークス・コーブ | — |
| Three Tables | スリー・テーブルズ | テーブル3つ |
| Chinaman's Hat | チャイナマンズ・ハット | — |
| Waimea Bay | ワイメア湾 | — |
| Kailua | カイルア | — |
| Lanikai | ラニカイ | — |
| Laie | ライエ | — |
| Hauula | ハウウラ | — |
| Haleiwa | ハレイワ | — |
| Kahana | カハナ | — |
| Kualoa | クアロア | — |

## Batch Fix Workflow for JA Pages

### Approach A: EN→JA Schema Mirroring (preferred when EN pages already have schema)

When English pages already have proper JSON-LD schema, mirror it to Japanese pages rather than creating schema from scratch:

1. **Map JA pages to EN counterparts** — strip the `ja/` prefix from the JA path to find the EN equivalent
2. **Extract schema blocks from EN** — parse all `<script type="application/ld+json">` blocks, collecting `@type` values
3. **Categorize by schema state**:
   - Pages with only joke `WebPage` schema ("This Page is on an Adventure, Sorry") → full inject
   - Pages missing specific types vs EN → partial inject
   - Pages already matching EN → skip
4. **Localize URLs** — replace `activeoahutours.com/` with `activeoahutours.com/ja/` in `url`, `@id`, and `mainEntityOfPage.@id` fields
5. **Inject baseline on every page** — WebSite + Organization (these are business-level, language-neutral)
6. **Remove joke schemas** — strip any schema containing "This Page is on an Adventure"
7. **Verify** — `grep -c 'application/ld+json'` on all JA pages; `grep -rl '"@type"'` to confirm types present

```python
# Core mirroring logic
en_rel = Path(*ja_rel.parts[1:])  # strip ja/ prefix
en_path = base / en_rel
en_content = en_path.read_text()
ja_url = f'https://activeoahutours.com/{ja_rel.parent}/'.replace('/index.html', '/')
# Extract schema blocks, localize URLs, inject
```

### Approach B: Template-Based Injection (when EN pages lack schema)

1. Scan all `ja/**/index.html` files
2. For each: check if schema exists; if not, inject before `</head>`
3. For each: check title for keyword stuffing (commas ≥ 3 or geo-chain pattern)
4. For each: check meta description for known MT errors (duplication, geo mistranslation)
5. Fix in passes: schema pass → meta pass → title pass
6. Verify with `grep` for known bad patterns:
   ```bash
   grep -r 'テーブル 3 つ' site/ja/  # should return nothing
   grep -r 'PCC、' site/ja/           # keyword stuffing indicator
   ```

## Branch Warning (active-oahu-tours-mirror)
The `main` and `master` branches have diverged significantly. Both exist on origin and both are tracked locally. The active deployment branch (the one Cloudflare Pages auto-deploys from) varies — in recent cron-driven work (Jun 2026), `master` was the configured target. Before starting work, verify which branch is live:
```bash
git fetch origin
git log --oneline origin/main -3
git log --oneline origin/master -3
```
Then commit and push to the branch that is the current deployment target. Never cherry-pick between `main` and `master` — the file content is too different; re-apply changes fresh instead.
