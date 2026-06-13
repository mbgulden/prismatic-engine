# Cloudflare Pages: Large Asset Build Delays & SPA Fallback

## CRITICAL: Check Git Tracking FIRST

**The #1 root cause of "assets not loading" is files on disk that were never `git add`-ed.**  In a June 2026 session, 2,260 PNGs existed on disk in `assets/sprites/` but only 3 were tracked in git. Cloudflare Pages can only deploy what's in git — disk-only files never reach the CDN, no matter how many times you push.

**Always run this diagnostic BEFORE debugging anything else:**

```bash
# Compare tracked vs disk count — if they differ, git add the missing files
echo "Tracked in git: $(git ls-files assets/ | wc -l)"
echo "On disk:        $(find assets/ -type f | wc -l)"
# If disk >> tracked, your assets aren't deploying.
git add assets/
git commit -m "ADD: missing assets from disk"
git push origin master
```

**Then wait 3-10 minutes for the Pages build to process the binary files.** Verify with `curl -sI | grep content-type` — if you get `text/html`, the build hasn't finished (or the files still aren't in the deployed commit).

## Symptom
After pushing a commit with many binary files (game sprites, audio, images), the deployed site returns `content-type: text/html` for asset URLs that should return `image/png`, `audio/mpeg`, etc. The HTML content is the SPA fallback (index.html).

## Root Cause
Cloudflare Pages auto-builds from the git repo. For commits with many new binary files (154 files, ~30MB sprites, 137MB audio in this case), the build can take **5-10+ minutes** to process. Until the build completes, the previous deploy's files are served — and any new files return the SPA fallback.

The old deploy may have only had 3 tracked sprite files, so 151 new asset paths all 404 and fall back to index.html.

## Verification Pattern

```bash
# 1. Check content-type (text/html = SPA fallback, not the real asset)
curl -sI https://project.pages.dev/assets/sprites/file.png | grep content-type

# 2. If content-type is correct, verify actual bytes — check magic header
curl -s https://project.pages.dev/assets/sprites/file.png | xxd | head -1
# PNG: 8950 4e47
# MP3: 4944 33 or fffb
# WAV: 5249 4646
# HTML: 3c21 444f (fallback!)

# 3. Retry every 60s until content-type switches to correct MIME type
```

## Fix

**Wait.** Cloudflare Pages will eventually process the commit. Force a rebuild if stuck:

```bash
git commit --allow-empty -m "force rebuild: trigger Cloudflare Pages deploy"
git push origin master
```

## Prevention

For repos with many binary assets (>50MB total), consider:
1. **Git LFS** for assets to keep clone/build fast
2. **Separate asset deployment** — host sprites/audio on a CDN bucket, not in the Pages repo
3. **Pre-commit sprite sheets** — single large files instead of hundreds of tiny ones (Cloudflare Pages has no file count limit but 25MB per-file limit)

## Audio Path Mismatch Pattern

A related failure mode: audio files exist in git AND on CDN, but the code references wrong paths. In a June 2026 session, `AMBIENT_TRACKS` referenced `ambient_abyssal_trench.mp3` in `assets/audio/` but the actual files were `ambient/ambient_b1_atmosphere.wav` in `assets/audio/ambient/` — three mismatches: wrong filename, wrong directory, wrong extension.

**Verify code-to-file alignment:**
```bash
# 1. List all audio files actually deployed
curl -sI https://project.pages.dev/assets/audio/<candidate-path> | grep content-type
# Should return audio/wav or audio/mpeg, NOT text/html

# 2. Check what git actually tracks in audio/
git ls-files assets/audio/ | sort

# 3. Cross-reference against what the code imports
grep -n "assets/audio/" index.html
```

The audio loaded successfully from CDN but was silently broken because `new Audio('404-path')` doesn't throw — it just never plays.

## Diagnostic: Check What's Actually Deployed

```bash
# Compare git-tracked vs disk files
git ls-files assets/sprites/ | wc -l    # tracked in git
ls assets/sprites/*.png | wc -l         # on disk

# Check if specific file is in git AND on disk
git ls-files assets/sprites/enemy_crawler_0.png  # empty = not tracked
ls -la assets/sprites/enemy_crawler_0.png        # file size on disk
```
