"""Post morning audio news and summary to Discord via webhook."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List

import httpx

import config
from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)


def post_morning_news(
    audio_path: Path | None,
    summary_path: Path | None,
    articles: List[Article],
    date_str: str,
) -> bool:
    """Send audio file + summary text to the Discord channel.

    Returns True on success.
    """
    if not config.DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not set – skipping morning post")
        return False

    success = True

    # Build embed with article list
    embed = _build_embed(articles, date_str)

    # Send embed message first
    try:
        resp = httpx.post(
            config.DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=30,
        )
        resp.raise_for_status()
        log.info("Discord morning embed posted")
    except Exception as exc:
        log.error("Discord embed post failed: %s", exc)
        success = False

    # Attach summary markdown file
    if summary_path and summary_path.exists():
        success &= _upload_file(
            summary_path,
            content="📄 **本日の要約テキスト**",
        )

    return success


def _build_embed(articles: List[Article], date_str: str) -> dict:
    fields = []
    for i, a in enumerate(articles[:5], 1):
        lang_flag = "🇯🇵" if a.language == "ja" else "🇺🇸"
        fields.append(
            {
                "name": f"{lang_flag} {i}. {a.title[:80]}",
                "value": f"[{a.source}]({a.url})",
                "inline": False,
            }
        )

    return {
        "title": f"🌅 AI・ゲームニュース朝刊 {date_str}",
        "description": "本日のAI・ゲーム業界注目ニュースをお届けします。",
        "color": 0x00C8FF,  # Cyan
        "fields": fields,
        "footer": {"text": "DailyCatchUp • 毎朝07:00配信"},
    }


def _upload_file(path: Path, content: str = "") -> bool:
    for attempt in range(1, config.RETRY_COUNT + 1):
        try:
            with open(path, "rb") as f:
                resp = httpx.post(
                    config.DISCORD_WEBHOOK_URL,
                    data={"content": content},
                    files={"file": (path.name, f, _mime_type(path))},
                    timeout=60,
                )
            resp.raise_for_status()
            log.info("Discord file uploaded: %s", path.name)
            return True
        except Exception as exc:
            log.warning("Discord file upload failed (attempt %d): %s", attempt, exc)
            if attempt < config.RETRY_COUNT:
                time.sleep(config.RETRY_BACKOFF * attempt)

    return False


def _post_text(text: str) -> None:
    try:
        httpx.post(
            config.DISCORD_WEBHOOK_URL,
            json={"content": text},
            timeout=15,
        )
    except Exception:
        pass


def _mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {".mp3": "audio/mpeg", ".mp4": "video/mp4", ".md": "text/markdown"}.get(
        ext, "application/octet-stream"
    )
