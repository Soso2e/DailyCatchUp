"""Persistent storage for Discord webhook URLs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, TypedDict

import config

WEBHOOK_STORE_PATH = Path(config.BASE_DIR) / "webhook_channels.json"

_DISCORD_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)


class WebhookEntry(TypedDict):
    url: str
    label: str
    added_by: str
    added_at: str


def is_valid_discord_webhook_url(url: str) -> bool:
    return any(url.startswith(p) for p in _DISCORD_WEBHOOK_PREFIXES)


def mask_url(url: str) -> str:
    """Partially mask the webhook token for safe display."""
    parts = url.split("/")
    if len(parts) >= 2:
        token = parts[-1]
        masked = token[:4] + "..." + token[-4:] if len(token) > 8 else "****"
        parts[-1] = masked
        return "/".join(parts)
    return url[:20] + "..."


def load_webhooks() -> List[WebhookEntry]:
    if not WEBHOOK_STORE_PATH.exists():
        return []
    try:
        data = json.loads(WEBHOOK_STORE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_webhooks(entries: List[WebhookEntry]) -> None:
    WEBHOOK_STORE_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_webhook(url: str, label: str, added_by: str) -> bool:
    """Add a webhook. Returns False if the URL is already registered."""
    entries = load_webhooks()
    if any(e["url"] == url for e in entries):
        return False
    entries.append(
        {
            "url": url,
            "label": label,
            "added_by": added_by,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_webhooks(entries)
    return True


def remove_webhook(identifier: str) -> str | None:
    """Remove by URL or label. Returns the removed label, or None if not found."""
    entries = load_webhooks()
    new_entries = []
    removed_label: str | None = None
    for e in entries:
        if e["url"] == identifier or e["label"] == identifier:
            removed_label = e["label"]
        else:
            new_entries.append(e)
    if removed_label is not None:
        save_webhooks(new_entries)
    return removed_label


def get_all_webhook_urls() -> List[str]:
    """All webhook URLs: env var default + stored ones, deduplicated."""
    urls: List[str] = []
    if config.DISCORD_WEBHOOK_URL:
        urls.append(config.DISCORD_WEBHOOK_URL)
    for entry in load_webhooks():
        if entry["url"] not in urls:
            urls.append(entry["url"])
    return urls
