"""`prismatic receipt ...` — user-visible signed run receipts.

Thin presentation layer over :mod:`prismatic.verification.run_receipt`:
``show``/``list``/``verify`` only read the receipts log; ``issue`` builds a
receipt from an existing agent run record, signs it, and persists it. No
harness imports, no run execution — emission is explicit and inert by
default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from prismatic.run_records import AgentRunRecordStore
from prismatic.verification.run_receipt import (
    PUBLIC_KEY_FILE,
    PUBLIC_KEY_FILE_ENV,
    default_run_receipts_path,
    find_run_receipts,
    from_run_record,
    persist_run_receipt,
    render_receipt_text,
    sign_run_receipt,
    verify_run_receipt_signature,
)


def register_receipt_commands(
    subparsers: "argparse._SubParsersAction",
) -> None:
    """Register the ``receipt`` subcommand group."""
    receipt = subparsers.add_parser(
        "receipt",
        help="Show, list, issue, and verify signed run receipts (proof-of-done)",
        description=(
            "Every agent run can end in a readable, signed run receipt: what "
            "it did, what it verified (exact checks), what it could not "
            "verify, and a DONE / NOT DONE verdict no agent can fake."
        ),
    )
    receipt_sub = receipt.add_subparsers(dest="receipt_command")

    show = receipt_sub.add_parser("show", help="Show one run receipt")
    show.add_argument(
        "run_id",
        help="Run id, receipt id, or 'latest'",
    )
    show.add_argument("--json", action="store_true", help="Emit the raw receipt JSON")

    lst = receipt_sub.add_parser("list", help="List recent run receipts")
    lst.add_argument("--limit", type=int, default=10, help="Max receipts to show")
    lst.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    issue = receipt_sub.add_parser(
        "issue", help="Issue a signed receipt for a completed agent run"
    )
    issue.add_argument("--run-id", required=True, help="Agent run record id")
    issue.add_argument("--harness", default="", help="Harness id (e.g. agy-cli)")
    issue.add_argument("--model", default="", help="Model id, when known")
    issue.add_argument(
        "--cost-usd", type=float, default=None, help="Run cost in USD, when known"
    )
    issue.add_argument("--notes", default="", help="Operator notes on the receipt")

    verify = receipt_sub.add_parser(
        "verify", help="Verify a run receipt's Ed25519 signature"
    )
    verify.add_argument("receipt_id", help="Receipt id to verify")
    verify.add_argument(
        "--public-key",
        default=None,
        help="PEM public key file "
        f"(default: ${PUBLIC_KEY_FILE_ENV} or {PUBLIC_KEY_FILE})",
    )


def _resolve_log() -> Path:
    return default_run_receipts_path()


def _find_receipt(identifier: str) -> dict | None:
    receipts = find_run_receipts(log_path=_resolve_log(), limit=1000)
    if identifier == "latest":
        return receipts[-1] if receipts else None
    for receipt in receipts:
        if receipt.get("run_id") == identifier or receipt.get("receipt_id") == (
            identifier
        ):
            return receipt
    return None


def _cmd_show(args: argparse.Namespace) -> int:
    receipt = _find_receipt(args.run_id)
    if receipt is None:
        print(f"no run receipt found for {args.run_id!r}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(render_receipt_text(receipt), end="")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    receipts = find_run_receipts(log_path=_resolve_log(), limit=args.limit)
    if args.json:
        print(json.dumps(receipts, indent=2))
        return 0
    if not receipts:
        print("no run receipts recorded")
        return 0
    for receipt in receipts:
        done = receipt.get("done_gate_result") == "done"
        mark = "DONE ✔" if done else "NOT DONE ✘"
        sig = receipt.get("signature_or_attestation") or {}
        signed = "signed" if sig.get("value") else "unsigned"
        print(
            f"{receipt.get('receipt_id', '?')[:8]}  {mark}  "
            f"{receipt.get('verification_status', '?')}  "
            f"{receipt.get('agent_name', '?')}  {signed}  "
            f"run={str(receipt.get('run_id', '?'))[:8]}"
        )
    return 0


def _cmd_issue(args: argparse.Namespace) -> int:
    store = AgentRunRecordStore()
    record = store.get_run(args.run_id)
    if record is None:
        print(f"no agent run record {args.run_id!r}", file=sys.stderr)
        return 1
    receipt = from_run_record(
        record,
        harness=args.harness,
        model=args.model,
        cost_usd=args.cost_usd,
        notes=args.notes,
    )
    sign_run_receipt(receipt)
    receipt_id = persist_run_receipt(receipt)
    if not receipt_id:
        print("failed to persist run receipt", file=sys.stderr)
        return 1
    print(render_receipt_text(receipt), end="")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    receipts = find_run_receipts(log_path=_resolve_log(), limit=1000)
    receipt = next(
        (r for r in receipts if r.get("receipt_id") == args.receipt_id), None
    )
    if receipt is None:
        print(f"no run receipt {args.receipt_id!r}", file=sys.stderr)
        return 1
    key_path = args.public_key or os.environ.get(
        PUBLIC_KEY_FILE_ENV, str(PUBLIC_KEY_FILE)
    )
    try:
        pem = Path(key_path).read_text(encoding="utf-8")
    except OSError:
        print(f"public key not found: {key_path}", file=sys.stderr)
        print(
            "hint: sign with an ephemeral key and keep its .pub file, or set "
            f"${PUBLIC_KEY_FILE_ENV}",
            file=sys.stderr,
        )
        return 1
    ok, reason = verify_run_receipt_signature(receipt, pem)
    if ok:
        print(f"signature VALID  receipt={args.receipt_id}")
        return 0
    print(f"signature INVALID ({reason})  receipt={args.receipt_id}")
    return 1


def run_receipt(args: argparse.Namespace) -> int:
    """Dispatch ``prismatic receipt <subcommand>``."""
    cmd = getattr(args, "receipt_command", None)
    if cmd == "show":
        return _cmd_show(args)
    if cmd == "list":
        return _cmd_list(args)
    if cmd == "issue":
        return _cmd_issue(args)
    if cmd == "verify":
        return _cmd_verify(args)
    print("usage: prismatic receipt {show|list|issue|verify}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> None:
    """Standalone entry (primarily for tests); the real entry is via cli."""
    parser = argparse.ArgumentParser(prog="prismatic-receipt")
    subparsers = parser.add_subparsers(dest="command")
    register_receipt_commands(subparsers)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "receipt":
        sys.exit(run_receipt(args))
    parser.print_help()
    sys.exit(2)
