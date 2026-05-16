"""Post YouTube upload completion notification to Discord at night."""

from __future__ import annotations

from typing import List

import httpx

import config
from collector.rss_collector import Article
from logger import get_logger
from meta.meta_generator import VideoMetadata

log = get_logger(__name__)


def notify_youtube_uploaded(
    youtube_url: str,
    metadata: VideoMetadata,
    articles: List[Article],
    date_str: str,
) -> bool:
    """Send YouTube video link + topic summary to Discord.

    Returns True on success.
    """
    if not config.DISCORD_WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not set – skipping night notification")
        return False

    embed = _build_embed(youtube_url, metadata, articles, date_str)

    for attempt in range(1, config.RETRY_COUNT + 1):
        try:
            resp = httpx.post(
                config.DISCORD_WEBHOOK_URL,
                json={"content": f"🎬 本日の動画が公開されました！\n{youtube_url}", "embeds": [embed]},
                timeout=30,
            )
            resp.raise_for_status()
            log.info("Discord night notification sent (YouTube: %s)", youtube_url)
            return True
        except Exception as exc:
            log.warning("Discord night notify failed (attempt %d): %s", attempt, exc)
            import time
            if attempt < config.RETRY_COUNT:
                time.sleep(config.RETRY_BACKOFF * attempt)

    log.error("All Discord night notification attempts failed")
    return False


def _build_embed(
    youtube_url: str,
    metadata: VideoMetadata,
    articles: List[Article],
    date_str: str,
) -> dict:
    fields: list[dict] = []

    if metadata.tags:
        tag_str = " ".join(f"`{t}`" for t in metadata.tags[:8])
        fields.append({"name": "タグ", "value": tag_str, "inline": False})

    fields.append(
        {"name": "▶️ YouTube", "value": f"[動画を見る]({youtube_url})", "inline": False}
    )

    for i, a in enumerate(articles[:3], 1):
        lang_flag = "🇯🇵" if a.language == "ja" else "🇺🇸"
        fields.append(
            {
                "name": f"{lang_flag} {i}. {a.title[:60]}",
                "value": f"[{a.source}]({a.url})",
                "inline": True,
            }
        )

    return {
        "title": f"🌙 {metadata.title}",
        "description": metadata.description[:300] if metadata.description else "",
        "url": youtube_url,
        "color": 0xFF4500,  # Orange-red (YouTube-ish)
        "fields": fields,
        "footer": {"text": f"DailyCatchUp • {date_str} 19:00配信"},
        "thumbnail": {"url": "https://i.ytimg.com/vi/default/mqdefault.jpg"},
    }
