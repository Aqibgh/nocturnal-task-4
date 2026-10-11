"""Load the three sources into SourceRow objects.

Rules:
- Normalisation (spellings, units, date formats) is explicit and table-driven (config.toml).
- Anything that cannot be normalised with certainty becomes a ReviewItem. Nothing is guessed.
- Every input row produces exactly one SourceRow, including blank-GRN rows and duplicates, so the
  completeness proof can count them.
"""

from __future__ import annotations

import calendar
import csv
import re
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .config import Config
from .extract import PageResult
from .models import REGISTER_FIELDS, Cell, RegisterRow, ReviewItem, SourceRow

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}
EXCEL_EPOCH = date(1899, 12, 30)


# ---------------------------------------------------------------- shared helpers


def to_number(value: Any) -> float | None:
    """'1,200' -> 1200.0; anything that is not a number -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def canon_unit(cfg: Config, raw: Any) -> str | None:
    return cfg.unit_aliases.get(str(raw or "").strip().lower())


def item_key(text: str | None) -> str | None:
    """Comparison key for item descriptions. Letter O and digit 0 are folded together because
    handwriting does not distinguish them (register 'H202' == ERP 'H2O2')."""
    if not text:
        return None
    return re.sub(r"[^a-z0-9]", "", text.lower()).replace("o", "0")


def items_match(a: str | None, b: str | None, threshold: float) -> bool:
    if a is None or b is None:
        return a == b
    return a == b or SequenceMatcher(None, a, b).ratio() >= threshold


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def flag(row: SourceRow, field: str, reason: str, raw: Any = "", suggestion: str | None = None) -> None:
    """Send one field of one row to a person. At most one item per field; never for a resolved field."""
    if field in row.resolved or any(ri.field == field for ri in row.review):
        return
    photo = row.photo or {}
    row.review.append(
        ReviewItem(
            item_id=f"{row.row_id}:{field}",
            row_id=row.row_id,
            field=field,
            reason=reason,
            raw="" if raw is None else str(raw),
            suggestion=suggestion,
            page=photo.get("page"),
            y_top=photo.get("y_top"),
            y_bottom=photo.get("y_bottom"),
        )
    )


def _set_supplier(cfg: Config, row: SourceRow, raw: Any) -> None:
    row.supplier_code = cfg.supplier_aliases.get(" ".join(str(raw or "").lower().split()))
    if row.supplier_code is None:
        flag(row, "supplier", f"supplier spelling {raw!r} is not in the supplier list", raw)


def _set_qty_unit(cfg: Config, row: SourceRow, raw_qty: Any, raw_unit: Any, positive: bool = False) -> None:
    row.qty = to_number(raw_qty)
    if row.qty is None or (positive and row.qty <= 0):
        row.qty = None
        if not _blank(raw_qty):
            flag(row, "qty", "quantity is not a number" + (" above zero" if positive else ""), raw_qty)
    if _blank(raw_unit):
        flag(row, "unit", "unit is blank")
        return
    row.unit_raw = str(raw_unit)
    row.unit = canon_unit(cfg, raw_unit)
    if row.unit is None:
        flag(row, "unit", f"unknown unit {raw_unit!r} (add it to config.toml if it is valid)", raw_unit)
        return
    base = cfg.unit_base.get(row.unit)
    row.dimension = base["dimension"] if base else row.unit
    row.base_unit = cfg.base_unit(row.dimension) or row.unit
    if row.qty is not None:
        row.qty_base = round(row.qty * (base["factor"] if base else 1.0), 4)


def _set_common(cfg: Config, row: SourceRow, item: Any, lot: Any, period: str) -> None:
    row.item = None if _blank(item) else str(item)
    row.item_key = item_key(row.item)
    row.lot = None if _blank(lot) else str(lot).strip().upper()
    if row.date is None:
        return
    if row.date.strftime("%Y-%m") != period:
        row.notes.append(f"dated {row.date:%d %b %Y}, outside {period}")
    if row.date.weekday() not in cfg.reconcile.get("working_days", [0, 1, 2, 3, 4, 5]):
        row.notes.append(f"dated {row.date:%a %d %b}, a non-working day")


# ---------------------------------------------------------------- ERP


def load_erp(cfg: Config, path: Path, period: str) -> list[SourceRow]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = [_erp_row(cfg, line, rec, period) for line, rec in enumerate(csv.DictReader(f), start=2)]
    users = [r.raw.get("entered_by") for r in rows]
    usual = max(set(users), key=users.count) if users else None
    for r in rows:
        if r.raw.get("entered_by") != usual:
            r.warnings.append(f"entered by '{r.raw.get('entered_by')}', not the usual '{usual}'")
    return rows


def _erp_row(cfg: Config, line: int, rec: dict, period: str) -> SourceRow:
    row = SourceRow(row_id=f"erp:line{line:02d}", source="erp", raw=dict(rec))
    m = re.fullmatch(r"GRN-?(\d+)", (rec.get("grn_no") or "").strip())
    if m:
        row.grn = int(m.group(1))
    else:
        flag(row, "grn", "GRN number missing or unreadable", rec.get("grn_no"))
    try:
        row.date = date.fromisoformat((rec.get("grn_date") or "").strip())
    except ValueError:
        flag(row, "date", "date is not YYYY-MM-DD", rec.get("grn_date"))
    row.supplier_code = (rec.get("supplier_code") or "").strip() or None
    if row.supplier_code is None:
        _set_supplier(cfg, row, rec.get("supplier_name"))
    _set_qty_unit(cfg, row, rec.get("qty"), rec.get("uom"))
    _set_common(cfg, row, rec.get("item_desc"), rec.get("lot_no"), period)
    _note_late_entry(row, rec.get("entered_at"), period)
    return row


def _note_late_entry(row: SourceRow, entered_at: str | None, period: str) -> None:
    """Keyed after month-end, or long after the goods arrived."""
    try:
        keyed = datetime.fromisoformat((entered_at or "").strip())
    except ValueError:
        return
    if keyed.strftime("%Y-%m") != period:
        row.warnings.append(f"keyed in on {keyed:%d %b %H:%M}, after month-end")
    elif row.date and (keyed.date() - row.date) > timedelta(days=2):
        row.warnings.append(f"keyed in {(keyed.date() - row.date).days} days after receipt")


# ---------------------------------------------------------------- store sheet


def _sheet_date(value: Any, period: str) -> tuple[date | None, str | None]:
    """Returns (date, problem). Excel serials and real dates are fine. Text dates are accepted only
    if exactly one reading (day-first or month-first) falls inside the period."""
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    if isinstance(value, int | float):
        return EXCEL_EPOCH + timedelta(days=int(value)), None
    text = str(value or "").strip()
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", text)
    if not m:
        return None, f"date {text!r} could not be read"
    a, b, y = (int(g) for g in m.groups())
    y = y + 2000 if y < 100 else y
    readings = set()
    for d, mth in ((a, b), (b, a)):
        try:
            cand = date(y, mth, d)
        except ValueError:
            continue
        if cand.strftime("%Y-%m") == period:
            readings.add(cand)
    if len(readings) == 1:
        return readings.pop(), None
    return None, f"date {text!r} is ambiguous (day/month order)"


def read_sheet(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """Non-empty rows of the first worksheet as (excel_row_number, {header: value})."""
    ws = load_workbook(path, data_only=True).worksheets[0]
    header = [str(c.value or "").strip().lower() for c in ws[1]]
    out = []
    for cells in ws.iter_rows(min_row=2):
        values = [c.value for c in cells]
        if all(_blank(v) for v in values):
            continue
        out.append((cells[0].row, {h: v for h, v in zip(header, values, strict=False) if h}))
    return out


def load_sheet(cfg: Config, path: Path, period: str) -> list[SourceRow]:
    return [_sheet_row(cfg, n, rec, period) for n, rec in read_sheet(path)]


def _sheet_row(cfg: Config, excel_row: int, rec: dict[str, Any], period: str) -> SourceRow:
    row = SourceRow(row_id=f"sheet:row{excel_row:02d}", source="sheet", raw=rec)
    grn = to_number(rec.get("grn"))
    row.grn = int(grn) if grn is not None else None
    row.date, problem = _sheet_date(rec.get("date"), period)
    if problem:
        flag(row, "date", problem, rec.get("date"))
    elif isinstance(rec.get("date"), str):
        row.notes.append(f"date typed as text {rec['date']!r} (read as {row.date:%d %b %Y})")
    _set_supplier(cfg, row, rec.get("supplier"))
    _set_qty_unit(cfg, row, rec.get("qty received"), rec.get("uom"))
    _set_common(cfg, row, rec.get("material"), rec.get("lot"), period)
    if not _blank(rec.get("remarks")):
        row.notes.append(f"remark: {rec['remarks']}")
    return row


# ---------------------------------------------------------------- register


def load_register(
    cfg: Config, pages: list[PageResult], period: str, resolutions: dict[str, dict]
) -> tuple[list[SourceRow], list[str]]:
    """Returns (rows, page_problems). A reviewer's resolution replaces a cell's value before
    normalisation, and the cell is then trusted."""
    rows: list[SourceRow] = []
    problems: list[str] = []
    for page in pages:
        if page.error or not page.passes:
            problems.append(f"{page.page}: not read ({page.error})")
            continue
        problems += _page_problems(page, period)
        page_rows = [_register_row(cfg, page, idx, period, resolutions) for idx in range(len(page.passes[0].rows))]
        # GRNs on a page should run in sequence; a jump suggests a missed or misread row.
        grns = [r.grn for r in page_rows if r.grn]
        problems += [f"{page.page}: GRN jumps from {a} to {b}" for a, b in pairwise(grns) if b != a + 1]
        rows += page_rows
    return rows, problems


def page_number(page: PageResult) -> str:
    return re.sub(r"\D", "", page.passes[0].page_no) or page.page


def _page_problems(page: PageResult, period: str) -> list[str]:
    main = page.passes[0]
    problems = []
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s*(\d{4})", main.month_label or "")
    if not m or (int(m.group(2)), MONTHS.get(m.group(1).lower())) != (int(period[:4]), int(period[5:])):
        problems.append(f"{page.page}: header says '{main.month_label}', expected {period}")
    if any(len(p.rows) != len(main.rows) for p in page.passes[1:]):
        problems.append(f"{page.page}: the reads found different numbers of rows")
    return problems


def _register_row(cfg: Config, page: PageResult, idx: int, period: str, resolutions: dict) -> SourceRow:
    xr: RegisterRow = page.passes[0].rows[idx]
    row = SourceRow(
        row_id=f"reg:p{page_number(page)}:r{xr.row_index:02d}",
        source="register",
        raw={f: getattr(xr, f).value for f in REGISTER_FIELDS} | {"page": page.page},
        photo={"page": page.page, "y_top": xr.y_top, "y_bottom": xr.y_bottom},
    )
    min_conf = float(cfg.extraction.get("min_confidence", 0.85))
    cells: dict[str, str | None] = {}
    for f in REGISTER_FIELDS:
        resolution = resolutions.get(f"{row.row_id}:{f}")
        if resolution:
            row.resolved[f] = resolution
            cells[f] = resolution["value"]
            continue
        cell: Cell = getattr(xr, f)
        cells[f] = cell.value
        second_reads = {getattr(p.rows[idx], f).value for p in page.passes[1:] if idx < len(p.rows)}
        reasons = _doubts(cell, second_reads, min_conf)
        if reasons and f != "recd_by":
            flag(row, f, "; ".join(reasons), cell.raw_text, suggestion=cell.value)
    _register_values(cfg, row, cells, period)
    return row


def _doubts(cell: Cell, second_reads: set, min_conf: float) -> list[str]:
    """Why a person should look at this cell (empty list = trust it)."""
    if cell.value is None or "illegible" in cell.flags:
        return ["unreadable"]
    reasons = []
    if {"struck_through", "overwritten"} & set(cell.flags):
        reasons.append("corrected by hand")
    if "ambiguous" in cell.flags:
        reasons.append("could be read more than one way")
    if cell.confidence < min_conf:
        reasons.append(f"low confidence ({cell.confidence:.2f})")
    if second_reads and second_reads != {cell.value}:
        reasons.append(f"the reads disagree ({cell.value!r} vs {', '.join(map(repr, second_reads))})")
    return reasons


def _register_date(text: str, period: str) -> date | None:
    """Register dates are written day-first (DD/MM), the year comes from the page header."""
    m = re.fullmatch(r"(\d{1,2})\s*[/.-]\s*(\d{1,2})(?:\s*[/.-]\s*(\d{2,4}))?", text.strip())
    if not m:
        return None
    year = int(m.group(3)) if m.group(3) else int(period[:4])
    try:
        return date(year + 2000 if year < 100 else year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _register_values(cfg: Config, row: SourceRow, cells: dict[str, str | None], period: str) -> None:
    grn = to_number(cells["grn"])
    if grn is not None and grn == int(grn):
        row.grn = int(grn)
    elif cells["grn"] is not None:
        flag(row, "grn", "GRN is not a number", cells["grn"], suggestion=cells["grn"])

    if cells["date"]:
        row.date = _register_date(cells["date"], period)
        if row.date is None:
            flag(row, "date", "not a valid DD/MM date", cells["date"], suggestion=cells["date"])

    if cells["supplier"]:
        _set_supplier(cfg, row, cells["supplier"])
    _set_qty_unit(cfg, row, cells["qty"], cells["unit"], positive=True)
    _set_common(cfg, row, cells["item"], cells["lot"], period)
    if row.lot and not re.match(cfg.reconcile.get("lot_pattern", r".*"), row.lot):
        flag(row, "lot", "lot number does not fit the usual pattern", cells["lot"], suggestion=cells["lot"])
