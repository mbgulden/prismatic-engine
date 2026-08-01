"""CLI handler for merge-factory commands."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from prismatic.core.merge_factory import MergeFactoryStore, get_authenticated_principal


def _get_token() -> str:
    # 1. Try narrow environment variable
    token = os.environ.get("PRISMATIC_BEARER_TOKEN")
    if token:
        return token

    # 2. Try token file
    token_file_env = os.environ.get("PRISMATIC_TOKEN_FILE")
    token_file_path = token_file_env or os.path.expanduser("~/.prismatic_token")

    if os.path.exists(token_file_path):
        stat_info = os.stat(token_file_path)
        # Enforce mode 0600 or stricter (no group/other access)
        if os.name != "nt" and (stat_info.st_mode & 0o077) != 0:
            raise PermissionError(
                f"Token file {token_file_path} permissions are too open. Must be mode 0600 or stricter."
            )
        with open(token_file_path, "r", encoding="utf-8") as f:
            t = f.read().strip()
            if t:
                return t

    raise PermissionError(
        "Authentication token not found. Please set PRISMATIC_BEARER_TOKEN or configure a mode-0600 token file at ~/.prismatic_token."
    )


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="prismatic merge-factory",
        description="Manage cohorts, leases, locks, and attestations",
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    # Cohort
    cohort = subparsers.add_parser("cohort", help="Manage admission cohort")
    cohort_sub = cohort.add_subparsers(dest="cohort_cmd")
    c_add = cohort_sub.add_parser("add", help="Add issue to cohort")
    c_add.add_argument("issue_id", help="Issue ID")
    c_add.add_argument("stage", type=int, choices=[1, 2, 3], help="Stage")
    c_add.add_argument("sequence", type=int, help="Priority sequence")
    c_add.add_argument(
        "--allow-update",
        action="store_true",
        help="Explicitly allow updating existing non-leased cohort",
    )

    cohort_sub.add_parser("list", help="List cohort issues")

    c_status = cohort_sub.add_parser("status", help="Update issue status")
    c_status.add_argument("issue_id", help="Issue ID")
    c_status.add_argument(
        "status",
        choices=["PENDING", "ADMITTED", "COMPLETED", "EXCLUDED"],
        help="New status",
    )

    # Policy
    policy = subparsers.add_parser("policy", help="Manage operator policy")
    policy_sub = policy.add_subparsers(dest="policy_cmd")
    policy_sub.add_parser("get", help="Get policy")
    p_set = policy_sub.add_parser("set", help="Set policy")
    p_set.add_argument(
        "--stage-cap", type=int, choices=[1, 2, 3], help="Global stage cap"
    )
    p_set.add_argument(
        "--cron-paused", choices=["true", "false"], help="Is cron paused"
    )

    # Lease
    lease = subparsers.add_parser("lease", help="Manage concurrency leases")
    lease_sub = lease.add_subparsers(dest="lease_cmd")
    l_acq = lease_sub.add_parser("acquire", help="Acquire a lease")
    l_acq.add_argument("issue_id", help="Issue ID")
    l_acq.add_argument("stage", type=int, help="Stage")
    l_acq.add_argument("ttl", type=int, help="TTL in seconds")

    l_hb = lease_sub.add_parser("heartbeat", help="Heartbeat/renew a lease")
    l_hb.add_argument("issue_id", help="Issue ID")
    l_hb.add_argument("lease_id", help="Lease ID")

    l_rel = lease_sub.add_parser("release", help="Release a lease")
    l_rel.add_argument("issue_id", help="Issue ID")
    l_rel.add_argument("lease_id", help="Lease ID")

    # Judge
    judge = subparsers.add_parser("judge", help="George merge judge attestation")
    judge_sub = judge.add_subparsers(dest="judge_cmd")
    j_attest = judge_sub.add_parser("attest", help="Submit a decision attestation")
    j_attest.add_argument("issue_id", help="Issue ID")
    j_attest.add_argument(
        "decision",
        choices=["APPROVE_MERGE", "REPAIR", "REJECT", "SUPERSEDED", "MANUAL_REVIEW"],
        help="Decision",
    )
    j_attest.add_argument("base_sha", help="Base commit SHA")
    j_attest.add_argument("candidate_sha", help="Candidate commit SHA")
    j_attest.add_argument("manifest_digest", help="Manifest SHA-256 digest")
    j_attest.add_argument("evidence_digest", help="Evidence SHA-256 digest")
    j_attest.add_argument("repository", help="Repository name")
    j_attest.add_argument("target", help="Target branch/destination name")

    j_val = judge_sub.add_parser("validate", help="Validate a candidate approval")
    j_val.add_argument("issue_id", help="Issue ID")
    j_val.add_argument("base_sha", help="Base commit SHA")
    j_val.add_argument("candidate_sha", help="Candidate commit SHA")
    j_val.add_argument("manifest_digest", help="Manifest SHA-256 digest")
    j_val.add_argument("evidence_digest", help="Evidence SHA-256 digest")
    j_val.add_argument("repository", help="Repository name")
    j_val.add_argument("target", help="Target branch/destination name")

    # Lock
    lock = subparsers.add_parser("lock", help="Manage merge locks")
    lock_sub = lock.add_subparsers(dest="lock_cmd")
    lk_acq = lock_sub.add_parser("acquire", help="Acquire a merge lock")
    lk_acq.add_argument("repository", help="Repository name")
    lk_acq.add_argument("target", help="Target name")
    lk_acq.add_argument("issue_id", help="Issue ID")
    lk_acq.add_argument("base_sha", help="Base commit SHA")
    lk_acq.add_argument("candidate_sha", help="Candidate commit SHA")
    lk_acq.add_argument("manifest_digest", help="Manifest digest")
    lk_acq.add_argument("evidence_digest", help="Evidence digest")
    lk_acq.add_argument("approval_attestation_id", help="Approval attestation ID")
    lk_acq.add_argument("ttl", type=int, help="TTL in seconds")

    lk_hb = lock_sub.add_parser("heartbeat", help="Heartbeat a merge lock")
    lk_hb.add_argument("repository", help="Repository name")
    lk_hb.add_argument("target", help="Target name")
    lk_hb.add_argument("issue_id", help="Issue ID")
    lk_hb.add_argument("base_sha", help="Base commit SHA")
    lk_hb.add_argument("candidate_sha", help="Candidate commit SHA")
    lk_hb.add_argument("manifest_digest", help="Manifest digest")
    lk_hb.add_argument("evidence_digest", help="Evidence digest")
    lk_hb.add_argument("approval_attestation_id", help="Approval attestation ID")

    lk_rel = lock_sub.add_parser("release", help="Release a merge lock")
    lk_rel.add_argument("repository", help="Repository name")
    lk_rel.add_argument("target", help="Target name")
    lk_rel.add_argument("issue_id", help="Issue ID")

    args = parser.parse_args(argv)
    store = MergeFactoryStore()

    try:
        if args.subcommand == "cohort":
            if args.cohort_cmd == "add":
                p = get_authenticated_principal(_get_token())
                res = store.add_to_cohort(
                    args.issue_id, args.stage, args.sequence, p, args.allow_update
                )
                print(f"Added issue {args.issue_id} to cohort: {res}")
            elif args.cohort_cmd == "status":
                p = get_authenticated_principal(_get_token())
                res = store.update_cohort_status(args.issue_id, args.status, p)
                print(
                    f"Updated status of issue {args.issue_id} to {args.status}: {res}"
                )
            elif args.cohort_cmd == "list":
                cohort_items = store.get_cohort()
                for item in cohort_items:
                    print(
                        f"Issue: {item['issue_id']}, Stage: {item['stage']}, Status: {item['status']}, Sequence: {item['sequence']}"
                    )
            else:
                cohort.print_help()

        elif args.subcommand == "policy":
            if args.policy_cmd == "get":
                print(store.get_policy())
            elif args.policy_cmd == "set":
                p = get_authenticated_principal(_get_token())
                update = {}
                if args.stage_cap is not None:
                    update["stage_cap"] = args.stage_cap
                if args.cron_paused is not None:
                    update["cron_paused"] = args.cron_paused == "true"
                res = store.set_policy(update, p)
                print(f"Updated policy: {res}")
            else:
                policy.print_help()

        elif args.subcommand == "lease":
            if args.lease_cmd == "acquire":
                p = get_authenticated_principal(_get_token())
                res = store.acquire_lease(args.issue_id, args.stage, args.ttl, p)
                print(f"Acquired lease: {res}")
            elif args.lease_cmd == "heartbeat":
                p = get_authenticated_principal(_get_token())
                res = store.heartbeat_lease(args.issue_id, args.lease_id, p)
                print(f"Heartbeated lease: {res}")
            elif args.lease_cmd == "release":
                p = get_authenticated_principal(_get_token())
                store.release_lease(args.issue_id, args.lease_id, p)
                print(f"Released lease for {args.issue_id}")
            else:
                lease.print_help()

        elif args.subcommand == "judge":
            if args.judge_cmd == "attest":
                p = get_authenticated_principal(_get_token())
                res = store.submit_attestation(
                    issue_id=args.issue_id,
                    decision=args.decision,
                    base_sha=args.base_sha,
                    candidate_sha=args.candidate_sha,
                    manifest_digest=args.manifest_digest,
                    evidence_digest=args.evidence_digest,
                    repository=args.repository,
                    target=args.target,
                    principal=p,
                )
                print(f"Attestation recorded: {res}")
            elif args.judge_cmd == "validate":
                res = store.validate_approval(
                    issue_id=args.issue_id,
                    base_sha=args.base_sha,
                    candidate_sha=args.candidate_sha,
                    manifest_digest=args.manifest_digest,
                    evidence_digest=args.evidence_digest,
                    repository=args.repository,
                    target=args.target,
                )
                print(f"Validation result: {res}")
            else:
                judge.print_help()

        elif args.subcommand == "lock":
            if args.lock_cmd == "acquire":
                p = get_authenticated_principal(_get_token())
                res = store.acquire_lock(
                    repository=args.repository,
                    target=args.target,
                    issue_id=args.issue_id,
                    base_sha=args.base_sha,
                    candidate_sha=args.candidate_sha,
                    manifest_digest=args.manifest_digest,
                    evidence_digest=args.evidence_digest,
                    approval_attestation_id=args.approval_attestation_id,
                    ttl_seconds=args.ttl,
                    principal=p,
                )
                print(f"Acquired lock: {res}")
            elif args.lock_cmd == "heartbeat":
                p = get_authenticated_principal(_get_token())
                res = store.heartbeat_lock(
                    repository=args.repository,
                    target=args.target,
                    issue_id=args.issue_id,
                    base_sha=args.base_sha,
                    candidate_sha=args.candidate_sha,
                    manifest_digest=args.manifest_digest,
                    evidence_digest=args.evidence_digest,
                    approval_attestation_id=args.approval_attestation_id,
                    principal=p,
                )
                print(f"Heartbeated lock: {res}")
            elif args.lock_cmd == "release":
                p = get_authenticated_principal(_get_token())
                store.release_lock(args.repository, args.target, args.issue_id, p)
                print(f"Released lock for {args.repository}:{args.target}")
            else:
                lock.print_help()
        else:
            parser.print_help()
            return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0
