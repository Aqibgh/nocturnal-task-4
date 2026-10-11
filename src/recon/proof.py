"""Proof of completeness.

The input side is counted straight from the files (CSV lines, non-empty sheet rows, extracted
register rows), separately from the loaders. The output side is the ledger: one line per source
row with the single outcome it landed in. The run fails loudly if the two don't tie.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .extract import PageResult
from .models import OUTCOMES, SOURCES, Group, SourceRow
from .sources import canon_unit, page_number, read_sheet, to_number

Totals = dict[str, dict[str, float]]  # source -> unit -> quantity


@dataclass
class LedgerLine:
    row_id: str
    source: str
    outcome: str
    group: str
    grn: int | None
    qty: float | None
    unit: str | None


@dataclass
class Proof:
    ledger: list[LedgerLine]
    input_rows: dict[str, int]
    ledger_rows: dict[str, int]
    by_outcome: dict[str, dict[str, int]]
    input_totals: Totals
    ledger_totals: Totals
    unreadable_qty: dict[str, int]
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass
class _Inputs:
    rows: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SOURCES, 0))
    totals: Totals = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))
    unreadable: dict[str, int] = field(default_factory=lambda: dict.fromkeys(SOURCES, 0))

    def add(self, source: str, qty, unit) -> None:
        self.rows[source] += 1
        q = to_number(qty)
        if q is None:
            self.unreadable[source] += 1
        else:
            self.totals[source][unit] += q


def build(
    cfg: Config,
    groups: list[Group],
    all_rows: list[SourceRow],
    files: dict[str, Path],
    pages: list[PageResult],
    resolutions: dict[str, dict],
) -> Proof:
    ledger = _ledger(groups)
    inputs = _count_inputs(cfg, files, pages, resolutions)
    ledger_totals: Totals = defaultdict(lambda: defaultdict(float))
    for line in ledger:
        if line.qty is not None:
            ledger_totals[line.source][line.unit or "?"] += line.qty
    by_outcome: dict[str, dict[str, int]] = {s: dict.fromkeys(OUTCOMES, 0) for s in SOURCES}
    for line in ledger:
        by_outcome[line.source][line.outcome] += 1
    ledger_rows = {s: sum(by_outcome[s].values()) for s in SOURCES}

    failures = _each_row_once(ledger, all_rows) + _ties(inputs, ledger_rows, ledger_totals)
    return Proof(
        ledger=ledger,
        input_rows=inputs.rows,
        ledger_rows=ledger_rows,
        by_outcome=by_outcome,
        input_totals={s: dict(v) for s, v in inputs.totals.items()},
        ledger_totals={s: dict(v) for s, v in ledger_totals.items()},
        unreadable_qty=inputs.unreadable,
        failures=failures,
    )


def _ledger(groups: list[Group]) -> list[LedgerLine]:
    def line(r: SourceRow, outcome: str, g: Group) -> LedgerLine:
        return LedgerLine(r.row_id, r.source, outcome, g.key, r.grn, r.qty, r.unit or r.unit_raw or "?")

    out = [line(r, g.outcome, g) for g in groups for r in g.rows.values()]
    out += [line(r, "needs_review" if r.needs_review else "duplicate", g) for g in groups for r in g.extra_rows]
    return out


def _each_row_once(ledger: list[LedgerLine], all_rows: list[SourceRow]) -> list[str]:
    seen = Counter(line.row_id for line in ledger)
    failures = [f"{row_id} landed in {n} outcomes" for row_id, n in seen.items() if n > 1]
    failures += [f"{r.row_id} landed in no outcome" for r in all_rows if r.row_id not in seen]
    return failures


def _ties(inputs: _Inputs, ledger_rows: dict[str, int], ledger_totals: Totals) -> list[str]:
    failures = []
    for s in SOURCES:
        if inputs.rows[s] != ledger_rows[s]:
            failures.append(f"{s}: {inputs.rows[s]} rows in, {ledger_rows[s]} accounted for")
        for unit in set(inputs.totals.get(s, {})) | set(ledger_totals.get(s, {})):
            a = round(inputs.totals.get(s, {}).get(unit, 0.0), 4)
            b = round(ledger_totals.get(s, {}).get(unit, 0.0), 4)
            if a != b:
                failures.append(f"{s}: {unit} total {a:g} in, {b:g} accounted for")
    return failures


def _count_inputs(
    cfg: Config, files: dict[str, Path], pages: list[PageResult], resolutions: dict[str, dict]
) -> _Inputs:
    """A missing file counts as zero rows; the run reports the missing file separately."""
    inputs = _Inputs()

    def unit(raw) -> str:
        return canon_unit(cfg, raw) or str(raw or "?")

    if files["erp"].exists():
        for rec in _erp_records(files["erp"]):
            inputs.add("erp", rec.get("qty"), unit(rec.get("uom")))
    if files["sheet"].exists():
        for _, rec in read_sheet(files["sheet"]):
            inputs.add("sheet", rec.get("qty received"), unit(rec.get("uom")))
    for qty, raw_unit in _register_cells(pages, resolutions):
        inputs.add("register", qty, unit(raw_unit))
    return inputs


def _erp_records(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [rec for rec in csv.DictReader(f) if any((v or "").strip() for v in rec.values())]


def _register_cells(pages: list[PageResult], resolutions: dict[str, dict]) -> list[tuple]:
    """(qty, unit) for every extracted register row, with reviewers' decisions applied."""
    out = []
    for p in (p for p in pages if p.passes):
        for xr in p.passes[0].rows:
            base = f"reg:p{page_number(p)}:r{xr.row_index:02d}"
            qty = resolutions.get(f"{base}:qty", {}).get("value", xr.qty.value)
            out.append((qty, resolutions.get(f"{base}:unit", {}).get("value", xr.unit.value)))
    return out
