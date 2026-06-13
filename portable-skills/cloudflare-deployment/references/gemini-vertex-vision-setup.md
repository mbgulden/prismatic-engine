# Gemini Vision via Google Cloud Vertex AI

Setup guide for using Gemini Flash for vision/image analysis billed against GCP credits.

## Why Vertex AI (not Gemini API)

- **Gemini API** (`generativelanguage.googleapis.com`) — free tier with its own billing, does NOT use GCP credits
- **Vertex AI** (`aiplatform.googleapis.com`) — bills against GCP credits, required for using Google Cloud free credits

## API Key Format

Google Cloud API keys come in different formats:
- `AIza...` — traditional format
- `AQ.Ab8...` — newer format (2025+) — works the same way, passed as `?key=AQ.Ab8...`

If a key shows "Agent Platform API" restriction, it won't work for Vertex AI generative models. Either remove the restriction or add Vertex AI API.

## Endpoint Pattern

```
POST https://{region}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{region}/publishers/google/models/{MODEL}:generateContent?key={API_KEY}
```

Examples:
- `us-central1-aiplatform.googleapis.com` for us-central1
- Model: `gemini-2.0-flash-001` or `gemini-1.5-flash-001`

## Prerequisites

1. Vertex AI API enabled: https://console.cloud.google.com/apis/library/aiplatform.googleapis.com
2. Gemini model enabled in Model Garden: https://console.cloud.google.com/vertex-ai/model-garden
3. API key without restrictive API limitations (or with Vertex AI API allowed)

## Quick Test

```bash
# Get project ID from existing key
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=YOUR_KEY" \
  -H "Content-Type: application/json" -d '{"contents":[{"parts":[{"text":"hi"}]}]}' \
  | grep -oP 'projects/\d+'

# Test Vertex AI endpoint
curl -s -X POST \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/PROJECT_ID/locations/us-central1/publishers/google/models/gemini-2.0-flash-001:generateContent?key=YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Say hello"}]}]}'
```

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| `NOT_FOUND` / model not found | Model not enabled in project | Enable in Model Garden |
| `PERMISSION_DENIED` / API_KEY_SERVICE_BLOCKED | Key restricted to wrong API | Remove or broaden API restrictions |
| `UNAUTHENTICATED` with Bearer token | API key doesn't work as OAuth token | Use `?key=` parameter, not Bearer header |

## Hermes Config

In `config.yaml`, under `auxiliary.vision`:

```yaml
auxiliary:
  vision:
    provider: google-vertex
    model: gemini-2.0-flash-001
    base_url: ''
    api_key: 'AQ.YourKeyHere'
    timeout: 120
    extra_body: {}
    download_timeout: 30
```

Note: Hermes may need a `google-vertex` provider defined in the `providers:` section with the correct `api:` endpoint. If `google-vertex` isn't a recognized provider name, use `custom:google-vertex` with explicit API base URL.
