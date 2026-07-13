# Release checklist

Use this checklist for every public Prismatic Engine release.

## Pre-release

- [ ] Confirm `main` is clean and up to date.
- [ ] Confirm no unexpected open release-blocking PRs.
- [ ] Pick the next SemVer version.
- [ ] Update `pyproject.toml` `[project].version`.
- [ ] Update `prismatic.__version__`.
- [ ] Update `CHANGELOG.md` with a dated release section.
- [ ] Update `docs/migrations.md` if state, config, API, or plugin contracts changed.
- [ ] Update `docs/upgrade-guide.md` if operator actions changed.
- [ ] Run `python scripts/release_check.py`.
- [ ] Run `python scripts/release_smoke.py`.
- [ ] Run `python scripts/public_security_readiness_audit.py`.
- [ ] Run `python -m build`.
- [ ] Run `python -m twine check dist/*`.
- [ ] Open and merge a release-prep PR with green CI.

## Tag

- [ ] Create an annotated tag: `git tag -a vX.Y.Z -m "Prismatic Engine vX.Y.Z"`.
- [ ] Push the tag: `git push origin vX.Y.Z`.
- [ ] Watch the publish workflow.
- [ ] Confirm wheel/sdist artifacts were built.
- [ ] Confirm `twine check` passed.
- [ ] Confirm provenance attestation was created.

## Post-release

- [ ] Verify package install in a fresh virtualenv.
- [ ] Run `python scripts/release_smoke.py` against the installed package or clean checkout.
- [ ] Confirm docs/changelog point to the released version.
- [ ] Create the GitHub release notes from `CHANGELOG.md`.
- [ ] Announce known migration notes and caveats.

## Rollback

- [ ] If publication is incomplete, cancel the workflow and delete the bad tag if it did not publish.
- [ ] If the package published but is broken, prefer a patch release over deleting history.
- [ ] Yank on PyPI only for actively harmful releases.
- [ ] Add the incident and recovery note to `CHANGELOG.md` and `docs/migrations.md`.
