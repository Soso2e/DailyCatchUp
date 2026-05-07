"""Generate YouTube metadata using Gemini API."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List

from google import genai
from google.genai import types

import config
from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)

SYSTEM_PROMPT = """\
You are a professional content creator specializing in AI and gaming industry news.
Generate compelling YouTube metadata in JSON format. Output ONLY valid JSON, no markdown fences.
"""

USER_PROMPT_TEMPLATE = """\
Today is {date}.

The following news articles will be covered in today's episode:

{articles}

Generate YouTube metadata for this video. Return JSON with these exact keys:
- "title": Catchy Japanese YouTube title (under 60 chars), include today's date
- "description": Japanese YouTube description (200-400 chars) with key topics and hashtags
- "tags": Array of 10-15 English/Japanese tags (strings)
- "thumbnail_headline": Short Japanese headline for thumbnail (under 20 chars)
- "thumbnail_subtext": 2-3 key topic words for thumbnail (Japanese, comma-separated)
"""


@dataclass
class VideoMetadata:
    title: str
    description: str
    tags: List[str]
    thumbnail_headline: str
    thumbnail_subtext: str


def _gemini_key_state() -> tuple[bool, str | None]:
    api_key = config.GEMINI_API_KEY.strip()
    if not api_key:
        return False, "gemini_api_key_missing"
    if "your_" in api_key.lower():
        return False, "gemini_api_key_placeholder"
    return True, None


def generate_metadata(articles: List[Article], date_str: str) -> VideoMetadata | None:
    key_ok, skip_reason = _gemini_key_state()
    if not key_ok:
        log.warning("Skipping metadata generation: %s", skip_reason)
        return None

    article_lines = "\n".join(
        f"{i + 1}. [{a.language.upper()}] {a.title}\n   Source: {a.source}\n   URL: {a.url}"
        for i, a in enumerate(articles)
    )

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        log.info("Calling Gemini API for metadata generation (model=%s)", config.GEMINI_MODEL)
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=USER_PROMPT_TEMPLATE.format(date=date_str, articles=article_lines),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=1024,
            ),
        )
    except Exception as exc:
        message = str(exc)
        if "API_KEY_INVALID" in message or "API key not valid" in message:
            log.warning("Skipping metadata generation: gemini_api_key_invalid (%s)", exc)
        else:
            log.warning("Skipping metadata generation: gemini_request_failed (%s)", exc)
        return None

    raw = (response.text or "").strip()
    if not raw:
        log.warning("Skipping metadata generation: gemini_empty_response")
        return None

    log.debug("Gemini response: %s", raw[:200])

    # Strip Markdown code fences (```json ... ``` or ``` ... ```)
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned).strip()

    data = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                pass
        if data is None:
            log.error("Could not parse Gemini response as JSON: %s", cleaned[:500])
            _save_raw_gemini_response(cleaned, date_str)
            data = _fallback_metadata(articles, date_str)

    return VideoMetadata(
        title=data.get("title", f"AI・ゲームニュース {date_str}"),
        description=data.get("description", ""),
        tags=data.get("tags", []),
        thumbnail_headline=data.get("thumbnail_headline", "今日のAI・ゲーム"),
        thumbnail_subtext=data.get("thumbnail_subtext", ""),
    )


def _save_raw_gemini_response(raw: str, date_str: str) -> None:
    try:
        from pathlib import Path
        import config as _cfg
        out_dir = Path(_cfg.OUTPUTS_DIR) / date_str
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "gemini_raw_response.txt"
        dest.write_text(raw, encoding="utf-8")
        log.info("Raw Gemini response saved to %s", dest)
    except Exception as exc:
        log.warning("Could not save raw Gemini response: %s", exc)


def _fallback_metadata(articles: List[Article], date_str: str) -> dict:
    titles = " / ".join(a.title[:20] for a in articles[:3])
    return {
        "title": f"[{date_str}] AI・ゲーム最新ニュース",
        "description": f"本日のAI・ゲーム関連ニュースをまとめています。\n{titles}\n#AIニュース #ゲームニュース",
        "tags": ["AIニュース", "ゲームニュース", "AI", "gaming", "daily news"],
        "thumbnail_headline": "今日のAI・ゲーム",
        "thumbnail_subtext": date_str,
    }
