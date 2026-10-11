"""Monthly run.

Each tick: once the scheduled day and time of the month has passed, reconcile the previous month.
- Runs once per month, then again only if an input changes (new photo, corrected export, review decision).
  The message itself is also de-duplicated, so an unchanged result is never re-sent.
- A crash alerts, is retried on the next tick, and after max_attempts gives up with a final alert,
  until an input changes.
- Every tick writes state/heartbeat; the container health check fails if it goes stale.

`schedule --once` runs a single tick at the scenario clock (replayable; usable from cron/Task Scheduler).
`schedule` loops on the live clock (the Docker service).
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from datetime import time as dtime

from . import audit, notify, pipeline
from .config import Config


def due_at(cfg: Config) -> datetime:
    """This month's scheduled run time, e.g. the 1st at 09:00 local."""
    s = cfg.schedule
    hh, mm = (int(x) for x in str(s.get("time", "09:00")).split(":"))
    return datetime.combine(cfg.now.date().replace(day=int(s.get("day", 1))), dtime(hh, mm), tzinfo=cfg.tz)


def _state(cfg: Config) -> dict:
    p = cfg.state_dir / "schedule.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(cfg: Config, state: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "schedule.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def tick(cfg: Config) -> str:
    """One check. Returns what happened, for the log."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "heartbeat").write_text(cfg.now.isoformat(), encoding="utf-8")
    if cfg.now < due_at(cfg):
        return f"waiting: next run {due_at(cfg):%d %b %H:%M}"

    period = cfg.period
    inputs = pipeline.fingerprint(cfg, period)
    state = _state(cfg)
    last = state.get(period, {})
    if last.get("inputs") == inputs and (last.get("status") != "failed" or last.get("gave_up")):
        return f"{period}: up to date ({last['status']})"

    attempts = last.get("attempts", 0) + 1 if last.get("inputs") == inputs else 1
    try:
        res = pipeline.run(cfg, period)
    except Exception as exc:
        gave_up = attempts >= int(cfg.schedule.get("max_attempts", 3))
        state[period] = {
            "inputs": inputs,
            "status": "failed",
            "attempts": attempts,
            "gave_up": gave_up,
            "at": cfg.now.isoformat(),
            "error": str(exc),
        }
        _save(cfg, state)
        audit.log(cfg, "schedule.failed", period=period, attempt=attempts, gave_up=gave_up, error=str(exc))
        notify.alert(
            cfg,
            f"{period}, attempt {attempts}: {exc}\n"
            + ("Giving up until an input changes." if gave_up else "Will retry on the next check.")
            + "\n"
            + traceback.format_exc(limit=2),
        )
        return f"{period}: failed (attempt {attempts}{', gave up' if gave_up else ''})"

    status = "ok" if res.proof_ok else "incomplete"
    state[period] = {
        "inputs": inputs,
        "status": status,
        "attempts": attempts,
        "run": res.run_id,
        "at": cfg.now.isoformat(),
        "review_items": res.review_count,
        "message": res.delivery,
    }
    _save(cfg, state)
    audit.log(cfg, "schedule.ran", period=period, run=res.run_id, status=status)
    return f"{period}: ran {res.run_id} ({status}, {res.review_count} for review, message {res.delivery})"


def loop(cfg: Config) -> None:
    interval = int(cfg.schedule.get("check_every_minutes", 15)) * 60
    while True:
        cfg.now = datetime.now(cfg.tz)  # the loop runs on the live clock; --once uses the scenario clock
        try:
            outcome = tick(cfg)
        except Exception as exc:  # never let the scheduler die quietly
            notify.alert(cfg, f"scheduler check failed: {exc}")
            outcome = f"check failed: {exc}"
        print(f"[{cfg.now:%Y-%m-%d %H:%M}] {outcome}", flush=True)
        time.sleep(interval)
