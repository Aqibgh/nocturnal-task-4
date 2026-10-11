"""Turns a run into: a short controller message, a full markdown report, CSVs, and the review queue."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from .extract import PageResult
from .models import SOURCE_LABEL, SOURCES, Group, Mismatch, SourceRow
from .proof import Proof

FIELD_LABEL = {"grn": "GRN", "recd_by": "received-by"}  # every other field name reads fine as-is


@dataclass
class RunData:
    run_id: str
    period: str
    now: datetime
    pages: list[PageResult]
    groups: list[Group]
    gaps: list[int]
    proof: Proof
    problems: list[str]  # missing files and unreadable pages

    def of(self, outcome: str) -> list[Group]:
        return [g for g in self.groups if g.outcome == outcome]

    @property
    def month(self) -> str:
        return date.fromisoformat(self.period + "-01").strftime("%b %Y")

    @property
    def complete(self) -> bool:
        return self.proof.ok and not self.problems


# ---------------------------------------------------------------- shared wording


def _label(field: str) -> str:
    return FIELD_LABEL.get(field, field)


def mismatch_text(m: Mismatch) -> str:
    if m.odd_one_out:
        rest = {s: v for s, v in m.values.items() if s != m.odd_one_out}
        if len(set(rest.values())) == 1:
            agree = f"{next(iter(rest.values()))} in the other two"
        else:  # same quantity written in different units
            agree = " and ".join(f"{SOURCE_LABEL[s]} {v}" for s, v in rest.items()) + " (equal)"
        txt = f"{SOURCE_LABEL[m.odd_one_out]} {m.values[m.odd_one_out]} vs {agree}"
    else:
        txt = ", ".join(f"{SOURCE_LABEL[s]} {v}" for s, v in m.values.items())
    return f"{_label(m.field)}: {txt}" + (f" ({m.hint})" if m.hint else "")


def _all_rows(g: Group) -> list[SourceRow]:
    return [*g.rows.values(), *g.extra_rows]


def review_lines(g: Group) -> list[str]:
    out = []
    for r in _all_rows(g):
        for ri in r.review:
            label = f"GRN {g.grn}" if g.grn else ri.row_id
            seen = f" Page shows: {ri.raw}." if ri.raw and r.source == "register" else ""
            others = ", ".join(f"{k} {v}" for k, v in ri.others.items())
            out.append(
                f"{label} {SOURCE_LABEL[r.source].lower()} {_label(ri.field)}: {ri.reason}.{seen}"
                + (f" ({others})" if others else "")
            )
    return out


def _missing_line(g: Group) -> str:
    present = [s for s in SOURCES if s not in g.missing_from]
    where = (
        f"only in {SOURCE_LABEL[present[0]]}"
        if len(present) == 1
        else "not in " + " or ".join(SOURCE_LABEL[s] for s in g.missing_from)
    )
    warn = f" ({'; '.join(g.warnings)})" if g.warnings else ""
    also = "; also " + "; ".join(mismatch_text(m) for m in g.mismatches) if g.mismatches else ""
    return f"{g.grn} {where}{warn}{also}"


# ---------------------------------------------------------------- controller message


def summary(run: RunData, review_link: str) -> str:
    sections: list[Callable[[RunData], list[str]]] = [_msg_header, _msg_review, _msg_mismatch, _msg_missing, _msg_also]
    lines = [line for section in sections for line in section(run)]
    lines.append(f"\nMatched in all three: {len(run.of('matched'))}.")
    if run.of("needs_review"):
        lines.append(f"Resolve: {review_link}")
    return "\n".join(lines)


def _bullets(title: str, items: list[str]) -> list[str]:
    return [f"\n*{title} ({len(items)})*", *(f"• {i}" for i in items)] if items else []


def _msg_header(run: RunData) -> list[str]:
    n = run.proof.input_rows
    counts = f"{n['register']} register / {n['erp']} ERP / {n['sheet']} sheet rows"
    head = [f"*Store reconciliation, {run.month}*"]
    if run.complete:
        return [*head, f"{counts}. Every row accounted for."]
    return [*head, f"⚠️ {counts}. NOT COMPLETE:", *(f"• {p}" for p in run.problems + run.proof.failures)]


def _msg_review(run: RunData) -> list[str]:
    return _bullets("Needs your decision", [line for g in run.of("needs_review") for line in review_lines(g)])


def _msg_mismatch(run: RunData) -> list[str]:
    return _bullets(
        "Sources disagree", [f"{g.grn} {mismatch_text(m)}" for g in run.of("mismatched") for m in g.mismatches]
    )


def _msg_missing(run: RunData) -> list[str]:
    items = [_missing_line(g) for g in run.of("missing")]
    items += [f"{n} in no source (number skipped: cancelled or lost?)" for n in run.gaps]
    return _bullets("Missing", items)


def _msg_also(run: RunData) -> list[str]:
    items = [g.notes[0] for g in run.of("adjustment")]
    items += [
        f"Store sheet has GRN {g.grn} twice (counted once)"
        for g in run.groups
        if g.extra_rows and g.outcome != "needs_review"
    ]
    return ["\n*Also*", *(f"• {i}" for i in items)] if items else []


# ---------------------------------------------------------------- files


def write_all(out: Path, run: RunData, message: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.txt").write_text(message, encoding="utf-8")
    _write_csv(
        out / "outcomes.csv",
        ["key", "grn", "outcome", "missing_from", "mismatches", "register_row", "erp_row", "sheet_row", "notes"],
        (
            [
                g.key,
                g.grn or "",
                g.outcome,
                ";".join(g.missing_from),
                " | ".join(mismatch_text(m) for m in g.mismatches),
                *(g.rows[s].row_id if s in g.rows else "" for s in SOURCES),
                " | ".join(g.warnings + g.notes),
            ]
            for g in run.groups
        ),
    )
    _write_csv(
        out / "ledger.csv",
        ["row_id", "source", "outcome", "group", "grn", "qty", "unit"],
        (
            [
                line.row_id,
                line.source,
                line.outcome,
                line.group,
                line.grn or "",
                "" if line.qty is None else f"{line.qty:g}",
                line.unit,
            ]
            for line in run.proof.ledger
        ),
    )
    queue = [
        asdict(ri) | {"group": g.key, "grn": g.grn, "source": r.source}
        for g in run.groups
        for r in _all_rows(g)
        for ri in r.review
    ]
    (out / "review.json").write_text(json.dumps(queue, indent=2, default=str), encoding="utf-8")
    (out / "report.md").write_text(markdown(run), encoding="utf-8")


def _write_csv(path: Path, header: list[str], rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------- full report


def markdown(run: RunData) -> str:
    lines = [
        f"# Goods-received reconciliation, {run.month}",
        "",
        f"Run `{run.run_id}` at {run.now:%a %d %b %Y %H:%M %Z}.",
    ]
    for part in (_md_inputs, _md_proof, _md_outcomes, _md_observations):
        lines += ["", *part(run)]
    return "\n".join(lines) + "\n"


def _md_inputs(run: RunData) -> list[str]:
    lines = ["## Inputs", "", "| Source | File | Rows |", "|---|---|---|"]
    for p in run.pages:
        rows = len(p.passes[0].rows) if p.passes else "NOT READ"
        lines.append(f"| Register | {p.page} ({p.source}, sha256 {p.sha256[:12]}) | {rows} |")
    lines.append(f"| ERP | erp_grn.csv | {run.proof.input_rows['erp']} |")
    lines.append(f"| Store sheet | store_sheet.xlsx | {run.proof.input_rows['sheet']} |")
    if run.problems:
        lines += ["", "**Problems with the inputs:**", "", *(f"- {p}" for p in run.problems)]
    return lines


def _md_proof(run: RunData) -> list[str]:
    pf = run.proof
    lines = [
        "## Proof of completeness",
        "",
        "Every source row lands in exactly one outcome. Input counts and totals are read straight from the files.",
        "",
        "| Outcome | " + " | ".join(SOURCE_LABEL[s] for s in SOURCES) + " |",
        "|---|" + "---|" * len(SOURCES),
    ]
    lines += [f"| {o} | " + " | ".join(str(pf.by_outcome[s][o]) for s in SOURCES) + " |" for o in pf.by_outcome["erp"]]
    lines.append("| **accounted for** | " + " | ".join(f"**{pf.ledger_rows[s]}**" for s in SOURCES) + " |")
    lines.append("| **rows in the input** | " + " | ".join(f"**{pf.input_rows[s]}**" for s in SOURCES) + " |")
    lines += ["", "Quantity totals by unit (input = accounted for):", ""]
    lines += [_md_totals(pf, s) for s in SOURCES]
    lines += ["", f"**Result: {'TIES' if pf.ok else 'DOES NOT TIE'}**", *(f"- {f}" for f in pf.failures)]
    return lines


def _md_totals(pf: Proof, source: str) -> str:
    parts = []
    for unit, total in sorted(pf.input_totals.get(source, {}).items()):
        got = pf.ledger_totals.get(source, {}).get(unit, 0)
        parts.append(f"{unit} {total:g}" + (" ✓" if round(total, 4) == round(got, 4) else f" ✗ ({got:g})"))
    unread = pf.unreadable_qty.get(source, 0)
    return f"- {SOURCE_LABEL[source]}: " + ", ".join(parts) + (f"; {unread} row(s) unreadable" if unread else "")


def _md_outcomes(run: RunData) -> list[str]:
    sections: list[tuple[str, str, Callable[[Group], list[str]]]] = [
        ("Needs a person", "needs_review", review_lines),
        ("Mismatched", "mismatched", lambda g: [f"**{g.grn}** {mismatch_text(m)}" for m in g.mismatches]),
        ("Missing", "missing", lambda g: [_missing_line(g)]),
        ("Adjustments (not receipts)", "adjustment", lambda g: g.notes[:1]),
        ("Matched", "matched", lambda g: [str(g.grn)]),
    ]
    lines: list[str] = []
    for title, outcome, fmt in sections:
        groups = run.of(outcome)
        if groups:
            lines += [f"## {title} ({len(groups)})", "", *(f"- {x}" for g in groups for x in fmt(g)), ""]
    if run.gaps:
        lines += ["## GRN numbers in no source", "", *(f"- {n}" for n in run.gaps), ""]
    return lines


def _md_observations(run: RunData) -> list[str]:
    notes = [f"- {g.grn or g.key}: {n}" for g in run.groups for n in g.warnings + g.notes]
    return ["## Observations (do not change the outcome)", "", *notes] if notes else []
