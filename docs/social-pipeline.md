# Social Pipeline (GRO-572)

Auto-generate social posts from a media library and queue them for posting
through the Meta/Instagram Graph API.

> **Lane:** `prismatic/social/`, `scripts/social/`, `tests/social/`
> **Owner:** Ned
> **Issue:** GRO-572

## What it does

Every day (driven by cron), the pipeline:

1. **Selects** 3-5 photos from a configured media library.
2. **Captions** each photo from its tags/sidecar metadata.
3. **Queues** the posts at scheduled slots throughout the day.
4. **Publishes** any posts whose slot has elapsed via the Meta Graph API
   (Instagram Content Publishing flow).

If `META_ACCESS_TOKEN` / `META_BUSINESS_ID` are not present (or
`SOCIAL_DRY_RUN=1`), the publish stage is a no-op that returns a
deterministic synthetic media id — so cron can run safely without
real credentials.

## Why dry-run by default

A cron job must never post to live Instagram from a misconfigured shell.
The pipeline is therefore safe-by-default:

- Missing or empty Meta creds → `dry_run = True`.
- `SOCIAL_DRY_RUN=1` (any value) → forces dry-run even with creds.
- `SOCIAL_DISABLE_POSTING=1` → queue still runs, but `publish_due()`
  flips nothing; useful for staging.

## Architecture

```
                ┌────────────────────┐
                │ PhotoSelector      │ score = 0.4·recency + 0.4·freshness + 0.2·tag_bonus
                │ (media_library)    │ freshness = 1.0 if never posted
                └─────────┬──────────┘
                          │ list[Photo]
                          ▼
                ┌────────────────────┐
                │ CaptionGenerator   │ hook bank by tag + caption_seed + tags line
                └─────────┬──────────┘
                          │ list[Caption]
                          ▼
                ┌────────────────────┐
                │ SocialPipeline     │ slots: SOCIAL_CRON_HOURS (UTC HH:MM)
                │  .enqueue()        │ idempotent on (photo_id, slot_iso)
                └─────────┬──────────┘
                          │ Post{status=SCHEDULED}
                          ▼
                ┌────────────────────┐
                │ QueueStore         │ JSON, atomic write, corrupt-quarantine
                │  (queue.json)      │
                └─────────┬──────────┘
                          │ due() picks SCHEDULED where slot <= now
                          ▼
                ┌────────────────────┐
                │ MetaGraphClient    │ POST /media  →  POST /media_publish
                │  or DryRun...      │
                └────────────────────┘
```

## State machine

```
   PENDING ──► SCHEDULED ──► POSTING ──► POSTED   (terminal)
      │            │             │
      └─► FAILED ◄─┴─► FAILED ◄──┘
                   ▲
   FAILED ─────────┘  (operator retry)
```

`Post.transition()` enforces the table; `QueueStore.transition()` is the
operator-facing entry point and raises `IllegalStateTransition` on
disallowed moves.

## CLI

```bash
# Full daily batch (what cron runs)
python3 -m scripts.social.social_pipeline daily

# Just select
python3 -m scripts.social.social_pipeline select -n 5

# Just publish (e.g. after manual queue inspection)
python3 -m scripts.social.social_pipeline publish-due

# Inspect queue
python3 -m scripts.social.social_pipeline status

# Wipe queue (careful!)
python3 -m scripts.social.social_pipeline reset --confirm
```

All subcommands support `--json` for machine-readable output.

## Configuration

All via environment variables. Defaults are chosen so the cron call
"just works" without any env config beyond the library path.

| Variable | Default | Required for live? | Description |
|---|---|---|---|
| `SOCIAL_MEDIA_LIBRARY` | `~/mounts/synology-photo` | n/a | Root path with photos |
| `SOCIAL_QUEUE_PATH` | `$PRISMATIC_HOME/social_queue.json` | n/a | JSON queue file |
| `SOCIAL_DAILY_LIMIT` | `4` | no | 3-5 per GRO-572 |
| `SOCIAL_CRON_HOURS` | `15:00,17:00,19:00,21:00` | no | UTC HH:MM slots |
| `SOCIAL_HASHTAGS` | `#growthwebdev,#smallbusiness,#marketing` | no | Pad hashtags when photo has none |
| `SOCIAL_PUBLIC_URL_PREFIX` | (none) | yes (live) | Where Meta can fetch the photo |
| `META_ACCESS_TOKEN` | (none) | yes (live) | Instagram Graph long-lived token |
| `META_BUSINESS_ID` | (none) | yes (live) | IG Business Account ID |
| `META_API_VERSION` | `v21.0` | no | Graph API version |
| `SOCIAL_DRY_RUN` | auto | no | Force dry-run (`1`) |
| `SOCIAL_DISABLE_POSTING` | `0` | no | Skip the POSTING transition |

## Tagging photos (sidecar convention)

Drop a sibling file next to the photo. Either format works; the JSON
form wins if both exist.

**`photo.jpg.tags.json`** (preferred):
```json
["sunset", "ocean"]
```
or with a long-form caption:
```json
{"tags": ["hike", "mountain"], "caption_seed": "Top of the world."}
```

**`photo.jpg.txt`** (fallback): one keyword per line.

The selector never modifies the filesystem; tags only influence
selection scoring and caption hooks.

## Going live

1. **Connect a Meta Business / Instagram Business account.**
   Required: long-lived `META_ACCESS_TOKEN` and `META_BUSINESS_ID`
   with `instagram_content_publish` permission.
2. **Host photos at a public URL.** Meta fetches the image server-side;
   `file://` will not work. Set `SOCIAL_PUBLIC_URL_PREFIX` to your
   Cloudflare R2 / S3 / GCS bucket URL. Paths under the library are
   URL-encoded relative to that prefix.
3. **Smoke test in dry-run.** Run `daily` once; inspect
   `social_queue.json`; confirm slots look right.
4. **Switch to live.** Unset `SOCIAL_DRY_RUN`; ensure creds are present.
   Run `publish-due` manually to flush the queue.

## Tests

```bash
python3 -m pytest tests/social/ -v
```

59 tests cover selector scoring, caption normalization, state-machine
transitions, queue roundtrips, dry-run determinism, idempotent
re-runs, and disable-posting behavior.

## File map

| Path | Lane | Purpose |
|---|---|---|
| `prismatic/social/__init__.py` | `prismatic/` | Public API |
| `prismatic/social/config.py` | `prismatic/` | Env-driven config loader |
| `prismatic/social/models.py` | `prismatic/` | `Photo`, `Caption`, `Post`, `PostStatus` |
| `prismatic/social/selector.py` | `prismatic/` | `PhotoSelector` (recency/freshness/tags) |
| `prismatic/social/captioner.py` | `prismatic/` | `CaptionGenerator` (hook bank + dedup) |
| `prismatic/social/meta_client.py` | `prismatic/` | `MetaGraphClient` + `DryRunMetaClient` |
| `prismatic/social/queue_store.py` | `prismatic/` | Atomic JSON queue with state machine |
| `prismatic/social/pipeline.py` | `prismatic/` | `SocialPipeline` orchestrator |
| `prismatic/social/exceptions.py` | `prismatic/` | Typed errors |
| `scripts/social/social_pipeline.py` | `scripts/` | CLI entry point |
| `tests/social/` | `tests/` | 59 pytest tests |
| `docs/social-pipeline.md` | `docs/` | This file |

## Future work (v2)

- **LLM captioning.** `CaptionGenerator.generate()` is the seam — swap in
  a subclass that calls an LLM with the photo's tags and `caption_seed`.
- **EXIF/XMP tag reader.** Plug a real tag extractor into `PhotoSelector`
  to enrich scores for photos without sidecar files.
- **Carousel posts.** `MetaGraphClient.publish()` already supports
  `is_carousel_item=True`; add a stage that batches 2-10 photos.
- **Per-platform variants.** Currently Instagram-first. Add
  Facebook Page and Twitter/X adapters that share the `Caption` model.
- **Slack/Telegram approval gate.** Optional `SCHEDULED → POSTING`
  transition only after a human approves from a bot.
