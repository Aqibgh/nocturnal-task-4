# Kestrel register reconciliation (Task 4)

Compares photographed receiving-register pages with ERP GRNs and the storekeeper's Excel. It reports matches, field/value differences, missing entries, duplicates and adjustments. A source-row ledger checks counts and quantity totals; only doubtful cells need human review.

## Run

Requires Docker. From the repository folder:

```bash
docker compose run --rm --build recon run --no-send
docker compose up --build review
```

Open **http://localhost:8501**. Enter a reviewer name, inspect the photo context or full page, save a decision and rerun reconciliation. The supplied sample includes cached results of two real Gemini reads per photo, so replay requires no Gemini key.

For email delivery or uncached photos, copy `.env.example` to `.env`:

```dotenv
EMAIL_FROM=your_account@gmail.com
EMAIL_TO=your_account@gmail.com
EMAIL_APP_PASSWORD=your_google_app_password
GEMINI_API_KEY=your_gemini_api_key
```

Use a [Google App Password](https://myaccount.google.com/apppasswords) after enabling 2-Step Verification. Create the Gemini key in [Google AI Studio](https://aistudio.google.com/apikey); keep its project on the free tier. Send tests to yourself. Then run:

```bash
docker compose run --rm recon run
```

`config.toml` selects Gemini and email. `now = "2026-10-15T08:30:00+05:00"` selects September, the previous month; `RECON_NOW` overrides it. Results are in `out/2026-09/`: `report.md`, `summary.txt`, `outcomes.csv`, `ledger.csv` and `review.json`. Extraction, decisions and delivery are logged in `state/audit.jsonl`.

## September results and assumptions

All **86 rows** are accounted for: 28 register, 28 ERP, 30 Excel. There are 21 matched GRNs, four mismatches, two missing-source cases and two review items. The report identifies the lot transposition, Sunday date, carton quantity difference, yards/metres error, duplicate 4409, return adjustment, missing 4421, late ERP-only 4430 and sequence gap 4429. The corrected 4419 quantity and unreadable 4428 quantity remain for review; the blot is not filled from ERP.

GRN number is the match key. Supplier aliases map to ERP codes. Register dates are day-first; Sunday is non-working. Quantities match exactly after supported conversions, including 1 yd = 0.9144 m. Hand corrections need confirmation. Post-month-end ERP entries remain included but are flagged.

## Failure handling and data

Model and delivery calls retry with backoff. Missing files or unreadable pages produce a **NOT COMPLETE** message with the affected source. Missing sources, unreadable pages, extraction row-count disagreements and proof failures exit with code 2 and are recorded as incomplete by the scheduler. Crashes trigger a best-effort alert; failed delivery saves `UNSENT.txt`. Identical messages are deduplicated by channel, month and content.

The optional scheduler (`docker compose up -d --build scheduler`) checks every 15 minutes, runs after the configured monthly date/time, and reruns changed inputs. Crashes retry up to three attempts. Its heartbeat health check exposes stalled checks.

Only register photos go to the model provider. ERP/Excel files stay local; the summary is emailed. The container runs without root privileges and mounts source data read-only. Secrets belong only in `.env`, excluded from Git and Docker. The supplied data is fictional.

Quantity decisions must be finite and positive; invalid input shows an error and stays unresolved. Previously saved invalid quantities are ignored and returned to review. Extraction row-count disagreement prevents a passing completeness proof. A row omitted by every model read still requires independent photo coverage checks; two matching reads alone cannot prove its absence.

Email is an allowed assessment channel. WhatsApp adapters are included but live delivery is unverified; scheduled production messages need approved templates outside the customer-service window.

## Validation, demo and next work

Latest checks: **42 tests passed, 92% coverage**, plus Ruff lint, formatting and mypy.

```bash
docker compose --profile quality run --rm test
docker compose --profile quality run --rm lint
```

[YouTube demo: 4:45](https://youtu.be/_LO8tD0izJI).

As an extra challenge, I wanted to develop a tool that could record the demo for me, so I built a Playwright-based recording workflow with local narration and captions. The demo replays genuine cached Gemini results, shows review and missing-export recovery in an isolated copy, and includes verified email-delivery evidence. The video predates the quantity-validation and incomplete-status fixes; both fixes are included in the current code and regression tests.

With another week: independently check photo row coverage, evaluate extraction against labelled pages, add approved WhatsApp templates, phone review and photo intake.

AI tools: Claude Code for the original planning, implementation, tests and seed transcription; Codex for email integration, live checks, review UI fixes and demo preparation. Playwright recorded the browser; narration was generated locally with Windows speech synthesis.

## Rough time log

Developer-estimated total: **4.5 hours**.

| Part | Hours |
|---|---:|
| Requirements and data inspection | 0.5 |
| Extraction, normalization and reconciliation | 1.5 |
| Completeness proof and review | 1.0 |
| Email, testing and failure handling | 1.0 |
| Documentation and demo | 0.5 |
| **Total** | **4.5** |
