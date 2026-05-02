"""Generate YouTube metadata (title, description, tags, thumbnail text) using Claude API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List

import anthropic

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


def generate_metadata(articles: List[Article], date_str: str) -> VideoMetadata:
    article_lines = "\n".join(
        f"{i + 1}. [{a.language.upper()}] {a.title}\n   Source: {a.source}\n   URL: {a.url}"
        for i, a in enumerate(articles)
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    log.info("Calling Claude API for metadata generation (model=%s)", config.CLAUDE_MODEL)

    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(
                    date=date_str,
                    articles=article_lines,
                ),
            }
        ],
    )

    raw = message.content[0].text.strip()
    log.debug("Claude response: %s", raw[:200])

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON block if model wrapped it
        import re

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            log.error("Could not parse Claude response as JSON: %s", raw)
            data = _fallback_metadata(articles, date_str)

    return VideoMetadata(
        title=data.get("title", f"AI・ゲームニュース {date_str}"),
        description=data.get("description", ""),
        tags=data.get("tags", []),
        thumbnail_headline=data.get("thumbnail_headline", "今日のAI・ゲームニュース"),
        thumbnail_subtext=data.get("thumbnail_subtext", ""),
    )


def _fallback_metadata(articles: List[Article], date_str: str) -> dict:
    titles = "・".join(a.title[:20] for a in articles[:3])
    return {
        "title": f"【{date_str}】AI・ゲーム最新ニュース",
        "description": f"本日のAI・ゲーム業界ニュースまとめです。\n{titles}\n#AIニュース #ゲームニュース",
        "tags": ["AIニュース", "ゲームニュース", "AI", "gaming", "daily news"],
        "thumbnail_headline": "今日のAI・ゲームニュース",
        "thumbnail_subtext": date_str,
    }
