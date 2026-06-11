# Michael's GCP / Vertex AI Environment (June 2026)

## Auth: Always gcloud, never Gemini API key

Michael accesses all Google AI services through Vertex AI with gcloud OAuth:
- Project: `darius-star-game`
- Auth: `gcloud auth print-access-token` → Bearer token
- Region: `us-central1`
- Account: `mbgulden@gmail.com`

**Critical rule**: Every Google AI service (Imagen 3, Veo 3.1, Lyria 2) uses the SAME gcloud auth. Never suggest or request a separate Gemini API key from aistudio.google.com. Michael's directive: "Stop trying to get me to use the Gemini API."

## Service Access Pattern

| Service | Model | Endpoint Style | Auth |
|---------|-------|---------------|------|
| Imagen 3 | `imagen-3.0-generate-001` | `:predict` | gcloud token → Bearer |
| Veo 3.1-lite | `veo-3.1-lite-generate-001` | `:predictLongRunning` | gcloud token → Bearer |
| Lyria 2 | `lyria-002` | PredictionServiceClient.predict() | gcloud ADC |

## Credits
- $280 GCP credits (Vertex AI)
- $100/month Google AI Ultra (includes Gemini/Lyria)
- Veo quota: 1 request/minute default
- Imagen quota: standard
- Lyria: $0.04 per 30s clip

## Discovery Rule
Michael provides model names from GCP console. Do NOT guess model names from documentation — they will 404. Ask Michael, or try the exact endpoint URL he provides.
