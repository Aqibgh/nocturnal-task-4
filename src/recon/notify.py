"""Send the controller message once, on the directors' channel.

Channels (config.toml notify.channel; credentials only in .env):
  email            Gmail SMTP with an app password (default)
  whatsapp_cloud   Meta WhatsApp Cloud API (official test number is free)
  twilio_whatsapp  Twilio WhatsApp sandbox
  console          prints, for local runs and the demo

Idempotent: the message is keyed by channel + period + content hash, so re-running the same reconciliation
never sends a duplicate. A changed result (e.g. after a review decision) is a new message.
On failure after retries, the message is saved to out/<period>/UNSENT.txt and the error is raised,
so the run fails visibly (the scheduler alerts and retries it).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import smtplib
import ssl
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import parseaddr

from . import audit
from .config import Config

TIMEOUT = 15


def send(cfg: Config, period: str, text: str, run_id: str) -> str:
    channel = cfg.notify.get("channel", "console")
    key = hashlib.sha256(f"{channel}\n{period}\n{text}".encode()).hexdigest()[:16]
    sent_file = cfg.state_dir / "sent.json"
    sent = json.loads(sent_file.read_text(encoding="utf-8")) if sent_file.exists() else {}
    legacy_key = hashlib.sha256(f"{period}\n{text}".encode()).hexdigest()[:16]
    if legacy_key in sent and sent[legacy_key].get("channel") == channel:
        key = legacy_key  # preserve deduplication for messages sent before channel-aware keys
    if key in sent:
        audit.log(cfg, "notify.skipped_duplicate", run=run_id, key=key, first_sent=sent[key])
        return "skipped (identical message already sent)"

    try:
        _with_retries(cfg, run_id, channel, text)
    except Exception as exc:
        unsent = cfg.out_dir / period / "UNSENT.txt"
        unsent.parent.mkdir(parents=True, exist_ok=True)
        unsent.write_text(text, encoding="utf-8")
        audit.log(cfg, "notify.failed", run=run_id, channel=channel, error=str(exc))
        raise RuntimeError(f"could not send via {channel}: {exc}") from exc

    sent[key] = {"at": cfg.now.isoformat(), "run": run_id, "channel": channel}
    sent_file.parent.mkdir(parents=True, exist_ok=True)
    sent_file.write_text(json.dumps(sent, indent=2), encoding="utf-8")
    audit.log(cfg, "notify.sent", run=run_id, channel=channel, key=key)
    return f"sent via {channel}"


def alert(cfg: Config, text: str) -> None:
    """Best-effort alert that a run broke. Never raises."""
    try:
        _deliver(cfg.notify.get("channel", "console"), "⚠️ Store reconciliation run FAILED\n" + text)
    except Exception as exc:
        print(f"[alert could not be delivered: {exc}]\n{text}")


def _with_retries(cfg: Config, run_id: str, channel: str, text: str, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            _deliver(channel, text)
            return
        except Exception as exc:
            audit.log(cfg, "notify.retry", run=run_id, channel=channel, attempt=attempt, error=str(exc))
            if attempt == attempts:
                raise
            time.sleep(2**attempt)


def chunks(text: str, limit: int) -> list[str]:
    """Split on line boundaries so no message exceeds the channel's limit."""
    parts: list[str] = []
    current = ""
    for raw_line in text.split("\n"):
        line = raw_line
        while len(line) > limit:  # a single over-long line is split, never cut off
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            candidate = line
        current = candidate
    return [*parts, current] if current else parts


def _deliver(channel: str, text: str) -> None:
    sender, limit = CHANNELS.get(channel, (None, 0))
    if sender is None:
        raise ValueError(f"unknown notify channel {channel!r}")
    if channel == "email":
        sender(text)  # one email contains the full summary, without chat-message splitting
        return
    for part in chunks(text, limit):
        sender(part)


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        if r.status >= 300:
            raise RuntimeError(f"HTTP {r.status} from {urllib.parse.urlparse(url).netloc}")


def _console(text: str) -> None:
    print("\n----- message to Finance Controller -----\n" + text + "\n-----------------------------------------\n")


def _email(text: str) -> None:
    sender = os.environ.get("EMAIL_FROM", "").strip()
    recipient = os.environ.get("EMAIL_TO", "").strip()
    password = os.environ.get("EMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not sender or not recipient or not password:
        raise ValueError("email needs EMAIL_FROM, EMAIL_TO and EMAIL_APP_PASSWORD in .env")
    for name, address in (("EMAIL_FROM", sender), ("EMAIL_TO", recipient)):
        if "@" not in address or any(c.isspace() for c in address) or parseaddr(address)[1] != address:
            raise ValueError(f"{name} must be a single email address")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "Kestrel store reconciliation"
    message.set_content(text)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=TIMEOUT, context=ssl.create_default_context()) as smtp:
            smtp.login(sender, password)
            refused = smtp.send_message(message)
            if refused:
                raise RuntimeError("Gmail refused the recipient")
    except smtplib.SMTPException as exc:
        # SMTP responses can contain addresses or authentication details; don't log them.
        raise RuntimeError(f"Gmail SMTP failed ({type(exc).__name__}); check the app password and recipient") from None


def _whatsapp_cloud(text: str) -> None:
    # Free-form text is allowed inside the 24-hour window after the recipient last messaged the number;
    # outside it, Meta requires an approved template (see README).
    phone_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    body = {"messaging_product": "whatsapp", "to": os.environ["WHATSAPP_TO"], "type": "text", "text": {"body": text}}
    _post(
        f"https://graph.facebook.com/v21.0/{phone_id}/messages",
        json.dumps(body).encode(),
        {"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN']}", "Content-Type": "application/json"},
    )


def _twilio_whatsapp(text: str) -> None:
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    form = {
        "From": f"whatsapp:{os.environ.get('TWILIO_WHATSAPP_FROM', '+14155238886')}",  # sandbox number
        "To": f"whatsapp:{os.environ['WHATSAPP_TO']}",
        "Body": text,
    }
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    _post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        urllib.parse.urlencode(form).encode(),
        {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
    )


# channel -> (sender, max characters per message)
CHANNELS: dict[str, tuple[Callable[[str], None], int]] = {
    "email": (_email, 100_000),
    "console": (_console, 100_000),
    "whatsapp_cloud": (_whatsapp_cloud, 4096),
    "twilio_whatsapp": (_twilio_whatsapp, 1600),
}
