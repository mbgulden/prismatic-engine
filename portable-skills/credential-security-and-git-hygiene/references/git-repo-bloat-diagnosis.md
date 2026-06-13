# Git Repo Bloat Diagnosis

When a repo is unusually large (multi-GB `.git`), diagnose the root cause before
attempting any cleanup. The wrong fix (e.g., `git gc` on a full disk) can make
things worse.

## Diagnosis Pipeline

### 1. Check overall size
```bash
du -sh .git                    # Git objects only
du -sh .                       # Working tree + .git
df -h /                        # Disk free space — critical context
```

### 2. Identify largest objects in history
```bash
git rev-list --objects --all \
  | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' \
  | awk '/^blob/ {print $3, $4}' \
  | sort -rn \
  | head -30
```

This shows the 30 largest blobs by byte size with their paths. Common culprits:
- WAV/MP4 files at 2-10MB each, committed 2+ times across history
- PNG sprite sheets at 1-5MB each
- Accidentally committed `.venv/` or `node_modules/`

### 3. Count untracked files (what's not yet committed)
```bash
git status --porcelain | grep '^?' | wc -l
```

### 4. Breakdown untracked by extension
```bash
git status --porcelain \
  | grep '^?' \
  | awk '{print $2}' \
  | grep -oP '\.[^.]+$' \
  | sort | uniq -c | sort -rn
```

### 5. Check total commits
```bash
git rev-list --count HEAD
```

## The Disk-Full Failure Cascade

When disk space runs out during a git hygiene task, you hit a specific failure
cascade. Recognize it early to avoid wasted retries:

1. **Commit succeeds locally** — `git commit` writes a small commit object, so it
   works even with minimal free space.
2. **Push fails silently** — `git push` needs to build pack files. With 0 bytes
   free, it can't create temporary objects. Git may report "Everything up-to-date"
   even though the remote is behind.
3. **`git gc` times out** — repacking 11GB of objects needs scratch space. On a
   full disk, it spins indefinitely.
4. **`git fetch` fails with "No space left on device"** — fetch-pack can't write
   index data.

### Recovery sequence

**Quick-win: clean stale tmp_pack files first.** Failed git operations leave
orphan pack files in `.git/objects/pack/tmp_pack_*` and loose tmp objects.
These are garbage — remove them immediately before any gc:

```bash
rm -f .git/objects/pack/tmp_pack_* .git/objects/*/tmp_obj_*
# Can free 50%+ of .git size instantly (6.6GB freed from 11GB in darius-star)
du -sh .git  # Verify reduction
```

1. Free non-git space: clean snaps (`sudo snap list --all | grep disabled`),
   vacuum journals (`sudo journalctl --vacuum-size=20M`),
   clean apt cache (`sudo apt-get clean`).
2. If `.git` is the bloat source, run `git gc --aggressive --prune=now` in
   background with a long timeout (`background=true, notify_on_complete=true`).
   This repacks all objects into efficient pack files.
3. If `git gc` times out or disk is critically full, fall back to
   `git filter-repo` to purge large binaries from history.
4. On a 40GB disk with 11GB `.git`, you need either disk expansion or an
   external volume mounted temporarily for the repack.

## .gitignore Patterns for AI-Generated Asset Repos

Repos that use AI asset pipelines (Imagen, Veo, Lyria, Vertex AI) accumulate
large binary outputs. The `.gitignore` should distinguish between:

- **Runtime assets** (committed — the game needs them): sprites, SFX, ambient
  audio that the game loads at runtime.
- **Generation artifacts** (gitignored — ephemeral tool output): asset catalogs
  from generation runs, test outputs, intermediate files.

```gitignore
# Generation catalogs (ephemeral tool output)
assets/ASSET_CATALOG.json
assets/VEO_ASSET_CATALOG.json
assets/*_CATALOG.json
assets/*_MANIFEST.json

# Test/draft outputs
assets/audio/test/

# Temp files from generation runs
*.tmp
*.bak
generation_logs/

# Cloudflare Wrangler state
.wrangler/
```

### Git LFS migration note

For repos where large binary assets ARE runtime-critical (sprites, audio),
Git LFS prevents history bloat:

```bash
# Track patterns for LFS
git lfs track "assets/audio/ambient/*.wav"
git lfs track "assets/audio/sfx/*.wav"
git lfs track "assets/sprites/**/*.png"
git lfs track "assets/cinematics/*.mp4"

# Migrate existing history (requires git-lfs + filter-repo)
# This rewrites history — force-push required after
```

## Real-World Example: darius-star (June 2026)

- **40GB disk, 15GB repo, 11GB `.git`**, 28 commits
- Root cause: 20 ambient WAV files at 2.6MB each, stored 2-3 times across commits
  = ~150MB of source material bloated to 11GB in git objects
- 2,746 untracked files (2,456 PNGs, 200+ audio files) from AI asset pipelines
- `.gitignore` existed but was never committed
- Commit `a2d5997` created with .gitignore + all assets but couldn't push —
  disk was 100% full
