"""Post morning audio news and summary to Discord channels via bot token."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List

import httpx

import config
from collector.rss_collector import Article
from discord_bot.channel_store import get_all_channel_ids
from logger import get_logger

log = get_logger(__name__)

_DISCORD_API = "https://discord.com/api/v10"
_DISCORD_MAX_BYTES = 25 * 1024 * 1024  # 25 MB


def post_morning_news(
    audio_path: Path | None,
    summary_path: Path | None,
    articles: List[Article],
    date_str: str,
) -> bool:
    """Send morning news to all registered Discord channels.

    Returns True if at least one channel succeeded.
    """
    channel_ids = get_all_channel_ids()

    # Fallback: legacy single webhook URL
    if not channel_ids and config.DISCORD_WEBHOOK_URL:
        return _post_to_webhook(
            config.DISCORD_WEBHOOK_URL, audio_path, summary_path, articles, date_str
        )

    if not channel_ids:
        log.warning("No Discord channels registered – skipping morning post")
        return False

    results = [
        _post_to_channel(cid, audio_path, summary_path, articles, date_str)
        for cid in channel_ids
    ]
    return any(results)


def _post_to_channel(
    channel_id: str,
    audio_path: Path | None,
    summary_path: Path | None,
    articles: List[Article],
    date_str: str,
) -> bool:
    if not config.DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set – cannot post to channel %s", channel_id)
        return False

    headers = {"Authorization": f"Bot {config.DISCORD_BOT_TOKEN}"}
    url = f"{_DISCORD_API}/channels/{channel_id}/messages"
    embed = _build_embed(articles, date_str)
    success = True

    try:
        resp = httpx.post(url, headers=headers, json={"embeds": [embed]}, timeout=30)
        resp.raise_for_status()
        log.info("Discord morning embed posted to channel %s", channel_id)
    except Exception as exc:
        log.error("Discord embed post failed (channel %s): %s", channel_id, exc)
        success = False

    if audio_path and audio_path.exists():
        size_bytes = audio_path.stat().st_size
        size_mb = size_bytes / 1_048_576
        if size_bytes > _DISCORD_MAX_BYTES:
            log.warning("Audio %.1f MB exceeds Discord 25 MB limit – skipping upload", size_mb)
            _post_text_to_channel(
                channel_id,
                headers,
                f"🎙️ **本日の音声ニュース** (ファイルサイズ {size_mb:.1f} MB が Discord の 25 MB 制限を超えています)\n"
                "ボイスチャンネルで聞くには Bot コマンドをお使いください:\n"
                "```\n/news play\n```",
            )
        else:
            success &= _upload_file_to_channel(
                channel_id, headers, audio_path, content="🎙️ **本日の音声ニュース**"
            )

    if summary_path and summary_path.exists():
        success &= _upload_file_to_channel(
            channel_id, headers, summary_path, content="📄 **本日の要約テキスト**"
        )

    return success


def _post_text_to_channel(channel_id: str, headers: dict, text: str) -> None:
    try:
        httpx.post(
            f"{_DISCORD_API}/channels/{channel_id}/messages",
            headers=headers,
            json={"content": text},
            timeout=15,
        )
    except Exception:
        pass


def _upload_file_to_channel(
    channel_id: str, headers: dict, path: Path, content: str = ""
) -> bool:
    url = f"{_DISCORD_API}/channels/{channel_id}/messages"
    for attempt in range(1, config.RETRY_COUNT + 1):
        try:
            with open(path, "rb") as f:
                resp = httpx.post(
                    url,
                    headers=headers,
                    data={"payload_json": json.dumps({"content": content})},
                    files={"files[0]": (path.name, f, _mime_type(path))},
                    timeout=60,
                )
            resp.raise_for_status()
            log.info("Discord file uploaded to channel %s: %s", channel_id, path.name)
            return True
        except Exception as exc:
            log.warning("File upload failed (attempt %d, channel %s): %s", attempt, channel_id, exc)
            if attempt < config.RETRY_COUNT:
                time.sleep(config.RETRY_BACKOFF * attempt)
    return False


def _post_to_webhook(
    webhook_url: str,
    audio_path: Path | None,
    summary_path: Path | None,
    articles: List[Article],
    date_str: str,
) -> bool:
    """Legacy single-webhook fallback."""
    embed = _build_embed(articles, date_str)
    success = True
    try:
        resp = httpx.post(webhook_url, json={"embeds": [embed]}, timeout=30)
        resp.raise_for_status()
        log.info("Discord morning embed posted (webhook fallback)")
    except Exception as exc:
        log.error("Discord webhook embed post failed: %s", exc)
        success = False

    if audio_path and audio_path.exists():
        size_bytes = audio_path.stat().st_size
        if size_bytes <= _DISCORD_MAX_BYTES:
            for attempt in range(1, config.RETRY_COUNT + 1):
                try:
                    with open(audio_path, "rb") as f:
                        resp = httpx.post(
                            webhook_url,
                            data={"content": "🎙️ **本日の音声ニュース**"},
                            files={"file": (audio_path.name, f, _mime_type(audio_path))},
                            timeout=60,
                        )
                    resp.raise_for_status()
                    break
                except Exception as exc:
                    log.warning("Webhook file upload failed (attempt %d): %s", attempt, exc)
                    if attempt < config.RETRY_COUNT:
                        time.sleep(config.RETRY_BACKOFF * attempt)
                    else:
                        success = False

    if summary_path and summary_path.exists():
        for attempt in range(1, config.RETRY_COUNT + 1):
            try:
                with open(summary_path, "rb") as f:
                    resp = httpx.post(
                        webhook_url,
                        data={"content": "📄 **本日の要約テキスト**"},
                        files={"file": (summary_path.name, f, _mime_type(summary_path))},
                        timeout=60,
                    )
                resp.raise_for_status()
                break
            except Exception as exc:
                log.warning("Webhook summary upload failed (attempt %d): %s", attempt, exc)
                if attempt < config.RETRY_COUNT:
                    time.sleep(config.RETRY_BACKOFF * attempt)
                else:
                    success = False

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
        "color": 0x00C8FF,
        "fields": fields,
        "footer": {"text": "DailyCatchUp • 毎朝07:00配信"},
    }


def _mime_type(path: Path) -> str:
    return {".mp3": "audio/mpeg", ".mp4": "video/mp4", ".md": "text/markdown"}.get(
        path.suffix.lower(), "application/octet-stream"
    )
