"""Persistent storage for registered Discord notification channels."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, TypedDict

import config

CHANNEL_STORE_PATH = Path(config.BASE_DIR) / "notification_channels.json"


class ChannelEntry(TypedDict):
    channel_id: str
    guild_name: str
    channel_name: str
    added_by: str
    added_at: str


def load_channels() -> List[ChannelEntry]:
    if not CHANNEL_STORE_PATH.exists():
        return []
    try:
        data = json.loads(CHANNEL_STORE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_channels(entries: List[ChannelEntry]) -> None:
    CHANNEL_STORE_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_channel(
    channel_id: str,
    guild_name: str,
    channel_name: str,
    added_by: str,
) -> bool:
    """Returns False if the channel is already registered."""
    entries = load_channels()
    if any(e["channel_id"] == channel_id for e in entries):
        return False
    entries.append(
        {
            "channel_id": channel_id,
            "guild_name": guild_name,
            "channel_name": channel_name,
            "added_by": added_by,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_channels(entries)
    return True


def remove_channel(channel_id: str) -> str | None:
    """Remove by channel ID. Returns the channel name, or None if not found."""
    entries = load_channels()
    new_entries = []
    removed_name: str | None = None
    for e in entries:
        if e["channel_id"] == channel_id:
            removed_name = e["channel_name"]
        else:
            new_entries.append(e)
    if removed_name is not None:
        save_channels(new_entries)
    return removed_name


def get_all_channel_ids() -> List[str]:
    return [e["channel_id"] for e in load_channels()]
