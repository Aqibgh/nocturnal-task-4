"""One reconciliation run: extract -> load -> reconcile -> prove -> report -> notify."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import audit, extract, notify, proof, reconcile, report, sources
from .config import Config
from .extract import PageResult
from .models import OUTCOMES, SourceRow

REVIEW_LINK = "http://localhost:8501 (review page)"


@dataclass
class RunResult:
    run_id: str
    period: str
    message: str
    proof_ok: bool
    review_count: int
    out_dir: Path
    delivery: str
    complete: bool


# ---------------------------------------------------------------- review decisions


def resolutions_path(cfg: Config, period: str) -> Path:
    return cfg.state_dir / "resolutions" / f"{period}.json"


def load_resolutions(cfg: Config, period: str) -> dict[str, dict]:
    p = resolutions_path(cfg, period)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_resolution(cfg: Config, period: str, item_id: str, value: str, by: str, note: str = "") -> None:
    if not by.strip():
        raise ValueError("a resolution needs the reviewer's name")
    value = sources.validate_resolution(item_id.rsplit(":", 1)[-1], value)
    res = load_resolutions(cfg, period)
    res[item_id] = {"value": value, "by": by, "note": note, "at": cfg.now.isoformat()}
    p = resolutions_path(cfg, period)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=2), encoding="utf-8")
    audit.log(cfg, "review.resolved", period=period, item=item_id, value=value, by=by, note=note)


# ---------------------------------------------------------------- the run


def _inputs(cfg: Config, period: str) -> tuple[list[Path], dict[str, Path]]:
    d = cfg.period_dir(period)
    pages = sorted(p for p in (d / "register").glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    return pages, {"erp": d / "erp_grn.csv", "sheet": d / "store_sheet.xlsx"}


def fingerprint(cfg: Config, period: str) -> str:
    """Changes whenever any input changes: a photo, the ERP export, the sheet, or a review decision."""
    pages, files = _inputs(cfg, period)
    digest = hashlib.sha256()
    for p in [*pages, *files.values(), resolutions_path(cfg, period)]:
        if p.exists():
            digest.update(p.name.encode())
            digest.update(p.read_bytes())
    return f"{period}-{digest.hexdigest()[:8]}"


def _load(
    cfg: Config, period: str, pages: list[PageResult], files: dict[str, Path], resolutions: dict
) -> tuple[list[SourceRow], list[str]]:
    rows, problems = sources.load_register(cfg, pages, period, resolutions)
    loaders = {"erp": sources.load_erp, "sheet": sources.load_sheet}
    names = {"erp": "ERP export", "sheet": "store sheet"}
    for key, load in loaders.items():
        if files[key].exists():
            rows += load(cfg, files[key], period)
        else:
            problems.append(f"{names[key]} ({files[key].name}) not received")
    return rows, problems


def run(cfg: Config, period: str | None = None, send: bool = True) -> RunResult:
    period = period or cfg.period
    page_paths, files = _inputs(cfg, period)
    run_id = fingerprint(cfg, period)
    audit.log(cfg, "run.start", run=run_id, period=period, pages=[p.name for p in page_paths])

    pages = extract.extract_pages(cfg, page_paths, run_id)
    resolutions = load_resolutions(cfg, period)
    rows, problems = _load(cfg, period, pages, files, resolutions)
    if not page_paths:
        problems.insert(0, "no register photos found")

    groups, gaps = reconcile.reconcile(cfg, rows)
    pf = proof.build(cfg, groups, rows, files, pages, resolutions)
    data = report.RunData(run_id, period, cfg.now, pages, groups, gaps, pf, problems)
    message = report.summary(data, REVIEW_LINK)
    out = cfg.out_dir / period
    report.write_all(out, data, message)

    review_count = sum(len(r.review) for g in groups for r in [*g.rows.values(), *g.extra_rows])
    audit.log(
        cfg,
        "run.done",
        run=run_id,
        proof_ok=pf.ok,
        complete=data.complete,
        review_items=review_count,
        outcomes={o: len(data.of(o)) for o in OUTCOMES},
        gaps=gaps,
        failures=pf.failures,
    )
    delivery = notify.send(cfg, period, message, run_id) if send else "not sent (--no-send)"
    return RunResult(run_id, period, message, pf.ok, review_count, out, delivery, data.complete)
