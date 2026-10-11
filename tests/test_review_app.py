"""Renders the review page headlessly and saves a decision through it."""

import shutil
from pathlib import Path

from streamlit.testing.v1 import AppTest

from recon import config, pipeline

ROOT = Path(__file__).resolve().parents[1]


def test_review_page_saves_a_decision(tmp_path, monkeypatch):
    for name in ("config.toml", "data", "extractions"):
        src = ROOT / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, tmp_path / name)
    monkeypatch.setenv("RECON_ROOT", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        config_file.read_text(encoding="utf-8")
        .replace('provider = "gemini"', 'provider = "cached"')
        .replace('extractions = "extractions/live"', 'extractions = "extractions"')
        .replace('channel = "email"', 'channel = "console"'),
        encoding="utf-8",
    )
    pipeline.run(config.load(), send=False)

    at = AppTest.from_file(str(ROOT / "src" / "recon" / "review_app.py"), default_timeout=30).run()
    assert not at.exception
    assert len(at.subheader) == 2  # only 4419 and 4428, not all 58 rows
    assert sum(expander.label == "Full register page" for expander in at.expander) == 2
    at.sidebar.text_input[1].input("Test Reviewer").run()
    box = next(t for t in at.text_input if t.key == "v-reg:p2:r14:qty")
    box.input("banana").run()
    next(b for b in at.button if b.key == "b-reg:p2:r14:qty").click().run()
    assert not at.exception
    assert any("finite number above zero" in error.value for error in at.error)
    assert pipeline.load_resolutions(config.load(), "2026-09") == {}
    assert len(at.subheader) == 2
    box.input("250").run()
    next(b for b in at.button if b.key == "b-reg:p2:r14:qty").click().run()
    assert not at.exception
    saved = pipeline.load_resolutions(config.load(), "2026-09")
    assert saved["reg:p2:r14:qty"]["value"] == "250"
    assert saved["reg:p2:r14:qty"]["by"] == "Test Reviewer"
    assert any(button.label == "Re-run reconciliation with these decisions" for button in at.button)
    next(button for button in at.button if button.label == "Re-run reconciliation with these decisions").click().run()
    assert not at.exception
    assert len(at.subheader) == 1
    assert at.subheader[0].value == "GRN 4419: qty"
