"""End-to-end tests on the real September data (cached extraction, no API calls)."""

import csv
import shutil
from pathlib import Path

import pytest

from recon import config, pipeline

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path):
    c = config.load(ROOT / "config.toml")
    c.state_dir = tmp_path / "state"
    c.out_dir = tmp_path / "out"
    c.extraction["provider"] = "cached"
    c.extractions_dir = ROOT / "extractions"
    c.notify["channel"] = "console"
    return c


def outcomes(cfg):
    with (cfg.out_dir / "2026-09" / "outcomes.csv").open() as f:
        return {row["grn"]: row for row in csv.DictReader(f) if row["grn"]}


def test_scenario_clock_picks_september(cfg):
    assert cfg.period == "2026-09"


def test_september_results(cfg):
    res = pipeline.run(cfg)
    assert res.proof_ok
    o = outcomes(cfg)
    assert o["4412"]["outcome"] == "mismatched"
    assert "lot" in o["4412"]["mismatches"]
    assert o["4415"]["outcome"] == "mismatched"
    assert "date" in o["4415"]["mismatches"]
    assert o["4417"]["outcome"] == "mismatched"
    assert "46" in o["4417"]["mismatches"]
    assert o["4425"]["outcome"] == "mismatched"
    assert "wrong unit" in o["4425"]["mismatches"]
    assert o["4421"]["outcome"] == "missing"
    assert o["4421"]["missing_from"] == "erp"
    assert o["4430"]["outcome"] == "missing"
    assert o["4430"]["missing_from"] == "register;sheet"
    assert o["4419"]["outcome"] == "needs_review"
    assert o["4428"]["outcome"] == "needs_review"
    assert o["4409"]["outcome"] == "matched"  # the sheet's duplicate row is set aside
    assert o["4416"]["outcome"] == "matched"  # text date in the sheet still parses
    assert "4429 in no source" in res.message
    assert "-5000" in res.message
    assert "4402" in res.message


def test_never_guesses_the_blotted_quantity(cfg):
    pipeline.run(cfg)
    ledger = (cfg.out_dir / "2026-09" / "ledger.csv").read_text()
    reg_4428 = next(line for line in ledger.splitlines() if line.startswith("reg:p2:r14"))
    assert ",needs_review," in reg_4428
    assert ",,bdl" in reg_4428  # qty is empty, not 250


def test_review_resolves_and_reruns(cfg):
    pipeline.run(cfg)
    pipeline.save_resolution(cfg, "2026-09", "reg:p2:r14:qty", "250", by="Test Reviewer")
    pipeline.save_resolution(cfg, "2026-09", "reg:p2:r05:qty", "66", by="Test Reviewer")
    res = pipeline.run(cfg)
    o = outcomes(cfg)
    assert res.proof_ok
    assert res.review_count == 0
    assert o["4428"]["outcome"] == "matched"
    assert o["4419"]["outcome"] == "mismatched"
    assert "Store sheet 60" in o["4419"]["mismatches"]
    audit = (cfg.state_dir / "audit.jsonl").read_text()
    assert "review.resolved" in audit
    assert "Test Reviewer" in audit


def test_resolution_needs_a_name(cfg):
    with pytest.raises(ValueError):
        pipeline.save_resolution(cfg, "2026-09", "reg:p2:r14:qty", "250", by=" ")


def test_same_result_is_not_sent_twice(cfg):
    assert pipeline.run(cfg).delivery.startswith("sent")
    assert pipeline.run(cfg).delivery.startswith("skipped")


def test_new_page_without_a_model_is_reported_not_hidden(cfg, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    (data / "2026-09" / "register" / "page3.jpg").write_bytes(b"not really a photo")
    cfg.data_dir = data
    res = pipeline.run(cfg)
    assert "page3.jpg: not read" in res.message
    assert "NOT COMPLETE" in res.message


def test_missing_erp_export_still_reports(cfg, tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    (data / "2026-09" / "erp_grn.csv").unlink()
    cfg.data_dir = data
    res = pipeline.run(cfg)
    assert "ERP export (erp_grn.csv) not received" in res.message
    assert res.proof_ok  # what did arrive is still fully accounted for
