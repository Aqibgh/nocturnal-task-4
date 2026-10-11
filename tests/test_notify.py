"""Message delivery: splitting, request shape, retries and the unsent fallback (no network)."""

import hashlib
import json
import urllib.parse
from pathlib import Path

import pytest

from recon import config, notify

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = config.load(ROOT / "config.toml")
    c.state_dir, c.out_dir = tmp_path / "state", tmp_path / "out"
    monkeypatch.setattr(notify.time, "sleep", lambda _s: None)
    return c


def test_chunks_keep_every_character():
    text = "\n".join(["short line"] * 50 + ["x" * 3500])
    parts = notify.chunks(text, 1600)
    assert all(len(p) <= 1600 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_whatsapp_cloud_request(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_post", lambda url, data, headers: sent.append((url, json.loads(data), headers)))
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setenv("WHATSAPP_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_TO", "+920000000000")
    notify._deliver("whatsapp_cloud", "hello")
    url, body, headers = sent[0]
    assert url.endswith("/123/messages")
    assert body == {"messaging_product": "whatsapp", "to": "+920000000000", "type": "text", "text": {"body": "hello"}}
    assert headers["Authorization"] == "Bearer tok"


def test_twilio_splits_long_messages(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_post", lambda url, data, headers: sent.append((url, data, headers)))
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "WHATSAPP_TO"):
        monkeypatch.setenv(k, "x")
    notify._deliver("twilio_whatsapp", "\n".join(["line"] * 500))
    assert len(sent) == 2
    for url, data, headers in sent:
        assert url == "https://api.twilio.com/2010-04-01/Accounts/x/Messages.json"
        form = urllib.parse.parse_qs(data.decode())
        assert form["To"] == ["whatsapp:x"]
        assert form["From"][0].startswith("whatsapp:")
        assert len(form["Body"][0]) <= 1600
        assert headers["Authorization"] == "Basic eDp4"


def test_failed_send_is_saved_and_raised(cfg, monkeypatch):
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    cfg.notify["channel"] = "whatsapp_cloud"  # missing credentials -> every attempt fails
    with pytest.raises(RuntimeError):
        notify.send(cfg, "2026-09", "hello", run_id="t")
    assert (cfg.out_dir / "2026-09" / "UNSENT.txt").read_text() == "hello"
    assert "notify.failed" in (cfg.state_dir / "audit.jsonl").read_text()


def test_console_delivery_does_not_suppress_whatsapp(cfg, monkeypatch):
    delivered = []
    monkeypatch.setattr(notify, "_deliver", lambda channel, text: delivered.append((channel, text)))
    cfg.notify["channel"] = "console"
    assert notify.send(cfg, "2026-09", "hello", "t").startswith("sent")
    cfg.notify["channel"] = "whatsapp_cloud"
    assert notify.send(cfg, "2026-09", "hello", "t").startswith("sent")
    assert notify.send(cfg, "2026-09", "hello", "t").startswith("skipped")
    assert delivered == [("console", "hello"), ("whatsapp_cloud", "hello")]


def test_existing_whatsapp_delivery_is_not_resent(cfg, monkeypatch):
    cfg.state_dir.mkdir(parents=True)
    key = hashlib.sha256(b"2026-09\nhello").hexdigest()[:16]
    (cfg.state_dir / "sent.json").write_text(json.dumps({key: {"channel": "whatsapp_cloud"}}))
    cfg.notify["channel"] = "whatsapp_cloud"
    monkeypatch.setattr(notify, "_deliver", lambda *_: pytest.fail("already delivered"))
    assert notify.send(cfg, "2026-09", "hello", "t").startswith("skipped")


@pytest.fixture
def email_env(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "sender@example.com")
    monkeypatch.setenv("EMAIL_TO", "recipient@example.com")
    monkeypatch.setenv("EMAIL_APP_PASSWORD", "fake app password")


def test_email_sends_full_unicode_summary_once(email_env, monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, *, timeout, context):
            assert (host, port, timeout) == ("smtp.gmail.com", 465, notify.TIMEOUT)
            assert context.check_hostname

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def login(self, username, password):
            assert (username, password) == ("sender@example.com", "fakeapppassword")

        def send_message(self, message):
            calls.append(message)
            return {}

    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    text = "Reconciliation: \u2713\n" + "x" * 100_001
    notify._deliver("email", text)
    assert len(calls) == 1
    assert calls[0]["From"] == "sender@example.com"
    assert calls[0]["To"] == "recipient@example.com"
    assert calls[0].get_content().rstrip("\n") == text


@pytest.mark.parametrize("failure", ["missing_password", "invalid_recipient", "auth_failure"])
def test_email_failure_retries_saves_unsent_and_keeps_secrets_out(cfg, email_env, monkeypatch, failure):
    cfg.notify["channel"] = "email"
    attempts = []

    def unavailable(*_args, **_kwargs):
        attempts.append(1)
        raise notify.smtplib.SMTPAuthenticationError(535, b"fakeapppassword sender@example.com")

    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", unavailable)
    if failure == "missing_password":
        monkeypatch.delenv("EMAIL_APP_PASSWORD")
    elif failure == "invalid_recipient":
        monkeypatch.setenv("EMAIL_TO", "recipient@example.com\nBcc: other@example.com")
    with pytest.raises(RuntimeError) as exc:
        notify.send(cfg, "2026-09", "summary", "t")
    assert len(attempts) == (3 if failure == "auth_failure" else 0)
    assert (cfg.out_dir / "2026-09" / "UNSENT.txt").read_text() == "summary"
    assert not (cfg.state_dir / "sent.json").exists()
    evidence = str(exc.value) + (cfg.state_dir / "audit.jsonl").read_text()
    assert "fakeapppassword" not in evidence
    assert "sender@example.com" not in evidence
