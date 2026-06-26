#!/usr/bin/env python3
"""social_pipeline — CLI entry point for prismatic.social.

Usage
-----
::

    python3 -m scripts.social.social_pipeline daily           # full daily batch
    python3 -m scripts.social.social_pipeline select          # stage 1 only
    python3 -m scripts.social.social_pipeline caption         # stage 2 (needs --photos)
    python3 -m scripts.social.social_pipeline publish-due     # stage 4 only
    python3 -m scripts.social.social_pipeline status          # queue summary
    python3 -m scripts.social.social_pipeline reset --confirm # wipe queue

Exit codes
----------
* ``0`` — pipeline ran (even if zero posts selected or zero due).
* ``2`` — configuration error (missing required env, bad path).
* ``3`` — runtime error that the caller should retry.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Make the package importable when invoked directly from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prismatic.social import (  # noqa: E402  (sys.path tweak above)
    CaptionGenerator,
    PhotoSelector,
    QueueStore,
    SocialPipeline,
    load_config,
)
from prismatic.social.exceptions import ConfigError, SocialPipelineError  # noqa: E402

log = logging.getLogger("social_pipeline")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def _build_parser() -> argparse.ArgumentParser:
    _desc = "CLI entry point for prismatic.social pipeline (GRO-572)"
    p = argparse.ArgumentParser(prog="social_pipeline", description=_desc)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_daily = sub.add_parser("daily", help="Run the full daily batch.")
    p_daily.add_argument("--json", action="store_true", help="Emit JSON report only.")

    p_select = sub.add_parser("select", help="Stage 1 only: scan and pick photos.")
    p_select.add_argument("-n", type=int, default=None, help="Override daily limit.")
    p_select.add_argument("--json", action="store_true", help="Emit JSON list only.")

    p_caption = sub.add_parser("caption", help="Stage 2 only.")
    p_caption.add_argument(
        "--photos", required=True, help="JSON list of {path,tags,caption_seed}"
    )
    p_caption.add_argument("--json", action="store_true")

    p_pub = sub.add_parser("publish-due", help="Stage 4 only: publish any due posts.")

    p_status = sub.add_parser("status", help="Print queue status summary.")
    p_status.add_argument("--json", action="store_true")

    p_reset = sub.add_parser("reset", help="Wipe the queue file.")
    p_reset.add_argument("--confirm", action="store_true")

    for sp in (p_daily, p_select, p_pub, p_status):
        sp.add_argument("-v", "--verbose", action="store_true")

    p.add_argument("-v", "--verbose", action="store_true")
    return p


def cmd_daily(args: argparse.Namespace) -> int:
    cfg = load_config()
    pipeline = SocialPipeline(config=cfg)
    report = pipeline.run_daily_batch()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"selected={len(report.selected)} captioned={len(report.captioned)} "
            f"queued={len(report.queued)} attempted={len(report.attempted)} "
            f"posted={len(report.posted)} failed={len(report.failed)} "
            f"dry_run={report.dry_run}"
        )
        if report.failed:
            print("FAILURES:", file=sys.stderr)
            for pid in report.failed:
                p = pipeline.queue.get(pid)
                if p:
                    print(f"  {pid}: {p.error}", file=sys.stderr)
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    cfg = load_config()
    sel = PhotoSelector(
        cfg, previously_posted_ids=QueueStore.load(cfg.queue_path).posted_photo_ids()
    )
    photos = sel.select(args.n or cfg.daily_limit)
    if args.json:
        print(json.dumps([p.to_dict() for p in photos], indent=2))
    else:
        for p in photos:
            print(f"{p.path}\ttags={','.join(p.tags)}\tscore={p.score:.3f}")
    return 0


def cmd_caption(args: argparse.Namespace) -> int:
    cfg = load_config()
    from prismatic.social.models import Photo

    raw = json.loads(args.photos)
    photos = [Photo.from_dict(item) for item in raw]
    gen = CaptionGenerator(config=cfg)
    out = [gen.generate(p) for p in photos]  # type: ignore[attr-defined]  (kept for clarity)
    if args.json:
        print(json.dumps([c.to_dict() for c in out], indent=2))
    else:
        for c in out:
            print(c.full_text)
    return 0


def cmd_publish_due(args: argparse.Namespace) -> int:
    cfg = load_config()
    pipeline = SocialPipeline(config=cfg)
    result = pipeline.publish_due()
    payload = {
        "attempted": [p.post_id for p in result["attempted"]],
        "posted": [p.post_id for p in result["posted"]],
        "failed": [p.post_id for p in result["failed"]],
    }
    if "skipped" in result:
        payload["skipped"] = [p.post_id for p in result["skipped"]]
    print(json.dumps(payload, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    queue = QueueStore.load(cfg.queue_path)
    summary = {
        "total": len(queue),
        "by_status": {},
        "queue_path": str(cfg.queue_path),
        "dry_run": cfg.dry_run,
        "live_meta": cfg.live_meta_available(),
    }
    for p in queue.all():
        summary["by_status"][p.status.value] = (
            summary["by_status"].get(p.status.value, 0) + 1
        )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"queue: {cfg.queue_path} ({len(queue)} posts)")
        for k, v in sorted(summary["by_status"].items()):
            print(f"  {k}: {v}")
        print(f"  dry_run={cfg.dry_run} live_meta={cfg.live_meta_available()}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not args.confirm:
        print("refusing to reset without --confirm", file=sys.stderr)
        return 2
    if cfg.queue_path.exists():
        cfg.queue_path.unlink()
    print(f"removed {cfg.queue_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    try:
        if args.cmd == "daily":
            return cmd_daily(args)
        if args.cmd == "select":
            return cmd_select(args)
        if args.cmd == "caption":
            return cmd_caption(args)
        if args.cmd == "publish-due":
            return cmd_publish_due(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "reset":
            return cmd_reset(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except SocialPipelineError as e:
        print(f"pipeline error: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
