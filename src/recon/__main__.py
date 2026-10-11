"""CLI.

python -m recon run [--period 2026-09] [--no-send]
python -m recon review                         # list open review items
python -m recon resolve <item_id> <value> --by "Name" [--note "..."]
python -m recon schedule [--once]            # monthly run (loop on live clock, or one check at "now")
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from . import config, notify, pipeline, schedule


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--period", help="YYYY-MM (default: the month before 'now')")
    ap = argparse.ArgumentParser(prog="recon")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", parents=[common], help="reconcile a month")
    r.add_argument("--no-send", action="store_true")
    sub.add_parser("review", parents=[common], help="list items waiting for a person")
    s = sub.add_parser("resolve", parents=[common], help="record a reviewer's decision, then re-run")
    s.add_argument("item_id")
    s.add_argument("value")
    s.add_argument("--by", required=True)
    s.add_argument("--note", default="")
    sc = sub.add_parser("schedule", help="monthly run: loop on the live clock, or --once at the scenario clock")
    sc.add_argument("--once", action="store_true")
    return ap


def _run(cfg: config.Config, period: str, args: argparse.Namespace) -> int:
    try:
        res = pipeline.run(cfg, period, send=not args.no_send)
    except Exception:
        notify.alert(cfg, traceback.format_exc(limit=3))
        raise
    print(
        f"run {res.run_id}: proof {'TIES' if res.proof_ok else 'DOES NOT TIE'}, "
        f"{res.review_count} item(s) for review, message {res.delivery}"
    )
    print(f"report: {res.out_dir / 'report.md'}")
    return 0 if res.proof_ok else 2


def _review(cfg: config.Config, period: str, _args: argparse.Namespace) -> int:
    path = cfg.out_dir / period / "review.json"
    items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if not items:
        print("nothing to review")
    for it in items:
        print(
            f"{it['item_id']}: {it['reason']} | written: {it['raw']!r} | "
            f"suggested: {it['suggestion']!r} | others: {it['others']}"
        )
    return 0


def _resolve(cfg: config.Config, period: str, args: argparse.Namespace) -> int:
    pipeline.save_resolution(cfg, period, args.item_id, args.value, args.by, args.note)
    res = pipeline.run(cfg, period)
    print(
        f"resolved {args.item_id} = {args.value!r}; re-run {res.run_id}: "
        f"{res.review_count} item(s) still open, message {res.delivery}"
    )
    return 0


def _schedule(cfg: config.Config, _period: str, args: argparse.Namespace) -> int:
    if args.once:
        print(schedule.tick(cfg))
    else:
        schedule.loop(cfg)
    return 0


COMMANDS = {"run": _run, "review": _review, "resolve": _resolve, "schedule": _schedule}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cfg = config.load()
    return COMMANDS[args.cmd](cfg, getattr(args, "period", None) or cfg.period, args)


if __name__ == "__main__":
    sys.exit(main())
