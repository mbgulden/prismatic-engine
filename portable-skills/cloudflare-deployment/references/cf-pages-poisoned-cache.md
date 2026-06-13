# CF Pages Poisoned Cache Bug

## Symptom

After fixing a build failure (oversized image, broken filename, etc.), the next deploy's build log shows:

```
Uploading... (N/N)
✨ Success! Uploaded 0 files (N already uploaded) (0.33 sec)
✨ Upload complete!
Success: Assets published!
Success: Your site was deployed!
```

Despite "Success," pages return HTTP 404. Only pages from the *last genuinely successful deployment* (before the failure) work.

## Root Cause

When a CF Pages build FAILS at asset validation (e.g., file >25MB), Cloudflare caches the file hashes for whatever WAS partially uploaded before the failure. Subsequent successful builds compare file hashes against this poisoned cache, find matches, and report "Uploaded 0 files (already uploaded)." The deployment is marked successful but the CDN only serves files from the last *complete* deployment — the poisoned cache entries are orphans.

This persists across multiple pushes because the hashes are server-side and content-independent — changing file contents does not help.

## Detection

Compare two consecutive deployment logs:

- **Healthy:** `Uploaded N files (0 already uploaded)` — all files fresh
- **Poisoned:** `Uploaded 0 files (N already uploaded)` — nothing changed on CDN

If you see `0 uploaded` after fixing a build error and pages are 404, the cache is poisoned.

## Fix

**Try first:** Cloudflare Dashboard action:

1. Workers & Pages → project → Settings → Builds & deployments
2. Scroll to **"Clear build cache"** (or "Purge cache")
3. If this section says "Build cache is not enabled" or the clear doesn't help, skip to the nuclear option below.
4. Deployments tab → ⋮ on latest → **Retry deployment**

After clearing, the next build will show `Uploaded N files (0 already uploaded)` and all pages work.

**Nuclear option (when \"Clear build cache\" is disabled or doesn't work):** Delete and recreate the project:

1. Workers & Pages → project → **Settings** → scroll to bottom → **Delete project** (confirm)
2. **Create** → **Pages** → **Connect to Git** → same repo → same branch
3. Build output directory: `site` (or whatever it was)
4. **Save and Deploy**

This forces a completely fresh deployment with zero cache. All 2,000+ files upload fresh. Takes 60 seconds. This was the ONLY fix that worked in the Active Oahu Tours session — the build cache clearing option wasn't available on the free plan.

## What Does NOT Work

- Pushing more commits — they all hash-match the same poisoned cache
- Changing file content (adding comments, timestamps) — CF hashing is not content-based for the upload step
- Waiting for CDN propagation — the files were never actually uploaded
- Using the API to trigger a deploy — the API is blocked (7003) on accounts where this bug occurs

## Prevention

- Run image compression BEFORE the first deploy — a >25MB file on deploy #1 poisons the cache immediately
- Run filename cleanup (`references/wp-mirror-filename-cleanup.md`) before deploying
- Remove all `.tmp` files before committing
- After ANY failed deploy, clear the build cache before pushing the fix
