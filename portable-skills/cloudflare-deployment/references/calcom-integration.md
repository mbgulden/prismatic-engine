# Cal.com Integration

Integrate Cal.com scheduling into static sites, bots, and agent workflows. Covers API-based profile management, event type creation, booking links, and inline embeds.

## Provider: Cal.com

### API Key
API keys are per-user, created at `cal.com/settings/security`. Format: `cal_live_...` for production, `cal_test_...` for sandbox.

Store in orchestrator `.env`: `CALCOM_API_KEY=cal_live_...` (chmod 600).

### Authentication
All requests use Bearer token:
```
Authorization: Bearer $CALCOM_API_KEY
Content-Type: application/json
```

### Endpoints (v2 API)

| Operation | Method | Endpoint |
|---|---|---|
| Get profile | GET | `/v2/me` |
| Update profile | PATCH | `/v2/me` |
| List event types | GET | `/v2/event-types?username=<slug>` |
| Create event type | POST | `/v2/event-types` |

### Get/Update Profile

```bash
# Read current profile
curl -s "https://api.cal.com/v2/me" \
  -H "Authorization: Bearer $CALCOM_API_KEY"

# Returns: username, name, email, bio, timeZone, weekStart, brandColor, etc.

# Set bio
curl -s -X PATCH "https://api.cal.com/v2/me" \
  -H "Authorization: Bearer $CALCOM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"bio": "Your bio text here. Supports line breaks."}'
```

### Event Types — List

```bash
curl -s "https://api.cal.com/v2/event-types?username=<slug>" \
  -H "Authorization: Bearer $CALCOM_API_KEY"
```

**Response structure (important):** The v2 API nests event types under `data.eventTypeGroups[].eventTypes[]`, NOT `data[]` directly. Each group has a `profile` object with `slug` and `name`. Event types include: `id`, `slug`, `title`, `length` (minutes), `hidden` (boolean), `description`.

When the `eventTypes` array is empty, no event types have been created yet — the booking link will 404.

### Event Types — Create

```bash
curl -s -X POST "https://api.cal.com/v2/event-types" \
  -H "Authorization: Bearer $CALCOM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Consultation Call",
    "slug": "30min",
    "description": "What the call is about — shown on booking page.",
    "length": 30,
    "hidden": false,
    "locations": [{"type": "google_meet"}],
    "bookingFields": [
      {"name": "name", "type": "name", "required": true},
      {"name": "email", "type": "email", "required": true},
      {"name": "notes", "type": "textarea", "required": false, "label": "Custom question for booker"}
    ]
  }'
```

**Key fields:**
- `title` — Display name of the event type
- `slug` — URL slug (e.g., "30min" becomes `cal.com/username/30min`)
- `length` — Duration in minutes
- `hidden` — `false` = publicly bookable; `true` = hidden from profile
- `locations` — Array of location types. Common: `google_meet`, `zoom`, `phone` (requires `{{phone}}` field), `inPerson` (requires `{{address}}` field)
- `bookingFields` — Custom questions shown during booking. Standard fields: `name`, `email`, `location`, `guests`, `rescheduleReason`, `notes`, `title`, `phone`, `address`, `smsReminderNumber`

### Bio Content Guidelines

The cal.com bio appears on the public booking page. Keep it:
- Direct and personal
- Specific about what to expect
- Anti-hype — no buzzwords, no inflated claims
- Includes a clear call outcome

### Public URL Caution
The username in API responses is lowercase (`michael-gulden`), but the public cal.com URL is case-sensitive. Always use the exact casing the user registered with. Test both versions if uncertain.

## Static Site Integration

### Inline Embed (Link-Based)
Simplest approach for static sites — no JavaScript, no iframe:
```html
<a href="https://cal.com/USERNAME/30min" target="_blank" rel="noopener">
  SCHEDULE_A_CALL
</a>
```

Opens cal.com in a new tab. No account required for bookers. No embedded iframe complexity or CSP issues.

### Combined Contact + Calendar Page
Pattern from Beyond SaaS: dedicated `/contact/` page with a two-column layout:
- Left column: Contact form (formsubmit.co or similar)
- Right column: Calendar CTA with explanation of what to expect

## Pitfalls

- **Username casing mismatch:** The cal.com public URL may be case-sensitive (`Michael-gulden`) while the API returns lowercase (`michael-gulden`). Always use the public-facing casing in links, API slug for queries.
- **Empty event types = 404:** New accounts have zero event types. The `/username/30min` link will 404 until an event type with slug `30min` is created. Create via API before linking from the site.
- **Bio is often empty by default:** New accounts have no bio. Set it via API as part of onboarding.
- **Event type response nesting:** The `event-types` endpoint returns `data.eventTypeGroups[].eventTypes[]` — not `data[]` directly. Iteration must go through groups.
- **API key scope:** API keys grant full access to the user's account. Store securely (chmod 600), never commit, never expose in memory or client-side code.
- **No API for some operations:** Brand colors, dark mode, and some visual settings may not have API endpoints. Check dashboard for those.
