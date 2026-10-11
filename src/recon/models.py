"""Data shapes shared across the pipeline."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# ---------- What the vision model must return for one register page ----------

CellFlag = Literal["illegible", "struck_through", "overwritten", "ambiguous"]


class Cell(BaseModel):
    value: str | None = Field(description="Exactly what is written, or null if it cannot be read with certainty.")
    raw_text: str = Field(
        description="Everything visible in the cell, including crossed-out text, e.g. '60 (struck) 66'."
    )
    confidence: float = Field(description="0.0-1.0: how sure you are that 'value' is exactly right.")
    flags: list[CellFlag] = Field(default_factory=list)


class RegisterRow(BaseModel):
    row_index: int = Field(description="1-based position of the row on the page.")
    y_top: float | None = Field(default=None, description="Top of the row as a fraction of image height (0-1).")
    y_bottom: float | None = Field(default=None, description="Bottom of the row as a fraction of image height (0-1).")
    date: Cell
    grn: Cell
    supplier: Cell
    item: Cell
    lot: Cell
    qty: Cell
    unit: Cell
    recd_by: Cell


class PageExtraction(BaseModel):
    month_label: str = Field(description="The 'Month:' header, as written.")
    page_no: str = Field(description="The 'Page No.' header, as written.")
    rows: list[RegisterRow] = Field(description="Every row that has any writing, top to bottom. Skip fully blank rows.")
    blank_rows: int = Field(description="Number of fully blank ruled rows at the bottom of the table.")


REGISTER_FIELDS = ["date", "grn", "supplier", "item", "lot", "qty", "unit", "recd_by"]

# ---------- Normalised rows from all three sources ----------

Source = Literal["register", "erp", "sheet"]
SOURCES: tuple[str, ...] = ("register", "erp", "sheet")
SOURCE_LABEL = {"register": "Register", "erp": "ERP", "sheet": "Store sheet"}


@dataclass
class ReviewItem:
    item_id: str  # "<row_id>:<field>", the key a resolution is saved under
    row_id: str
    field: str
    reason: str
    raw: str  # what the source shows
    suggestion: str | None = None  # best candidate (never applied automatically)
    page: str | None = None  # register page image, for cropping
    y_top: float | None = None
    y_bottom: float | None = None
    others: dict[str, str] = dataclasses.field(default_factory=dict)  # what the other sources say, for the reviewer


@dataclass
class SourceRow:
    row_id: str  # stable, human-readable: reg:p2:r05, erp:line06, sheet:row21
    source: Source
    raw: dict  # the values as read, before normalisation
    grn: int | None = None
    date: date | None = None
    supplier_code: str | None = None
    item: str | None = None  # display form
    item_key: str | None = None  # comparison form
    lot: str | None = None
    qty: float | None = None  # as written
    unit: str | None = None  # canonical unit
    unit_raw: str | None = None  # as written, kept so the proof can label unknown units
    qty_base: float | None = None  # converted to the dimension's base unit (yd -> m), else == qty
    dimension: str | None = None  # same dimension => comparable after conversion
    base_unit: str | None = None  # the unit qty_base is in
    review: list[ReviewItem] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)  # info only, never changes the outcome
    warnings: list[str] = dataclasses.field(default_factory=list)  # info worth putting in the controller's message
    resolved: dict = dataclasses.field(default_factory=dict)  # field -> resolution applied
    photo: dict | None = None  # register only: page image and row position, for the reviewer

    @property
    def needs_review(self) -> bool:
        return bool(self.review)


Outcome = Literal["matched", "mismatched", "missing", "needs_review", "duplicate", "adjustment"]
OUTCOMES: tuple[str, ...] = ("matched", "mismatched", "missing", "needs_review", "duplicate", "adjustment")


@dataclass
class Mismatch:
    field: str
    values: dict[str, str]  # source -> display value
    odd_one_out: str | None = None  # the source that disagrees with the other two
    hint: str | None = None


@dataclass
class Group:
    """One GRN (or one un-keyed row) and everything the three sources say about it."""

    key: str
    grn: int | None
    outcome: Outcome
    rows: dict[str, SourceRow] = dataclasses.field(default_factory=dict)  # source -> primary row
    missing_from: list[str] = dataclasses.field(default_factory=list)
    mismatches: list[Mismatch] = dataclasses.field(default_factory=list)
    extra_rows: list[SourceRow] = dataclasses.field(default_factory=list)  # duplicates attached to this GRN
    notes: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
