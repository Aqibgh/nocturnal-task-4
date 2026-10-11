"""Loads config.toml. The scenario clock ("now") can be overridden with RECON_NOW for replays."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]  # default; RECON_ROOT overrides it at load time


@dataclass
class Config:
    now: datetime
    tz: ZoneInfo
    data_dir: Path
    extractions_dir: Path
    state_dir: Path
    out_dir: Path
    extraction: dict
    reconcile: dict
    notify: dict
    schedule: dict
    unit_aliases: dict[str, str]
    unit_base: dict[str, dict]
    supplier_aliases: dict[str, str] = field(default_factory=dict)  # lowercased spelling -> code

    def base_unit(self, dimension: str | None) -> str | None:
        """The unit with factor 1 in a dimension (e.g. "m" for length): what converted quantities are shown in."""
        return next(
            (u for u, b in self.unit_base.items() if b["dimension"] == dimension and math.isclose(b["factor"], 1.0)),
            None,
        )

    @property
    def period(self) -> str:
        """The month being reconciled: the one before 'now' (month-end close)."""
        first = self.now.date().replace(day=1)
        prev_last = date.fromordinal(first.toordinal() - 1)
        return prev_last.strftime("%Y-%m")

    def period_dir(self, period: str) -> Path:
        return self.data_dir / period


def load(path: Path | None = None) -> Config:
    root = Path(os.environ.get("RECON_ROOT", ROOT))
    path = path or root / "config.toml"
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    tz = ZoneInfo(raw.get("timezone", "Asia/Karachi"))
    now = datetime.fromisoformat(os.environ.get("RECON_NOW", raw["now"]))
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    p = raw["paths"]
    suppliers = {}
    for code, names in raw.get("suppliers", {}).items():
        for name in names:
            suppliers[_key(name)] = code
    return Config(
        now=now.astimezone(tz),
        tz=tz,
        data_dir=root / p["data"],
        extractions_dir=root / p["extractions"],
        state_dir=root / p["state"],
        out_dir=root / p["out"],
        extraction=raw["extraction"],
        reconcile=raw["reconcile"],
        notify=raw["notify"],
        schedule=raw.get("schedule", {}),
        unit_aliases={k.lower(): v for k, v in raw["units"]["aliases"].items()},
        unit_base=raw["units"].get("base", {}),
        supplier_aliases=suppliers,
    )


def _key(name: str) -> str:
    return " ".join(name.lower().split())
