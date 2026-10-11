"""Append-only audit trail: every extraction, review decision, run and message."""

from __future__ import annotations

import json
from datetime import datetime

from .config import Config


def log(cfg: Config, event: str, **fields) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": cfg.now.isoformat(),  # scenario time, so replays line up
        "wall": datetime.now(cfg.tz).isoformat(timespec="seconds"),
        "event": event,
        **fields,
    }
    with (cfg.state_dir / "audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
