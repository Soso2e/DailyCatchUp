"""Post YouTube upload completion notification to Discord channels at night."""

from __future__ import annotations

import time
from typing import List

import httpx

import config
from collector.rss_collector import Article
from discord_bot.channel_store import get_all_channel_ids
from logger import get_logger
from meta.meta_generator import VideoMetadata

log = get_logger(__name__)

_DISCORD_API = "https://discord.com/api/v10"


def notify_youtube_uploaded(
    youtube_url: str,
    metadata: VideoMetadata,
    articles: List[Article],
    date_str: str,
) -> bool:
    """Send YouTube notification to all registered Discord channels.

    Returns True if at least one channel succeeded.
    """
    channel_ids = get_all_channel_ids()
    results: list[bool] = []

    if config.DISCORD_WEBHOOK_URL:
        results.append(
            _post_to_webhook(config.DISCORD_WEBHOOK_URL, youtube_url, metadata, articles, date_str)
        )

    results += [
        _post_to_channel(cid, youtube_url, metadata, articles, date_str)
        for cid in channel_ids
    ]

    if not results:
        log.warning("No Discord destinations configured – skipping night notification")
        return False

    return any(results)


def _post_to_channel(
    channel_id: str,
    youtube_url: str,
    metadata: VideoMetadata,
    articles: List[Article],
    date_str: str,
) -> bool:
    if not config.DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set – cannot post to channel %s", channel_id)
        return False

    headers = {"Authorization": f"Bot {config.DISCORD_BOT_TOKEN}"}
    url = f"{_DISCORD_API}/channels/{channel_id}/messages"
    embed = _build_embed(youtube_url, metadata, articles, date_str)

    for attempt in range(1, config.RETRY_COUNT + 1):
        try:
            resp = httpx.post(
                url,
                headers=headers,
                json={"content": f"🎬 本日の動画が公開されました！\n{youtube_url}", "embeds": [embed]},
                timeout=30,
            )
            resp.raise_for_status()
            log.info("Discord night notification sent to channel %s", channel_id)
            return True
        except Exception as exc:
            log.warning(
                "Discord night notify failed (attempt %d, channel %s): %s", attempt, channel_id, exc
            )
            if attempt < config.RETRY_COUNT:
                time.sleep(config.RETRY_BACKOFF * attempt)

    log.error("All Discord night notification attempts failed for channel %s", channel_id)
    return False


def _post_to_webhook(
    webhook_url: str,
    youtube_url: str,
    metadata: VideoMetadata,
    articles: List[Article],
    date_str: str,
) -> bool:
    """Legacy single-webhook fallback."""
    embed = _build_embed(youtube_url, metadata, articles, date_str)

    for attempt in range(1, config.RETRY_COUNT + 1):
        try:
            resp = httpx.post(
                webhook_url,
                json={"content": f"🎬 本日の動画が公開されました！\n{youtube_url}", "embeds": [embed]},
                timeout=30,
            )
            resp.raise_for_status()
            log.info("Discord night notification sent (webhook fallback)")
            return True
        except Exception as exc:
            log.warning("Discord webhook night notify failed (attempt %d): %s", attempt, exc)
            if attempt < config.RETRY_COUNT:
                time.sleep(config.RETRY_BACKOFF * attempt)

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
        "color": 0xFF4500,
        "fields": fields,
        "footer": {"text": f"DailyCatchUp • {date_str} 19:00配信"},
        "thumbnail": {"url": "https://i.ytimg.com/vi/default/mqdefault.jpg"},
    }
