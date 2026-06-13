# Git Repo Bloat from AI-Generated Game Assets — Diagnosis & Fix

## Symptoms
- `du -sh .git/` shows 10GB+ for a repo that should be ~1-2GB
- `git push` times out after 60-120 seconds (large pack upload)
- `git gc` also times out
- `git count-objects -vH` shows "garbage found: .git/objects/pack/tmp_pack_*" — failed pack operations left behind
- `size-pack: 0 bytes` but `count: 4000+` — objects never got packed

## Root Cause
When AI-generated binary assets (sprites, audio WAVs, sprite sheets) are committed in bulk without `.gitignore` filtering, git stores every version of every file as loose objects. A single session that generates 300+ sprite files (100 frames × 3 biomes, 60-frame SFX sheets) and commits them can bloat `.git` from 200MB to 11GB.

## Fix: Aggressive GC with Pruning

```bash
# Step 1: Check garbage count
git count-objects -vH

# Step 2: Aggressive repack + prune all garbage
git gc --aggressive --prune=now

# If gc times out (common for 10GB+ repos):
# Step 2b: Prune loose objects first, then gc
git prune
git repack -ad
git gc --aggressive

# Step 3: Verify
du -sh .git/
git count-objects -vH  # Should show 0 garbage, packs present

# Step 4: Push should now work
git push
```

## Prevention: Git LFS for AI Asset Repos

For any repo that stores AI-generated binary assets:
```bash
# Install Git LFS
git lfs install

# Track all binary asset types
git lfs track "assets/sprites/**/*.png"
git lfs track "assets/audio/**/*.wav"
git lfs track "assets/audio/**/*.mp3"

# Commit .gitattributes
git add .gitattributes
git commit -m "Configure Git LFS for binary assets"
```

## Workaround: Direct API Deploy

When git push is blocked and you need to deploy NOW:
- **Cloudflare Pages**: Use `POST /accounts/:id/pages/projects/:name/deployments` with the HTML file as a multipart upload — bypasses git entirely
- **Vercel/Netlify**: Similar direct deployment APIs exist

## Detection Script
```bash
#!/bin/bash
REPO_SIZE=$(du -sh .git/ | cut -f1)
GARBAGE=$(git count-objects -vH 2>/dev/null | grep "garbage" | wc -l)
if [[ "$GARBAGE" -gt 0 ]]; then
  echo "⚠️  Repo has $GARBAGE garbage objects (size: $REPO_SIZE) — run git gc --aggressive --prune=now"
fi
```
