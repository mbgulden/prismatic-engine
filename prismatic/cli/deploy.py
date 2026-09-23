"""`prismatic deploy ...` — deploy registry onboarding + portal control commands.

Thin presentation layer: ``add-repo``/``list-repos``/``validate-repo``/
``remove-repo`` delegate to :mod:`pe.deploy.onboard` (formats results,
prints exactly once, maps failures to exit codes -- no business logic
here). The control commands (``trigger``, ``rollback``, ``history``,
``releases``) are thin HTTP clients of the gateway's deploy control API
(``/api/deploys*``); they carry the operator Bearer <redacted> and print the
API's answer, nothing more.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from pe.deploy import onboard

#: Gateway base URL when --gateway / $PRISMATIC_GATEWAY_URL are unset.
DEFAULT_GATEWAY_URL = "http://127.0.0.1:9000"


class _ApiError(Exception):
    """A failed gateway API call (HTTP status or unreachable gateway)."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}" if status else detail)


def _gateway_base(args: argparse.Namespace) -> str:
    return (
        getattr(args, "gateway", None) or os.environ.get("PRISMATIC_GATEWAY_URL", "")
    ).strip() or DEFAULT_GATEWAY_URL


def _control_token(args: argparse.Namespace) -> str | None:
    """Operator Bearer <redacted> for mutating control calls (fail-closed: none -> None)."""
    token = (
        getattr(args, "token", None) or os.environ.get("PRISMATIC_CONTROL_TOKEN", "")
    ).strip()
    if not token:
        print(
            "[Deploy] no operator token: pass --token or set PRISMATIC_CONTROL_TOKEN",
            file=sys.stderr,
        )
        return None
    return token


def _api_token(args: argparse.Namespace) -> str | None:
    """Optional token for read-only calls (the gateway exempts GET from auth)."""
    token = (
        getattr(args, "token", None) or os.environ.get("PRISMATIC_CONTROL_TOKEN", "")
    ).strip()
    return token or None


def _api_request(
    method: str,
    gateway: str,
    path: str,
    token: str | None = None,
    payload: dict | None = None,
    timeout: int = 60,
) -> dict:
    """Minimal stdlib JSON client for the gateway control API."""
    url = gateway.rstrip("/") + path
    data: bytes | None = None
    if method.upper() == "GET":
        if payload:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in payload.items() if v is not None}
            )
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            pass
        raise _ApiError(exc.code, detail or str(exc.reason)) from exc
    except Exception as exc:
        raise _ApiError(0, f"cannot reach gateway at {gateway}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _ApiError(0, f"gateway returned non-JSON: {raw[:200]}") from exc
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _add_gateway_opts(p: argparse.ArgumentParser, *, mutating: bool) -> None:
    p.add_argument(
        "--gateway",
        default=None,
        help=(
            "Gateway base URL (default: $PRISMATIC_GATEWAY_URL or "
            f"{DEFAULT_GATEWAY_URL})"
        ),
    )
    p.add_argument(
        "--token",
        default=None,
        help=(
            "Operator Bearer <redacted> "
            + (
                "(required; default: $PRISMATIC_CONTROL_TOKEN)"
                if mutating
                else "(optional for reads; default: $PRISMATIC_CONTROL_TOKEN)"
            )
        ),
    )


def register_deploy_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``deploy`` subcommand group."""
    add_p = subparsers.add_parser(
        "add-repo",
        help="Onboard a repo onto this receiver's deploy registry",
        description=(
            "Validate owner/repo, check it is reachable, register it in the "
            "deploy registry file, clone the mirror, generate and store a "
            "per-repo HMAC secret, then print the exact GitHub-side steps "
            "(workflow, runner, secrets) still needing a human. "
            "Never deploys anything."
        ),
    )
    add_p.add_argument("full_name", help="Repo to onboard, as owner/repo")
    add_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Probe reachability and report the plan; change nothing",
    )
    add_p.add_argument(
        "--no-mirror",
        action="store_true",
        help="Skip the git mirror clone (register + secret only)",
    )
    add_p.add_argument(
        "--repo-url",
        default="",
        help="Mirror source override (default https://github.com/<owner/repo>.git)",
    )
    add_p.add_argument(
        "--target-service",
        default="",
        help="Override the systemd target service (default: registry default)",
    )
    add_p.add_argument(
        "--release-prefix",
        default="",
        help="Release-dir/live-link prefix (default: derived from owner/repo)",
    )
    add_p.add_argument(
        "--port",
        type=int,
        default=0,
        help="HTTP port the repo's service listens on (default: 9000)",
    )
    add_p.add_argument(
        "--extras",
        default=None,
        help="pip extras to install, or empty string for none "
        "(default: registry default)",
    )
    add_p.add_argument(
        "--smoke-import",
        default="",
        help="Python import used as the post-restart smoke test "
        "(default: prismatic.gateway.server)",
    )
    add_p.add_argument(
        "--registry-file",
        default="",
        help="Registry file override for PRISMATIC_DEPLOY_REPOS_FILE",
    )
    add_p.set_defaults(deploy_command="add-repo")

    subparsers.add_parser(
        "list-repos",
        help="Show every repo in the deploy registry and its readiness",
    ).set_defaults(deploy_command="list-repos")

    val_p = subparsers.add_parser(
        "validate-repo",
        help="Dry-run proof that a repo would deploy (no side effects)",
    )
    val_p.add_argument("full_name", help="Repo to validate, as owner/repo")
    val_p.set_defaults(deploy_command="validate-repo")

    rm_p = subparsers.add_parser(
        "remove-repo",
        help="Remove a repo from the deploy registry (artifacts stay in place)",
        description=(
            "Delete the repo's entry from the deploy registry file. The git "
            "mirror, the per-repo HMAC secret, and existing release dirs are "
            "left in place and reported. Refuses to empty the registry."
        ),
    )
    rm_p.add_argument("full_name", help="Repo to remove, as owner/repo")
    rm_p.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )
    rm_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed; change nothing",
    )
    rm_p.add_argument(
        "--registry-file",
        default="",
        help="Registry file override for PRISMATIC_DEPLOY_REPOS_FILE",
    )
    rm_p.set_defaults(deploy_command="remove-repo")

    trig_p = subparsers.add_parser(
        "trigger",
        help="Trigger a deploy for a repo via the gateway control API",
        description=(
            "Ask the gateway to deploy a repo (operator role). The gateway "
            "signs the trigger and hands it to the deploy receiver -- the "
            "same path as the post-merge webhook. Returns immediately "
            "(202); follow up with `prismatic deploy history`."
        ),
    )
    trig_p.add_argument("--repo", required=True, help="Repo to deploy, as owner/repo")
    trig_p.add_argument(
        "--ref",
        default=None,
        help="Ref to deploy: commit SHA or branch (default: origin/main HEAD)",
    )
    trig_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the deploy pipeline with zero side effects",
    )
    _add_gateway_opts(trig_p, mutating=True)
    trig_p.set_defaults(deploy_command="trigger")

    rb_p = subparsers.add_parser(
        "rollback",
        help="Roll a deploy back to the previous release",
        description=(
            "Restore the release that was live before the given deploy "
            "(operator role). The deploy must be the repo's latest "
            "successful one; the gateway refuses to roll back its own live "
            "release from inside itself."
        ),
    )
    rb_p.add_argument(
        "--deploy-id",
        required=True,
        help="Deploy id to roll back (see `prismatic deploy history`)",
    )
    _add_gateway_opts(rb_p, mutating=True)
    rb_p.set_defaults(deploy_command="rollback")

    hist_p = subparsers.add_parser(
        "history",
        help="Show deploy history from the gateway (sourced from alerts.log)",
    )
    hist_p.add_argument(
        "--repo", default=None, help="Filter to one repo, as owner/repo"
    )
    hist_p.add_argument(
        "--status",
        default=None,
        choices=["succeeded", "failed", "rolled_back", "dry_run"],
        help="Filter by outcome",
    )
    hist_p.add_argument(
        "--limit", type=int, default=20, help="Max entries (default: 20)"
    )
    _add_gateway_opts(hist_p, mutating=False)
    hist_p.set_defaults(deploy_command="history")

    rel_p = subparsers.add_parser(
        "releases",
        help="Show per-repo release state from the gateway (read-only)",
    )
    _add_gateway_opts(rel_p, mutating=False)
    rel_p.set_defaults(deploy_command="releases")


def _print_steps(steps: list[onboard.RepoStep]) -> None:
    glyph = {"ok": "ok", "would-do": "would-do", "skipped": "skipped"}
    for step in steps:
        print(f"  [{glyph.get(step.status, step.status)}] {step.name}: {step.detail}")


def run_deploy(args: argparse.Namespace) -> int:
    """Dispatch ``prismatic deploy ...``. Returns the process exit code."""
    command = getattr(args, "deploy_command", None)
    if command == "add-repo":
        options = onboard.AddRepoOptions(
            full_name=args.full_name,
            dry_run=args.dry_run,
            no_mirror=args.no_mirror,
            repo_url=args.repo_url,
            target_service=args.target_service,
            registry_file=args.registry_file,
            release_prefix=args.release_prefix,
            port=args.port,
            extras=args.extras,
            smoke_import=args.smoke_import,
        )
        try:
            result = onboard.add_repo(options)
        except onboard.OnboardError as exc:
            print(f"[Deploy] add-repo FAILED: {exc}", file=sys.stderr)
            return 1
        dry = " (dry run — nothing changed)" if args.dry_run else ""
        print(f"[Deploy] onboarded {result.full_name}{dry}")
        _print_steps(result.steps)
        if result.secret_value:
            # Shown ONCE, here. Never logged, never written anywhere else.
            print()
            print(f"  Receiver secret var : {result.secret_env_var}")
            print(f"  One-time secret     : {result.secret_value}")
            print("  Store the one-time secret as the repo's Actions secret,")
            print("  then it cannot be recovered from here again.")
        print(result.checklist)
        return 0

    if command == "list-repos":
        rows = onboard.list_repos()
        print(f"[Deploy] deploy registry: {len(rows)} repo(s)")
        for row in rows:
            mirror = "mirror-ok" if row.mirror_present else "mirror-missing"
            print(
                f"  {row.full_name}  service={row.target_service} "
                f"node={row.target_node}  {mirror}  secret={row.secret}"
            )
        return 0

    if command == "validate-repo":
        result = onboard.validate_repo(args.full_name)
        status = "VALID" if result.ok else "INVALID"
        print(f"[Deploy] {result.full_name}: {status}")
        for check in result.checks:
            glyph = "ok" if check.ok else "FAIL"
            print(f"  [{glyph}] {check.name}: {check.detail}")
        return 0 if result.ok else 1

    if command == "remove-repo":
        full_name = args.full_name.strip()
        if not args.yes and not args.dry_run:
            print(f"[Deploy] remove {full_name} from the deploy registry?")
            print("  Mirror, HMAC secret, and release dirs stay in place.")
            try:
                answer = input("  Type the repo name to confirm: ").strip()
            except EOFError:
                answer = ""
            if answer != full_name:
                print("[Deploy] remove-repo cancelled", file=sys.stderr)
                return 1
        try:
            result = onboard.remove_repo(
                full_name,
                dry_run=args.dry_run,
                registry_file=args.registry_file,
            )
        except onboard.OnboardError as exc:
            print(f"[Deploy] remove-repo FAILED: {exc}", file=sys.stderr)
            return 1
        dry = " (dry run — nothing changed)" if args.dry_run else ""
        print(f"[Deploy] removed {result.full_name}{dry}")
        _print_steps(result.steps)
        return 0

    if command == "trigger":
        token = _control_token(args)
        if token is None:
            return 1
        try:
            result = _api_request(
                "POST",
                _gateway_base(args),
                "/api/deploys",
                token=token,
                payload={
                    "repo": args.repo,
                    "ref": args.ref,
                    "dry_run": args.dry_run,
                },
            )
        except _ApiError as exc:
            print(f"[Deploy] trigger FAILED: {exc}", file=sys.stderr)
            return 1
        sha = str(result.get("pr_sha", ""))
        print(
            f"[Deploy] trigger {result.get('status')}: {result.get('repo')} "
            f"@ {sha[:12]}" + (" (dry run)" if result.get("dry_run") else "")
        )
        print(f"  trigger_id : {result.get('trigger_id')}")
        print(f"  receipt_id : {result.get('receipt_id')}")
        print(f"  follow up  : prismatic deploy history --repo {result.get('repo')}")
        return 0

    if command == "rollback":
        token = _control_token(args)
        if token is None:
            return 1
        deploy_id = args.deploy_id.strip()
        try:
            result = _api_request(
                "POST",
                _gateway_base(args),
                f"/api/deploys/{urllib.parse.quote(deploy_id, safe='')}/rollback",
                token=token,
                payload={},
            )
        except _ApiError as exc:
            print(f"[Deploy] rollback FAILED: {exc}", file=sys.stderr)
            return 1
        print(
            f"[Deploy] rollback {result.get('status')}: {deploy_id} "
            f"-> {result.get('restored_release')}"
        )
        print(f"  receipt_id : {result.get('receipt_id')}")
        return 0

    if command == "history":
        try:
            result = _api_request(
                "GET",
                _gateway_base(args),
                "/api/deploys",
                token=_api_token(args),
                payload={
                    "repo": args.repo,
                    "status": args.status,
                    "limit": args.limit,
                },
            )
        except _ApiError as exc:
            print(f"[Deploy] history FAILED: {exc}", file=sys.stderr)
            return 1
        rows = result.get("deploys", [])
        print(f"[Deploy] history: {result.get('count', len(rows))} entries")
        for row in rows:
            ts = str(row.get("timestamp", ""))[:19].replace("T", " ")
            print(
                f"  {ts}  {str(row.get('type', '')):18} "
                f"{str(row.get('repo', '')):42} "
                f"{str(row.get('pr_sha', ''))[:12]:12}  {row.get('deploy_id', '')}"
            )
        return 0

    if command == "releases":
        try:
            result = _api_request(
                "GET",
                _gateway_base(args),
                "/api/deploys/releases",
                token=_api_token(args),
            )
        except _ApiError as exc:
            print(f"[Deploy] releases FAILED: {exc}", file=sys.stderr)
            return 1
        repos = result.get("repos", [])
        print(f"[Deploy] release state: {len(repos)} repo(s)")
        for repo in repos:
            mirror = "mirror-ok" if repo.get("mirror_present") else "mirror-missing"
            print(
                f"  {repo.get('repo')}  service={repo.get('target_service')} "
                f"{mirror}  current={repo.get('current_release') or 'none'}"
            )
            for rel in (repo.get("releases") or [])[:8]:
                mark = "*" if rel.get("is_current") else " "
                ts = str(rel.get("modified", ""))[:19].replace("T", " ")
                print(f"   {mark} {rel.get('name')}  {ts}")
            latest = repo.get("latest_deploy")
            if latest:
                ts = str(latest.get("deployed_at", ""))[:19].replace("T", " ")
                print(
                    f"    latest: {latest.get('deploy_id')} "
                    f"success={latest.get('success')} at {ts}"
                )
        return 0

    print("[Deploy] unknown deploy command", file=sys.stderr)
    return 2
