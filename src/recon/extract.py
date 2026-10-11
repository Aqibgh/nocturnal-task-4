"""Register photos -> structured rows.

Every page is cached by the SHA-256 of the image, so:
- a re-run never pays for (or changes) a page already read,
- next month's run only reads the new pages,
- the reviewers can run everything with provider = "cached" and no API key.

The model is told never to guess. On top of that, each page is read `passes` times and any
cell where the reads disagree is sent to a person (see validate.py).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from . import audit
from .config import Config
from .models import PageExtraction

PROMPT = """You are transcribing one page of a handwritten goods-receiving register from a factory store.
Return every row that has any writing, top to bottom, with these columns:
Date, GRN No., Supplier, Item / Description, Lot No., Qty, Unit, Recd by.

Rules - these matter more than completeness:
- Copy what is written. Do not correct spelling, expand abbreviations or convert units.
- If you cannot read a value with certainty, set value to null, describe what you see in raw_text,
  and add the flag "illegible". Never fill a value in from context or from neighbouring rows.
- If a value is crossed out and another written next to it, put the new value in value, describe both
  in raw_text (e.g. "60 struck through, 66 written above"), and add "struck_through" and "overwritten".
- If a character could be two things (0 or O, 1 or 7), add "ambiguous" and lower confidence.
- confidence is your probability that value is exactly right.
- y_top / y_bottom: the row's vertical position as a fraction of the image height.
"""


@dataclass
class PageResult:
    page: str  # file name, e.g. page2.jpg
    sha256: str
    passes: list[PageExtraction]
    source: str  # "cache" or the provider that produced it
    error: str | None = None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_pages(cfg: Config, pages: list[Path], run_id: str) -> list[PageResult]:
    results = []
    for page in pages:
        digest = sha256(page)
        cache_file = cfg.extractions_dir / f"{digest}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            passes = [PageExtraction.model_validate(p) for p in data["passes"]]
            audit.log(
                cfg,
                "extract.cache_hit",
                run=run_id,
                page=page.name,
                sha256=digest[:12],
                extractor=data.get("extractor"),
            )
            results.append(PageResult(page.name, digest, passes, "cache"))
            continue

        provider = cfg.extraction["provider"]
        if provider == "cached":
            msg = "page is new and extraction.provider is 'cached' (no model configured)"
            audit.log(cfg, "extract.failed", run=run_id, page=page.name, error=msg)
            results.append(PageResult(page.name, digest, [], provider, error=msg))
            continue

        try:
            passes = [_with_retries(cfg, provider, page) for _ in range(int(cfg.extraction.get("passes", 2)))]
        except Exception as exc:  # the run continues; the page is reported as unread
            audit.log(cfg, "extract.failed", run=run_id, page=page.name, error=repr(exc))
            results.append(PageResult(page.name, digest, [], provider, error=str(exc)))
            continue

        cfg.extractions_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "image": page.name,
                    "sha256": digest,
                    "extractor": f"{provider}:{_model_name(cfg, provider)}",
                    "extracted_at": cfg.now.isoformat(),
                    "passes": [p.model_dump() for p in passes],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        audit.log(
            cfg,
            "extract.done",
            run=run_id,
            page=page.name,
            sha256=digest[:12],
            provider=provider,
            rows=len(passes[0].rows),
        )
        results.append(PageResult(page.name, digest, passes, provider))
    return results


def _with_retries(cfg: Config, provider: str, page: Path) -> PageExtraction:
    attempts = int(cfg.extraction.get("max_retries", 3))
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _call(cfg, provider, page)
        except ValidationError as exc:  # model returned JSON that doesn't fit the schema
            last = exc
        except Exception as exc:  # network, rate limit, 5xx
            last = exc
        time.sleep(min(2**attempt, 20))
    raise RuntimeError(f"{provider} failed after {attempts} attempts: {last}")


def _model_name(cfg: Config, provider: str) -> str:
    return cfg.extraction.get(f"{provider}_model", "?")


def _call(cfg: Config, provider: str, page: Path) -> PageExtraction:
    image = page.read_bytes()
    if provider == "anthropic":
        return _anthropic(cfg, image)
    if provider == "gemini":
        return _gemini(cfg, image)
    raise ValueError(f"unknown extraction provider {provider!r}")


def _anthropic(cfg: Config, image: bytes) -> PageExtraction:
    import anthropic

    client = anthropic.Anthropic(max_retries=0)  # retries are ours, so they are logged
    model = cfg.extraction["anthropic_model"]
    fallback = model in ("claude-opus-5-5", "claude-sonnet-5-5")
    response = client.messages.parse(
        model=model,
        # Server-side fallback: if a safety classifier declines, the API retries on a suitable model.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"} if fallback else None,
        extra_body={"fallbacks": "default"} if fallback else None,
        max_tokens=16000,
        output_config={"effort": "medium"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.standard_b64encode(image).decode(),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        output_format=PageExtraction,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    if response.parsed_output is None:
        raise RuntimeError(f"no structured output (stop_reason={response.stop_reason})")
    return response.parsed_output


def _gemini(cfg: Config, image: bytes) -> PageExtraction:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model=cfg.extraction["gemini_model"],
        contents=types.Content(
            role="user",
            parts=[types.Part.from_bytes(data=image, mime_type="image/jpeg"), types.Part.from_text(text=PROMPT)],
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=PageExtraction,
            temperature=0.0,
        ),
    )
    if not response.text:
        raise RuntimeError("Gemini returned no text")
    return PageExtraction.model_validate_json(response.text)
