"""Three-way match on GRN number. Every source row ends in exactly one outcome."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from .config import Config
from .models import SOURCE_LABEL, SOURCES, Group, Mismatch, SourceRow
from .sources import flag, items_match

COMPARED = ("date", "supplier", "item", "lot", "qty")


def reconcile(cfg: Config, rows: list[SourceRow]) -> tuple[list[Group], list[int]]:
    groups: list[Group] = []
    by_grn: dict[int, dict[str, list[SourceRow]]] = defaultdict(lambda: defaultdict(list))

    unkeyed = []
    for r in rows:
        if r.grn is None:
            unkeyed.append(r)
        else:
            by_grn[r.grn][r.source].append(r)

    for grn in sorted(by_grn):
        groups.append(_group(cfg, grn, by_grn[grn]))

    lots = {r.lot: r.grn for r in rows if r.grn and r.lot}
    groups += [_unkeyed_group(r, lots) for r in unkeyed]

    known = sorted(by_grn)
    gaps = [n for n in range(known[0], known[-1] + 1) if n not in by_grn] if known else []
    return groups, gaps


def _unkeyed_group(r: SourceRow, lots: dict[str, int]) -> Group:
    """A row with no GRN: a negative quantity is an adjustment (e.g. a return); anything else needs a person."""
    label = SOURCE_LABEL[r.source]
    if r.qty is not None and r.qty < 0:
        linked = lots.get(r.lot) if r.lot else None
        g = Group(key=f"adj:{r.row_id}", grn=None, outcome="adjustment", rows={r.source: r})
        g.notes.append(
            f"{label}: {r.qty:g} {r.unit or ''} with no GRN"
            + (f"; same lot as GRN {linked}, so likely a return against it" if linked else "")
        )
        g.notes += [f"{label}: {n}" for n in r.notes]
        return g
    flag(r, "grn", "row has no GRN number", r.raw)
    return Group(key=f"unkeyed:{r.row_id}", grn=None, outcome="needs_review", rows={r.source: r})


def _group(cfg: Config, grn: int, per_source: dict[str, list[SourceRow]]) -> Group:
    g = Group(key=f"grn:{grn}", grn=grn, outcome="matched")
    for src in SOURCES:
        found = per_source.get(src, [])
        if found:
            _place(g, src, found)
        else:
            g.missing_from.append(src)

    g.mismatches = _compare(cfg, g.rows)
    for src, r in g.rows.items():
        g.notes += [f"{SOURCE_LABEL[src]}: {n}" for n in r.notes]
        g.warnings += [f"{SOURCE_LABEL[src]}: {w}" for w in r.warnings]
        for ri in r.review:
            ri.others = {SOURCE_LABEL[s]: _show(o, ri.field) for s, o in g.rows.items() if s != src}

    if any(r.needs_review for r in [*g.rows.values(), *g.extra_rows]):
        g.outcome = "needs_review"
    elif g.missing_from:
        g.outcome = "missing"
    elif g.mismatches:
        g.outcome = "mismatched"
    return g


def _place(g: Group, src: str, found: list[SourceRow]) -> None:
    """First row is the source's entry for this GRN; any further rows are duplicates (set aside)
    or conflicting second entries (sent to a person)."""
    primary, *rest = found
    g.rows[src] = primary
    for extra in rest:
        g.extra_rows.append(extra)
        if _same(primary, extra):
            g.notes.append(
                f"{SOURCE_LABEL[src]}: GRN {g.grn} entered twice ({primary.row_id} and {extra.row_id}); "
                "second copy not counted"
            )
        else:
            flag(extra, "grn", f"a second, different entry for GRN {g.grn} ({primary.row_id})", extra.raw)


def _same(a: SourceRow, b: SourceRow) -> bool:
    def identity(r: SourceRow) -> tuple:
        return (r.date, r.supplier_code, r.item_key, r.lot, r.qty, r.unit)

    return identity(a) == identity(b)


def _reviewed(r: SourceRow, field: str) -> bool:
    return any(ri.field == field or (field == "qty" and ri.field == "unit") for ri in r.review)


def _qty_text(r: SourceRow) -> str:
    if r.qty is None:
        return "?"
    txt = f"{r.qty:g} {r.unit or r.unit_raw or ''}".strip()
    if r.qty_base is not None and r.base_unit != r.unit:
        txt += f" (= {r.qty_base:g} {r.base_unit})"
    return txt


_DISPLAY = {
    "date": lambda r: f"{r.date:%d %b (%a)}" if r.date else "?",
    "supplier": lambda r: r.supplier_code or "?",
    "item": lambda r: r.item or "?",
    "lot": lambda r: r.lot or "?",
    "qty": _qty_text,
    "unit": _qty_text,
}


def _show(r: SourceRow, field: str) -> str:
    fmt = _DISPLAY.get(field)
    return fmt(r) if fmt else str(r.raw.get(field, "?"))


def _compare(cfg: Config, rows: dict[str, SourceRow]) -> list[Mismatch]:
    tol = float(cfg.reconcile.get("qty_tolerance", 0))
    sim = float(cfg.reconcile.get("item_similarity", 0.85))
    out = []
    for field in COMPARED:
        present = {s: r for s, r in rows.items() if not _reviewed(r, field)}
        if len(present) < 2:
            continue
        if field == "qty":
            out += _compare_qty(present, tol)
            continue
        if field == "item":
            ref = next(iter(present.values())).item_key
            differs = any(not items_match(ref, r.item_key, sim) for r in present.values())
            keys = {s: r.item_key for s, r in present.items()}
        else:
            keys = {s: _show(r, field) for s, r in present.items()}
            differs = len(set(keys.values())) > 1
        if differs:
            out.append(Mismatch(field, {s: _show(r, field) for s, r in present.items()}, _odd_one(keys)))
    return out


def _compare_qty(present: dict[str, SourceRow], tol: float) -> list[Mismatch]:
    shown = {s: _show(r, "qty") for s, r in present.items()}
    dims = {s: r.dimension for s, r in present.items()}
    if len(set(dims.values())) > 1:
        return [Mismatch("unit", shown, _odd_one(dims), hint="units cannot be converted into each other")]
    vals = {s: r.qty_base for s, r in present.items()}
    ref = next(iter(vals.values()))
    if all(v is not None and ref is not None and abs(v - ref) <= tol for v in vals.values()):
        return []
    keys = {s: round(v, 4) if v is not None else None for s, v in vals.items()}
    hint = None
    odd = _odd_one(keys)
    if odd:
        r_odd = present[odd]
        for s, r in present.items():
            if s != odd and r.qty == r_odd.qty and r.unit != r_odd.unit:
                hint = (
                    f"{SOURCE_LABEL[odd]} has the same number as {SOURCE_LABEL[s]} "
                    f"({r.qty:g}) but in {r_odd.unit}, not {r.unit}: likely a wrong unit label"
                )
    return [Mismatch("qty", shown, odd, hint)]


def _odd_one(keys: Mapping[str, object]) -> str | None:
    if len(keys) != 3:
        return None
    for s, v in keys.items():
        rest = [w for t, w in keys.items() if t != s]
        if rest[0] == rest[1] and v != rest[0]:
            return s
    return None
