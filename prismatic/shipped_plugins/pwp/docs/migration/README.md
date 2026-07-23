# PWP source-freeze record

This directory records the immutable **source-freeze facts** used by the PWP separate-repository extraction work. It is not extraction output and does not claim that a standalone repository, Prismatic Engine cutover, monorepo removal, stale-PR merge, or production deployment has occurred.

## Recorded source

| Field | Value |
| --- | --- |
| Source repository | `mbgulden/prismatic-engine` |
| Source commit | `7ba0716ce1027c0e9cd8741dd548cf733508ecf3` |
| Source commit tree | `9ff226bcf74aa03139b1fecf8bb0e81fc5da7371` |
| PWP subtree tree | `b2eba79ae0fed505c2e7e26e835afb6337533ba1` |
| Source path | `plugins/pwp/` |
| Files recorded | 41 |
| Manifest algorithm | SHA-256 |

`SOURCE_FREEZE_METADATA.json` is a machine-readable copy of these facts, including the UTC time at which the freeze was recorded. `SOURCE_FREEZE_SHA256SUMS.txt` contains only source-tree file paths rooted at `plugins/pwp/`; it deliberately contains no extraction-output paths.

## Reproduce the manifest

Use a clean checkout of the exact commit, not a later extraction worktree:

```bash
git clone https://github.com/mbgulden/prismatic-engine.git /tmp/prismatic-engine-freeze
cd /tmp/prismatic-engine-freeze
git checkout --detach 7ba0716ce1027c0e9cd8741dd548cf733508ecf3
git status --porcelain  # must be empty
sha256sum -c plugins/pwp/docs/migration/SOURCE_FREEZE_SHA256SUMS.txt
```

A successful check validates every recorded file byte against the source commit. The manifest is intentionally scoped to the source freeze and is not a claim about files added in later extraction phases.
