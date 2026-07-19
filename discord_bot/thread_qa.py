"""Helpers for asking the daily NotebookLM notebook from Discord threads."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from logger import get_logger
from nlm.client import _open_client
from notion.status_manager import get_status_manager

log = get_logger(__name__)

JST = ZoneInfo("Asia/Tokyo")
DISCORD_MESSAGE_LIMIT = 1900
_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_DAILYCATCHUP_MARKERS = (
    "AI・ゲームニュース朝刊",
    "本日のアジェンダ（NotebookLM）",
    "本日の音声ニュース",
    "本日の要約テキスト",
)


def _embed_text(embed: Any) -> str:
    footer = getattr(getattr(embed, "footer", None), "text", None) or ""
    return "\n".join(
        str(value)
        for value in (
            getattr(embed, "title", None),
            getattr(embed, "description", None),
            footer,
        )
        if value
    )


def is_dailycatchup_starter(message: Any) -> bool:
    """Return whether a message looks like a DailyCatchUp daily post."""
    candidates = [getattr(message, "content", "") or ""]
    candidates.extend(_embed_text(embed) for embed in getattr(message, "embeds", []) or [])
    combined = "\n".join(candidates)
    return "DailyCatchUp" in combined or any(marker in combined for marker in _DAILYCATCHUP_MARKERS)


def infer_daily_date(message: Any) -> str:
    """Infer the target date from the post text, falling back to its JST creation date."""
    candidates = [getattr(message, "content", "") or ""]
    candidates.extend(_embed_text(embed) for embed in getattr(message, "embeds", []) or [])

    for candidate in candidates:
        for match in _DATE_RE.finditer(candidate):
            value = match.group(1)
            try:
                datetime.strptime(value, "%Y-%m-%d")
                return value
            except ValueError:
                continue

    created_at = getattr(message, "created_at", None)
    if created_at is None:
        return datetime.now(JST).date().isoformat()
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(JST).date().isoformat()


def build_notebook_prompt(question: str, date_str: str) -> str:
    """Build a source-grounded prompt whose answer can be pasted into Discord."""
    return (
        f"{date_str} のDailyCatchUpについて、以下の質問に回答してください。\n\n"
        "ルール:\n"
        "- このノートブック内のソースだけを根拠にする\n"
        "- 根拠が足りない場合は、推測せず『ノートブック内の情報だけでは判断できません』と伝える\n"
        "- 日本語で、結論を先に、必要十分な長さで答える\n"
        "- Discordにそのままコピペできる形で返信する\n"
        "- 表は避け、見出し・箇条書き・短い段落を使う\n"
        "- 『ご質問ありがとうございます』などの不要な前置きは付けない\n\n"
        f"質問:\n{question.strip()}"
    )


def _extract_answer(response: Any) -> str | None:
    for attribute in ("answer", "text", "content"):
        value = getattr(response, attribute, None)
        if value:
            return str(value).strip()
    if response:
        value = str(response).strip()
        return value or None
    return None


def load_notebook_id(date_str: str) -> str:
    """Load the NotebookLM notebook ID recorded for a given day."""
    status = get_status_manager().get(date_str)
    return (status.notebook_id or "").strip()


def ask_notebook(notebook_id: str, question: str, date_str: str) -> str | None:
    """Ask NotebookLM synchronously. Call this from a worker thread."""
    prompt = build_notebook_prompt(question, date_str)

    async def _inner() -> str | None:
        async with _open_client() as client:
            response = await client.chat.ask(notebook_id, prompt)
            return _extract_answer(response)

    log.info("Asking NotebookLM from Discord thread: date=%s notebook=%s", date_str, notebook_id)
    return asyncio.run(_inner())


def split_discord_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split a response into Discord-safe chunks while preferring line boundaries."""
    normalized = text.strip()
    if not normalized:
        return []
    if limit <= 0:
        raise ValueError("limit must be positive")

    chunks: list[str] = []
    current = ""

    for line in normalized.splitlines():
        remaining = line
        while len(remaining) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(remaining[:limit].rstrip())
            remaining = remaining[limit:]

        candidate = remaining if not current else f"{current}\n{remaining}"
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current.rstrip())
            current = remaining

    if current:
        chunks.append(current.rstrip())

    return [chunk for chunk in chunks if chunk]
