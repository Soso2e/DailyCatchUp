"""Post morning audio news and summary to Discord via webhook."""

from __future__ import annotations

import time
from pathlib import Path
from typing import List

import httpx

import config
from collector.rss_collector import Article
from discord_bot.webhook_store import get_all_webhook_urls
from logger import get_logger

log = get_logger(__name__)

_DISCORD_MAX_BYTES = 25 * 1024 * 1024  # 25 MB


def post_morning_news(
    audio_path: Path | None,
    summary_path: Path | None,
    articles: List[Article],
    date_str: str,
) -> bool:
    """Send audio file + summary text to all registered Discord channels.

    Returns True if at least one webhook succeeded.
    """
    urls = get_all_webhook_urls()
    if not urls:
        log.warning("No Discord webhook URLs configured – skipping morning post")
        return False

    results = [
        _post_to_webhook(url, audio_path, summary_path, articles, date_str)
        for url in urls
    ]
    return any(results)


def _post_to_webhook(
    webhook_url: str,
    audio_path: Path | None,
    summary_path: Path | None,
    articles: List[Article],
    date_str: str,
) -> bool:
    short_url = webhook_url[:60]
    success = True

    embed = _build_embed(articles, date_str)
    try:
        resp = httpx.post(webhook_url, json={"embeds": [embed]}, timeout=30)
        resp.raise_for_status()
        log.info("Discord morning embed posted: %s", short_url)
    except Exception as exc:
        log.error("Discord embed post failed (%s): %s", short_url, exc)
        success = False

    if audio_path and audio_path.exists():
        size_bytes = audio_path.stat().st_size
        size_mb = size_bytes / 1_048_576
        if size_bytes > _DISCORD_MAX_BYTES:
            log.warning(
                "Audio file %.1f MB exceeds Discord 25 MB limit – skipping file upload", size_mb
            )
            _post_text(
                webhook_url,
                f"🎙️ **本日の音声ニュース** (ファイルサイズ {size_mb:.1f} MB が Discord の 25 MB 制限を超えています)\n"
                "ボイスチャンネルで聞くには Bot コマンドをお使いください:\n"
                "```\n/news play\n```\n"
                "※ DailyCatchUp Bot が起動中で、あなたがボイスチャンネルに参加している場合のみ利用可能です。",
            )
        else:
            success &= _upload_file(webhook_url, audio_path, content="🎙️ **本日の音声ニュース**")

    if summary_path and summary_path.exists():
        success &= _upload_file(webhook_url, summary_path, content="📄 **本日の要約テキスト**")

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


def _upload_file(webhook_url: str, path: Path, content: str = "") -> bool:
    for attempt in range(1, config.RETRY_COUNT + 1):
        try:
            with open(path, "rb") as f:
                resp = httpx.post(
                    webhook_url,
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


def _post_text(webhook_url: str, text: str) -> None:
    try:
        httpx.post(webhook_url, json={"content": text}, timeout=15)
    except Exception:
        pass


def _mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {".mp3": "audio/mpeg", ".mp4": "video/mp4", ".md": "text/markdown"}.get(
        ext, "application/octet-stream"
    )
