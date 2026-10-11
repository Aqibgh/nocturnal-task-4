# Kestrel register reconciliation (Task 4)

Photograph the handwritten receiving register. The system reads it, compares it with the ERP's GRNs and
the storekeeper's Excel, and tells the Finance Controller exactly where the three disagree. It proves
every row was accounted for, and asks a person only about the entries it could not read with certainty.

## Run it

Needs Docker. Copy `.env.example` to `.env` and fill the Gmail and Gemini fields below.
The default uses `extractions/live` for real Gemini reads; the original September seed cache is retained for offline runs.
Use `recon run --no-send` to generate the report offline without messaging credentials.

```bash
docker compose run --rm recon run          # reconcile the previous month and email the controller summary
docker compose up review                   # review page: http://localhost:8501
docker compose up -d scheduler             # monthly run (below)
docker compose run --rm recon schedule --once   # one scheduler check at the scenario clock
docker compose run --rm recon resolve reg:p2:r14:qty 250 --by "Your Name"   # same as the page, from the CLI
```

Tests and code quality (dev image, never shipped):

```bash
docker compose --profile quality run --rm test       # tests on the real data, with coverage
docker compose --profile quality run --rm lint       # ruff lint + format check + mypy
docker compose --profile quality run --rm prettier   # markdown/yaml/json formatting
docker compose --profile quality up -d sonarqube     # then (SONAR_TOKEN in .env):
docker compose --profile quality run --rm sonar-scanner
```

Last scan: 0 bugs, 0 vulnerabilities, 0 code smells, 0% duplication, 91% coverage, quality gate passed.

Outputs go to `out/2026-09/`:

- `summary.txt`: the message
- `report.md`: full report with the completeness proof
- `outcomes.csv`: one line per GRN
- `ledger.csv`: one line per source row
- `review.json`: the review queue

`state/audit.jsonl` logs every extraction, decision, run and message.

**Scenario clock:** `now` in `config.toml` (or `RECON_NOW=2026-10-15T08:30:00+05:00`). The month reconciled is the one before `now`.

**Monthly run:** the `scheduler` service wakes every 15 minutes. Once the 1st of the month at 09:00 has passed
(`[schedule]` in `config.toml`), it reconciles the previous month and sends the message.

- It runs again only if an input changes: a new photo, a corrected export, or a review decision.
- An unchanged result is never re-sent.
- A crashed run alerts, retries on each check, and after 3 attempts gives up with a final alert until an input changes.
- Every check writes `state/heartbeat`. The container is marked unhealthy and restarted if checks stop.
- State is kept in `state/schedule.json`.

## How it works

1. **Read the photos.** A vision model (Claude or Gemini, set in `config.toml`) returns every cell with a
   confidence score and flags (`illegible`, `struck_through`, `overwritten`, `ambiguous`). It is told never to fill
   a value from context. Each page is read twice, and any cell where the reads disagree goes to review.
   Rule checks run on top: date inside the month, GRN in sequence, lot pattern, positive quantity, known
   unit and supplier. Results are cached by the image's SHA-256, so a monthly run only reads new pages.
2. **Normalise.** Supplier spellings map to the ERP supplier code. Units map to canonical units and
   convert within a dimension (1 yd = 0.9144 m). Dates come in as Excel serials, as text, or written day-first.
   All of these mappings are tables in `config.toml`. Anything unknown goes to review, never a guess.
3. **Reconcile on GRN number.** Each GRN ends up `matched`, `mismatched` (which field, each source's value,
   and which source is the odd one out), `missing` (from which source), or `needs_review`. Rows outside
   that get their own outcomes: exact duplicate rows become `duplicate`, and negative rows with no GRN become `adjustment`.
   GRN numbers that appear in no source are listed separately.
4. **Prove completeness.** Input row counts and per-unit quantity totals are read straight from the files.
   Every source row must land in exactly one outcome, and the totals must tie. If they don't, the run exits non-zero and says so.
5. **Review.** Only the doubtful items appear on the review page, each beside the cropped photo row. A decision
   needs the reviewer's name and is logged. The run then repeats with the decision applied.

## What it found in September

The full list is in `out/2026-09/report.md`. Rows: 28 register, 28 ERP, 30 sheet, all accounted for. 21 GRNs match in all three.

**For a person**

- 4419: quantity corrected by hand in the register (60 → 66).
- 4428: quantity hidden under an ink blot. It is **not** filled in from the ERP.

**Sources disagree**

- 4412: lot number has two digits swapped in the ERP.
- 4415: ERP dated Sunday 13 Sep; the other two say Saturday 12 Sep.
- 4417: 46 vs 48 cartons.
- 4425: the sheet says 1200 m, but the register's 1200 is yards, which matches the ERP's 1097.28 m.

**Missing**

- 4421 is not in the ERP.
- 4430 is only in the ERP. It was keyed on 2 Oct by `admin2`.
- 4429 is in no source.

**Also**

- The sheet has 4409 twice.
- The sheet has a −5,000 polybag return with no GRN, on the lot of GRN 4402.

## Assumptions

- GRN number is the match key. The register is written day-first. Sunday is a non-working day.
- Quantities must match exactly (`qty_tolerance` in config).
- The same quantity in convertible units is a match, so yards vs metres is not a disagreement.
- A hand-corrected value is always confirmed by a person, even when it is clear.
- An entry keyed after month-end still counts in the month of its GRN date, but it is flagged.

## How it fails, and how someone would know

- **Page can't be read** (API down, bad photo, no model configured): retried with backoff. The run still
  completes, and the message opens with "NOT COMPLETE: page3.jpg not read".
- **A file is missing** (ERP export, store sheet): the message says which. Everything that did arrive is still reconciled and proven.
- **Proof doesn't tie:** the message says so, the run is recorded as `incomplete` in `state/schedule.json`, and
  `recon run` exits with code 2.
- **Scheduler stops:** the heartbeat goes stale, Docker marks the container unhealthy, and `restart: unless-stopped` brings it back.
- **Crash:** an alert goes to the same channel with the error, and the exception is re-raised.
- **Duplicate messages:** a message is keyed by month + content hash, so re-running the same result never re-sends.

## Data handling

- Secrets go only in `.env` (git-ignored). `.env.example` lists the names.
- The container runs as a non-root user, with source data mounted read-only.
- Only the register photos go to the model provider. The ERP and sheet files stay local; their reconciliation summary is emailed to the configured recipient.

## Email and Gemini setup

The demo uses email, an allowed assessment channel. Set these in `.env`:

```dotenv
EMAIL_FROM=your_account@gmail.com
EMAIL_TO=your_account@gmail.com
EMAIL_APP_PASSWORD=your_google_app_password
GEMINI_API_KEY=your_gemini_api_key
```

Gmail: enable [2-Step Verification](https://myaccount.google.com/security), then create an
[App Password](https://myaccount.google.com/apppasswords) named `Kestrel Recon`. Use that password,
not your normal Google password. Some account policies disable App Passwords.
The sender uses TLS to `smtp.gmail.com:465`, sends the whole summary in one email, and logs failures
without the SMTP authentication response. Tests go to your own inbox.

Gemini: create a key in [Google AI Studio](https://aistudio.google.com/apikey) and use a free-tier
project without enabling paid billing. `config.toml` selects `provider = "gemini"` and
`gemini_model = "gemini-2.5-flash"`; quota or provider errors are reported as unread pages.
Existing extraction caches are reused. `[paths]` defaults to `extractions = "extractions/live"`
for real two-pass model extraction inside the Docker cache mount; the supplied single-pass seed
transcriptions remain in the original `extractions` directory.
To run entirely offline, use `provider = "cached"`
with the original `extractions` directory and `notify.channel = "console"`.

WhatsApp migration: the existing `whatsapp_cloud` and `twilio_whatsapp` adapters can send the same
summary using credentials from `.env`. Live WhatsApp delivery remains unverified: Meta account
restrictions and Twilio's trial content restrictions blocked setup. Production scheduled WhatsApp
messages need an approved template outside the 24-hour customer service window; template sending
is future work. No payment or account upgrade is required for this email demo.

## With another week

- Register photos sent in by WhatsApp or email, and an approved WhatsApp template for the scheduled send.
- Approving review items from the phone.
- A month-over-month view of repeat offenders.
- An eval set of hand-labelled register pages to measure extraction accuracy per model.

## AI tools used

Codex: email delivery, messaging configuration, credential setup documentation, and mocked messaging tests.

Claude Code (Claude Opus 5.5): planning, data audit (Haiku 5.5 subagents for Tasks 1–3), code, tests, and the seed
transcription in `extractions/` (`tools/make_seed.py`; replaced by a real model read when a live provider and a fresh cache directory are selected).

Demo: Playwright browser recording with local Windows synthetic narration and captions. Review actions and the
missing-export failure use an isolated copy; the original source data and pending review decisions are preserved.

## Demo video

[Watch the 4 minute 45 second demo](output/playwright/Kestrel-Task4-Demo.mp4).
The [transcript](output/playwright/demo-transcript.md) and [captions](output/playwright/demo-captions.srt) are included.

## Time log

Approximate total: **4.5 hours**, based on the developer's estimate.

| Part | Hours |
|---|---:|
| Requirements and source-data inspection | 0.5 |
| Photo extraction, normalization and reconciliation | 1.5 |
| Completeness proof and review workflow | 1.0 |
| Email integration, testing and failure handling | 1.0 |
| Documentation and demo preparation | 0.5 |
| **Total** | **4.5** |
