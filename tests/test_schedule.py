"""Monthly scheduler: runs once when due, reruns only on changed inputs, retries then gives up."""

import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from recon import config, notify, pipeline, schedule

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path):
    shutil.copytree(ROOT / "data", tmp_path / "data")
    c = config.load(ROOT / "config.toml")
    c.data_dir, c.state_dir, c.out_dir = tmp_path / "data", tmp_path / "state", tmp_path / "out"
    c.extraction["provider"] = "cached"
    c.extractions_dir = ROOT / "extractions"
    c.notify["channel"] = "console"
    return c


def at(cfg, text):
    return replace(cfg, now=datetime.fromisoformat(text).replace(tzinfo=cfg.tz))


def test_waits_until_the_scheduled_time(cfg):
    assert schedule.tick(at(cfg, "2026-10-01T08:59")).startswith("waiting")
    assert not (cfg.out_dir / "2026-09").exists()
    assert (cfg.state_dir / "heartbeat").exists()  # still proves it is alive


def test_runs_once_at_scenario_time_then_stays_quiet(cfg):
    first = schedule.tick(cfg)  # scenario clock: Thu 15 Oct 08:30, after the 1st 09:00
    assert "2026-09: ran" in first
    assert "message sent" in first
    assert "up to date" in schedule.tick(cfg)
    assert "up to date" in schedule.tick(at(cfg, "2026-10-20T09:00"))


def test_reruns_when_an_input_changes(cfg):
    schedule.tick(cfg)
    pipeline.save_resolution(cfg, "2026-09", "reg:p2:r14:qty", "250", by="Controller")
    assert "ran" in schedule.tick(cfg)  # review decision -> new run, new message
    shutil.copy(
        cfg.data_dir / "2026-09" / "register" / "page2.jpg", cfg.data_dir / "2026-09" / "register" / "page3.jpg"
    )
    assert "ran" in schedule.tick(cfg)  # a new photo -> new run


def test_crash_alerts_retries_then_gives_up(cfg, monkeypatch, capsys):
    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "run", broken)
    monkeypatch.setattr(notify.time, "sleep", lambda _s: None)
    assert "attempt 1" in schedule.tick(cfg)
    assert "attempt 2" in schedule.tick(cfg)
    assert "gave up" in schedule.tick(cfg)
    assert "up to date (failed)" in schedule.tick(cfg)  # no endless alerts
    assert capsys.readouterr().out.count("run FAILED") == 3
