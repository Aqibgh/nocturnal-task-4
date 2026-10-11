"""Writes the seed extraction cache for the two September register pages.

Values were transcribed by Claude (in Claude Code) looking at the photos, in exactly the schema the
live extractor must return. Re-run with provider = "anthropic" or "gemini" (after deleting these files)
to replace them with a real model read.
"""
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])

P1 = [
    ("01/09", "4401", "Qasr Pkg", "Cartons 5-ply", "L2609-001", "2000", "pcs", "B.H."),
    ("01/09", "4402", "Taraz Poly", "Polybags 12x16", "L2609-002", "25000", "pcs", "B.H."),
    ("02/09", "4403", "Zarnab Chem", "Caustic soda flakes", "L2609-003", "50", "bags", "B.H."),
    ("03/09", "4404", "Rehbar Threads", "Thread cones 40/2", "L2609-004", "600", "cones", "S.A."),
    ("04/09", "4405", "Saahil Pallets", "Wooden pallets", "L2609-005", "120", "pcs", "S.A."),
    ("05/09", "4406", "Nayab Labels", "Woven labels", "L2609-006", "10000", "pcs", "B.H."),
    ("07/09", "4407", "Qasr Pkg", "Cartons 3-ply", "L2609-007", "1500", "pcs", "B.H."),
    ("07/09", "4408", "Zarnab Chem", "H202 50%", "L2609-008", "40", "drums", "S.A."),
    ("08/09", "4409", "Taraz Poly", "Stretch film", "L2609-009", "80", "rolls", "B.H."),
    ("09/09", "4410", "Rehbar Threads", "Thread cones 20/2", "L2609-010", "450", "cones", "S.A."),
    ("10/09", "4411", "Qasr Pkg", "Cartons 5-ply", "L2609-011", "1800", "pcs", "B.H."),
    ("11/09", "4412", "Saahil Pallets", "Wooden pallets", "L2609-012", "90", "pcs", "S.A."),
    ("11/09", "4413", "Nayab Labels", "Care labels", "L2609-013", "15000", "pcs", "B.H."),
    ("12/09", "4414", "Zarnab Chem", "Soda ash", "L2609-014", "30", "bags", "S.A."),
]
P2 = [
    ("12/09", "4415", "Taraz Poly", "Polybags 10x14", "L2609-115", "30000", "pcs", "B.H."),
    ("14/09", "4416", "Qasr Pkg", "Cartons 5-ply", "L2609-116", "2200", "pcs", "B.H."),
    ("15/09", "4417", "Rehbar Threads", "Thread cones 40/2", "L2609-117", "48", "ctns", "S.A."),
    ("16/09", "4418", "Saahil Pallets", "Wooden pallets", "L2609-118", "100", "pcs", "S.A."),
    ("17/09", "4419", "Zarnab Chem", "H202 50%", "L2609-119", "66", "drums", "S.A."),
    ("18/09", "4420", "Nayab Labels", "Woven labels", "L2609-120", "12000", "pcs", "B.H."),
    ("19/09", "4421", "Taraz Poly", "Stretch film", "L2609-121", "75", "rolls", "B.H."),
    ("21/09", "4422", "Rehbar Threads", "Thread cones 20/2", "L2609-122", "500", "cones", "S.A."),
    ("22/09", "4423", "Qasr Pkg", "Cartons 3-ply", "L2609-123", "1600", "pcs", "B.H."),
    ("23/09", "4424", "Zarnab Chem", "Caustic soda flakes", "L2609-124", "45", "bags", "S.A."),
    ("24/09", "4425", "Shahtoot Weaving", 'Grey fabric 63"', "L2609-125", "1200", "yds", "B.H."),
    ("25/09", "4426", "Saahil Pallets", "Wooden pallets", "L2609-126", "140", "pcs", "S.A."),
    ("26/09", "4427", "Nayab Labels", "Care labels", "L2609-127", "9000", "pcs", "B.H."),
    ("28/09", "4428", "Taraz Poly", "Polybags 12x16 (bundles)", "L2609-128", None, "bdl", "B.H."),
]
FIELDS = ["date", "grn", "supplier", "item", "lot", "qty", "unit", "recd_by"]

# Cells that are not a plain, clear read.
SPECIAL = {
    ("2", 3, "unit"): dict(confidence=0.93, raw_text="ctns", flags=[]),
    ("2", 5, "qty"): dict(confidence=0.9, raw_text="60 struck through, 66 written above it",
                          flags=["struck_through", "overwritten"]),
    ("2", 14, "qty"): dict(confidence=0.0, raw_text="'2' followed by digits hidden under an ink blot",
                           flags=["illegible"]),
    ("1", 8, "item"): dict(confidence=0.9, raw_text="H202 50%", flags=[]),
    ("2", 5, "item"): dict(confidence=0.9, raw_text="H202 50%", flags=[]),
}


def page(rows, page_no, top0):
    out = []
    for i, vals in enumerate(rows, start=1):
        top = (top0 + 100 * (i - 1)) / 2000
        row = {"row_index": i, "y_top": round(top - 0.004, 4), "y_bottom": round(top + 0.054, 4)}
        for f, v in zip(FIELDS, vals):
            cell = {"value": v, "raw_text": v or "", "confidence": 0.97, "flags": []}
            cell.update(SPECIAL.get((page_no, i, f), {}))
            row[f] = cell
        out.append(row)
    return {"month_label": "Sep 2026", "page_no": page_no, "rows": out, "blank_rows": 2}


for name, rows, no, top0 in [("page1.jpg", P1, "1", 292), ("page2.jpg", P2, "2", 288)]:
    img = root / "data" / "2026-09" / "register" / name
    digest = hashlib.sha256(img.read_bytes()).hexdigest()
    doc = {
        "image": name,
        "sha256": digest,
        "extractor": "seed: transcribed by Claude in Claude Code from the photo (no API call)",
        "extracted_at": "2026-10-09T00:00:00+05:00",
        "passes": [page(rows, no, top0)],
    }
    (root / "extractions" / f"{digest}.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(name, digest[:12], len(rows), "rows")
