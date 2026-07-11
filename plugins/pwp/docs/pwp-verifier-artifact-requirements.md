# PWP Verifier Artifact Attachment Requirements

## Goal

PWP theme/autopilot work is not Done because an agent says it is Done. Each generated Linear issue must carry the checklist and attachment requirements that let a reviewer verify the work without reconstructing the run from chat history.

These requirements extend the Prismatic execution evidence contract for PWP theme work. They apply to every Phase 9 generated lane issue and to hand-authored PWP theme issues that expect verifier review.

## Required Linear checklist block

Every PWP theme issue must include this checklist in the issue description before dispatch:

```markdown
### Verifier artifact checklist
- [ ] Verification commands are listed with exact working directory and expected exit code.
- [ ] Build/test output artifact is attached or linked.
- [ ] Accessibility output artifact is attached or explicitly marked not applicable with reason.
- [ ] Visual/regression output artifact is attached or explicitly marked not applicable with reason.
- [ ] Contract/schema validation output is attached when schemas, tokens, modules, content, or manifests changed.
- [ ] Evidence summary states `verification_status`, `verification_scope`, `failure_category`, `cleanup_status`, and `done_gate_result`.
- [ ] Reviewer can reproduce each verifier from repo files without private credentials unless the issue is explicitly `live_integration`.
```

The checklist is intentionally mechanical. If an agent cannot check an item, the issue should remain `partially_verified`, `blocked`, or `failed`; it should not move to Done.

## Required artifacts by verifier type

| Verifier type | Required attachment/link | Minimum contents |
|---|---|---|
| Build/test | `build-log.txt`, `pytest.log`, `npm-check.log`, or equivalent | command, working directory, exit code, output excerpt |
| Accessibility | `axe-results.json`, `playwright-a11y.json`, or equivalent | page/fixture URL, rule violations, pass/fail summary |
| Visual/regression | screenshot diff, Playwright trace, image snapshot directory, or equivalent | viewport, fixture/page id, baseline/new artifact reference |
| Contract/schema | `schema-validation.json`, `theme-validate.json`, or equivalent | schema/contract version, validated files, errors/warnings |
| Deployment/provenance | deployment manifest or run-state snippet | theme id/version/hash, content hash, token hash, deploy id when available |

When a verifier is not applicable, the issue must say why in the checklist line. “Skipped” without a reason is self-reporting, not evidence.

## Evidence summary shape

Use this summary in the final Linear comment and in attached evidence files:

```text
verification_status=<verified|partially_verified|blocked|failed|self_reported>
verification_scope=<ad_hoc_targeted|canonical_full_suite|live_integration|not_run>
failure_category=<none|timeout|blocked_external_api|blocked_missing_context|verification_failed|conflict|hallucinated_claim|tooling_error>
cleanup_status=<what temp files/artifacts remain or were removed>
done_gate_result=<done|not_done>
artifacts=<repo path, artifact URL, or Linear attachment title list>
```

`done_gate_result=done` is only valid when `verification_status=verified` and the required checklist artifacts are present or explicitly not applicable.

## Lane-specific verifier expectations

| Lane | Required verifier evidence before Done |
|---|---|
| content strategist | module/page plan fixture, EmDash content/schema validation output, changed files list |
| design/theme agent | token compiler output, theme validation output, visual artifact for representative fixtures |
| Astro implementation agent | build output, module fixture render output, visual artifact, changed component list |
| accessibility verifier | axe/Playwright a11y output and page/fixture coverage list |
| SEO/schema agent | structured-data validation output, sitemap/metadata diff, page coverage list |
| deploy agent | deployment manifest/run-state snippet with theme/content/token hashes and rollback/idempotency notes |
| reviewer | diff review notes, contract drift findings, verified artifact checklist status |

## Generated-issue requirements

Task generators that create PWP Linear issues must populate:

1. exact repo files or contracts the agent may modify,
2. exact verification commands with working directory,
3. expected verifier artifacts from the table above,
4. the checklist block from this document,
5. labels for the correct implementation/verifier lane,
6. a reminder that `dispatch:ready` is not added until Michael starts the build process.

Missing artifact requirements are a generation bug. The issue may be triaged, but it should not be considered ready for autonomous dispatch until the checklist is present.
