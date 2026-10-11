"""Extraction retries/caching with a fake model, and the CLI entry points."""

import json
import shutil
from pathlib import Path

import pytest

from recon import __main__ as cli
from recon import config, extract
from recon.models import PageExtraction

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in ("config.toml", "data", "extractions"):
        src = ROOT / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, tmp_path / name)
    monkeypatch.setenv("RECON_ROOT", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        config_file.read_text(encoding="utf-8")
        .replace('channel = "whatsapp_cloud"', 'channel = "console"')
        .replace('channel = "twilio_whatsapp"', 'channel = "console"')
        .replace('channel = "email"', 'channel = "console"')
        .replace('provider = "gemini"', 'provider = "cached"')
        .replace('extractions = "extractions/live"', 'extractions = "extractions"'),
        encoding="utf-8",
    )
    monkeypatch.setattr(extract.time, "sleep", lambda _s: None)
    return tmp_path


def _a_page() -> PageExtraction:
    cached = json.loads(next((ROOT / "extractions").glob("*.json")).read_text())
    return PageExtraction.model_validate(cached["passes"][0])


def test_new_page_is_read_retried_and_cached(root, monkeypatch):
    cfg = config.load()
    cfg.extraction["provider"] = "anthropic"
    calls = {"n": 0}

    def flaky(_cfg, _provider, _page):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("network blip")
        return _a_page()

    monkeypatch.setattr(extract, "_call", flaky)
    photo = root / "new.jpg"
    photo.write_bytes(b"fake photo")
    [result] = extract.extract_pages(cfg, [photo], run_id="t")
    assert result.error is None
    assert len(result.passes) == 2  # two independent reads
    assert calls["n"] == 3  # one failure retried, then two passes
    cached = json.loads((cfg.extractions_dir / f"{result.sha256}.json").read_text())
    assert cached["extractor"] == "anthropic:claude-opus-5-5"

    monkeypatch.setattr(extract, "_call", lambda *_: pytest.fail("cached page must not be re-read"))
    [again] = extract.extract_pages(cfg, [photo], run_id="t2")
    assert again.source == "cache"


def test_model_that_keeps_failing_marks_the_page_unread(root, monkeypatch):
    cfg = config.load()
    cfg.extraction["provider"] = "gemini"

    def down(*_):
        raise TimeoutError("provider down")

    monkeypatch.setattr(extract, "_call", down)
    photo = root / "new.jpg"
    photo.write_bytes(b"fake photo")
    [result] = extract.extract_pages(cfg, [photo], run_id="t")
    assert result.passes == []
    assert "provider down" in (result.error or "")
    assert "extract.failed" in (cfg.state_dir / "audit.jsonl").read_text()


def test_cli_run_review_resolve(root, capsys):
    assert cli.main(["run", "--no-send"]) == 0
    assert "proof TIES, 2 item(s) for review" in capsys.readouterr().out
    assert cli.main(["review"]) == 0
    assert "reg:p2:r14:qty" in capsys.readouterr().out
    assert cli.main(["resolve", "reg:p2:r14:qty", "250", "--by", "Tester"]) == 0
    assert "1 item(s) still open" in capsys.readouterr().out


def test_cli_alerts_and_fails_on_crash(root, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cli.pipeline, "run", boom)
    with pytest.raises(RuntimeError):
        cli.main(["run"])
    assert "run FAILED" in capsys.readouterr().out


def test_cli_missing_source_exits_incomplete_even_when_ledger_ties(root, capsys):
    (root / "data" / "2026-09" / "erp_grn.csv").unlink()
    assert cli.main(["run", "--no-send"]) == 2
    text = capsys.readouterr().out
    assert "proof TIES" in text
    assert "run INCOMPLETE" in text


def test_cli_extraction_disagreement_exits_incomplete(root, disagreeing_extraction, capsys):
    assert cli.main(["run", "--no-send"]) == 2
    assert "run INCOMPLETE" in capsys.readouterr().out
