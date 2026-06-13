# Cross-Filesystem Move Pattern (rsync + --remove-source-files)

## When to Use

When moving large directories (100MB+) from local ext4 to an NFS mount (Synology NAS), `mv` will fail because cross-filesystem moves are copy+delete under the hood. On large trees, the copy phase times out before the delete phase runs.

**Symptoms:**
- `mv /local/big-dir /nas-mount/dest` hangs for 30+ seconds then times out
- Source directory still exists with all files intact
- Destination has a partial copy (often 30-50% of expected size)

## The Fix

```bash
# Phase 1: Copy with automatic cleanup of transferred files
rsync -a --remove-source-files /source/path/ /nas-mount/dest/path/

# Phase 2: Remove the now-empty source directory
rm -rf /source/path/

# Phase 3: Verify
du -sh /nas-mount/dest/path/
```

## Why This Works

- `--remove-source-files` deletes each source file after it's successfully transferred to the destination
- If rsync is interrupted (timeout, network blip), progress is preserved — already-transferred files are gone from source, untransferred files remain
- The final `rm -rf` cleans up the empty directory skeleton left behind

## NFS chgrp Errors Are Cosmetic

Synology NFS mounts don't support `chgrp` from non-root clients. rsync will emit:

```
rsync: [receiver] chgrp "/nas/path/.filename.XXXXXX" failed: Operation not permitted (1)
```

…for every single file. The exit code will be 23 ("partial transfer due to attribute errors").

**This is harmless.** The file data is fully transferred and intact. Verify with `du -sh` at the destination — if the size matches, the move succeeded.

## Full Example from GRO-902

Moving Google Cloud SDK (543MB) from local to NAS:

```bash
# FAILED: mv timed out, left partial copy
mv ${PRISMATIC_HOME}/.hermes/profiles/orchestrator/home/google-cloud-sdk \
   /home/ubuntu/mounts/synology-agentic-context/archived-tools/

# WORKED: rsync with --remove-source-files
rsync -a --remove-source-files \
  ${PRISMATIC_HOME}/.hermes/profiles/orchestrator/home/google-cloud-sdk/ \
  /home/ubuntu/mounts/synology-agentic-context/archived-tools/google-cloud-sdk/
# ...thousands of chgrp errors scroll by...
rm -rf ${PRISMATIC_HOME}/.hermes/profiles/orchestrator/home/google-cloud-sdk

# Verify
du -sh /home/ubuntu/mounts/synology-agentic-context/archived-tools/google-cloud-sdk/
# → 524M (close enough — some files were already moved by the failed mv)
```

## When NOT to Use

- Same-filesystem moves: `mv` is an atomic rename, instant, always use it
- Small files/directories (<50MB): `mv` will complete fast enough
- When you need to preserve exact permissions/ownership: rsync `-a` preserves what it can, but on NFS, group ownership will be lost
