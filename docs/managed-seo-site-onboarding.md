# Managed SEO site onboarding: GSC + GA4 + PE-native crons

This repo supports a portable managed-site registry so Active Oahu-style SEO automation can be turned on for every managed website without hardcoding one domain per script.

## Site registry

Default config:

```text
config/seo_sites.json
```

Override per host:

```bash
export PRISMATIC_SEO_SITES_CONFIG=/path/to/seo_sites.json
```

List configured sites:

```bash
python3 scripts/seo/site_registry.py list
```

Scaffold a new site entry:

```bash
python3 scripts/seo/site_registry.py scaffold example.com --name "Example Site"
```

Add the scaffolded JSON object to `config/seo_sites.json` or the host-specific config pointed at by `PRISMATIC_SEO_SITES_CONFIG`.

## Onboarding checklist for a new website

1. **Add registry entry**
   - `slug`
   - `domain`
   - `origin`
   - `gsc_property` — default to `sc-domain:example.com`
   - `sitemap_url`
   - `ga4_property_id` or `ga4_property_env`
   - `gtm_container_id` or `gtm_container_env`
   - `ga4_measurement_id` or `ga4_measurement_env`
   - `expected_data_layer_events`
   - static `site_dir_candidates` when available

2. **Google Search Console**
   - Add a Domain property in Search Console: `sc-domain:example.com`.
   - Verify ownership, usually with DNS TXT.
   - Submit `https://example.com/sitemap.xml` when sitemap is live.
   - Ensure this PE host's ADC user/service account can read the property via the Search Console API.

3. **Google Tag Manager**
   - Create/select the GTM container for the site.
   - Install the GTM `<head>` snippet once on every page.
   - Install the GTM `<noscript>` fallback immediately after the opening `<body>` tag.
   - Set `gtm_container_id` in the registry or export the configured env var, e.g. `EXAMPLE_COM_GTM_CONTAINER_ID=GTM-XXXXXXX`.
   - The site should push clean business events to `window.dataLayer`; GTM maps those events to GA4, Google Ads, remarketing, and other destinations.

4. **Google Analytics 4**
   - Create/select the GA4 property.
   - Create a web data stream for the site.
   - Configure the GA4 Configuration tag in GTM using the stream measurement ID.
   - Set `ga4_measurement_id` in the registry or export the configured env var, e.g. `EXAMPLE_COM_GA4_MEASUREMENT_ID=G-XXXXXXXXXX`.
   - Set `ga4_property_id` in the registry or export the configured env var, e.g. `EXAMPLE_COM_GA4_PROPERTY_ID=123456789`.
   - Mark key events/conversions: booking start, checkout click, lead submit, purchase/booking complete.
   - If booking completes off-site, configure cross-domain tracking and/or server-side Measurement Protocol/imports.
   - Ensure ADC can read GA4 with `https://www.googleapis.com/auth/analytics.readonly`.

5. **Run setup audit**

```bash
python3 scripts/seo/managed_site_setup_audit.py
```

Outputs:

```text
$PRISMATIC_STATE_DIR/seo/site-setup/latest_site_setup_audit.json
$PRISMATIC_STATE_DIR/seo/site-setup/latest_site_setup_audit.md
```

6. **Run GA4 insights**

```bash
python3 scripts/seo/ga4_insights.py
```

Outputs:

```text
$PRISMATIC_STATE_DIR/seo/ga4-insights/latest_ga4_insights.json
$PRISMATIC_STATE_DIR/seo/ga4-insights/latest_ga4_insights.md
```

## PE-native cron jobs

| Native cron ID | Schedule | Purpose |
|---|---:|---|
| `seo.managed-sites-setup-audit` | `0 5 * * 0` | Weekly GSC/sitemap/GTM/dataLayer/GA4 setup blocker audit for every managed site. |
| `seo.managed-sites-ga4-insights` | `0 6 * * *` | Daily GA4 conversion/revenue/page economics pull for every configured site. |

Install/update crontab after changing site config or cron definitions:

```bash
python3 scripts/install_native_crons.py
```

## What GA4 gives us that GSC cannot

GSC is search-engine visibility. It tells us Google organic queries, impressions, clicks, CTR, average position, page/query pairings, indexing and sitemap signals.

GA4 is visitor and business behavior after the click. For tourism sites, GA4 is essential because it can answer:

| Question | GSC | GA4 |
|---|---:|---:|
| Which Google queries/pages got impressions and clicks? | yes | no |
| Which landing pages converted? | no | yes |
| Page-level conversion rate | no | yes |
| Sitewide conversion rate | no | yes |
| Booking/revenue value by page/channel | no | yes, if ecommerce/booking events are configured |
| Funnel drop-off: view → booking click → checkout → purchase | no | yes, if events are configured |
| Non-Google channels: direct, referral, email, paid social, organic social | no | yes |
| Engagement, retention, returning users, geography/device/browser | limited/no | yes |
| Attribution across channels and campaigns | no | yes |
| Internal campaign/UTM performance | no | yes |

## Required GA4 event strategy for booking revenue

For online booking revenue to be trustworthy, each managed tourism site needs consistent GA4 events:

| Event | Purpose |
|---|---|
| `booking_start` | User opens booking widget / clicks FareHarbor booking CTA. |
| `begin_checkout` | User reaches provider checkout, when detectable. |
| `purchase` | Confirmed booking with `transaction_id`, `currency`, `value`, and item metadata. |
| `generate_lead` | Form/phone/contact lead when no online purchase occurs. |
| `booking_click` | Fallback click event for off-site booking links when purchase confirmation is unavailable. |

Without a real `purchase` or imported booking-complete event, GA4 can still show booking intent and conversion rate, but revenue will be partial or zero.
