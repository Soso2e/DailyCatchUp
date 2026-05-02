"""Notion DB status manager with SQLite fallback.

Maintains one record per day tracking pipeline progress (news_collected,
notebooklm_generated, etc.).  Falls back to SQLite automatically if Notion
credentials are missing or calls fail.

Both backends expose save_articles / load_articles so the runner can cache
collected articles and skip re-fetching when news_collected is already True.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, TYPE_CHECKING

import config
from logger import get_logger

if TYPE_CHECKING:
    from collector.rss_collector import Article

log = get_logger(__name__)

SQLITE_PATH = Path(config.BASE_DIR) / ".pipeline_status.db"


@dataclass
class DailyStatus:
    date: str = field(default_factory=lambda: date.today().isoformat())
    news_collected: bool = False
    notebooklm_generated: bool = False
    audio_downloaded: bool = False
    video_downloaded: bool = False
    meta_generated: bool = False
    discord_morning: bool = False
    youtube_uploaded: bool = False
    discord_notified: bool = False
    youtube_url: str = ""
    error_log: str = ""


# ---------------------------------------------------------------------------
# Notion backend
# ---------------------------------------------------------------------------

class NotionStatusManager:
    def __init__(self) -> None:
        from notion_client import Client  # type: ignore

        self._client = Client(auth=config.NOTION_API_KEY)
        self._db_id = config.NOTION_DATABASE_ID

    def _find_page(self, date_str: str) -> str | None:
        results = self._client.databases.query(
            database_id=self._db_id,
            filter={"property": "date", "date": {"equals": date_str}},
        )
        pages = results.get("results", [])
        return pages[0]["id"] if pages else None

    def get(self, date_str: str) -> DailyStatus:
        page_id = self._find_page(date_str)
        if not page_id:
            return DailyStatus(date=date_str)

        page = self._client.pages.retrieve(page_id=page_id)
        props = page["properties"]

        def cb(key: str) -> bool:
            return props.get(key, {}).get("checkbox", False)

        def txt(key: str) -> str:
            rich = props.get(key, {}).get("rich_text", [])
            return rich[0]["plain_text"] if rich else ""

        def url(key: str) -> str:
            return props.get(key, {}).get("url") or ""

        return DailyStatus(
            date=date_str,
            news_collected=cb("news_collected"),
            notebooklm_generated=cb("notebooklm_generated"),
            audio_downloaded=cb("audio_downloaded"),
            video_downloaded=cb("video_downloaded"),
            meta_generated=cb("meta_generated"),
            discord_morning=cb("discord_morning"),
            youtube_uploaded=cb("youtube_uploaded"),
            discord_notified=cb("discord_notified"),
            youtube_url=url("youtube_url"),
            error_log=txt("error_log"),
        )

    def save(self, status: DailyStatus) -> None:
        props: dict = {
            "news_collected": {"checkbox": status.news_collected},
            "notebooklm_generated": {"checkbox": status.notebooklm_generated},
            "audio_downloaded": {"checkbox": status.audio_downloaded},
            "video_downloaded": {"checkbox": status.video_downloaded},
            "meta_generated": {"checkbox": status.meta_generated},
            "discord_morning": {"checkbox": status.discord_morning},
            "youtube_uploaded": {"checkbox": status.youtube_uploaded},
            "discord_notified": {"checkbox": status.discord_notified},
            "youtube_url": {"url": status.youtube_url or None},
            "error_log": {"rich_text": [{"text": {"content": status.error_log[:2000]}}]},
        }

        page_id = self._find_page(status.date)
        if page_id:
            self._client.pages.update(page_id=page_id, properties=props)
        else:
            props["date"] = {"date": {"start": status.date}}
            self._client.pages.create(
                parent={"database_id": self._db_id}, properties=props
            )
        log.debug("Notion status saved for %s", status.date)

    def _find_or_create_page(self, date_str: str) -> str:
        page_id = self._find_page(date_str)
        if not page_id:
            self.save(DailyStatus(date=date_str))
            page_id = self._find_page(date_str)
        return page_id  # type: ignore[return-value]

    def save_articles(self, date_str: str, articles: "List[Article]") -> None:
        from notion.article_store import save_articles_to_page
        page_id = self._find_or_create_page(date_str)
        save_articles_to_page(self._client, page_id, articles)

    def load_articles(self, date_str: str) -> "List[Article]":
        from notion.article_store import load_articles_from_page
        page_id = self._find_page(date_str)
        if not page_id:
            return []
        return load_articles_from_page(self._client, page_id)


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

class SQLiteStatusManager:
    def __init__(self, db_path: Path = SQLITE_PATH) -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_status (
                    date TEXT PRIMARY KEY,
                    news_collected INTEGER DEFAULT 0,
                    notebooklm_generated INTEGER DEFAULT 0,
                    audio_downloaded INTEGER DEFAULT 0,
                    video_downloaded INTEGER DEFAULT 0,
                    meta_generated INTEGER DEFAULT 0,
                    discord_morning INTEGER DEFAULT 0,
                    youtube_uploaded INTEGER DEFAULT 0,
                    discord_notified INTEGER DEFAULT 0,
                    youtube_url TEXT DEFAULT '',
                    error_log TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_articles (
                    date TEXT PRIMARY KEY,
                    articles_json TEXT DEFAULT '[]'
                )
                """
            )

    def get(self, date_str: str) -> DailyStatus:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM daily_status WHERE date = ?", (date_str,)
            ).fetchone()
            if not row:
                return DailyStatus(date=date_str)
            return DailyStatus(
                date=row["date"],
                news_collected=bool(row["news_collected"]),
                notebooklm_generated=bool(row["notebooklm_generated"]),
                audio_downloaded=bool(row["audio_downloaded"]),
                video_downloaded=bool(row["video_downloaded"]),
                meta_generated=bool(row["meta_generated"]),
                discord_morning=bool(row["discord_morning"]),
                youtube_uploaded=bool(row["youtube_uploaded"]),
                discord_notified=bool(row["discord_notified"]),
                youtube_url=row["youtube_url"] or "",
                error_log=row["error_log"] or "",
            )

    def save(self, status: DailyStatus) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO daily_status VALUES (
                    :date, :news_collected, :notebooklm_generated,
                    :audio_downloaded, :video_downloaded, :meta_generated,
                    :discord_morning, :youtube_uploaded, :discord_notified,
                    :youtube_url, :error_log
                )
                ON CONFLICT(date) DO UPDATE SET
                    news_collected=excluded.news_collected,
                    notebooklm_generated=excluded.notebooklm_generated,
                    audio_downloaded=excluded.audio_downloaded,
                    video_downloaded=excluded.video_downloaded,
                    meta_generated=excluded.meta_generated,
                    discord_morning=excluded.discord_morning,
                    youtube_uploaded=excluded.youtube_uploaded,
                    discord_notified=excluded.discord_notified,
                    youtube_url=excluded.youtube_url,
                    error_log=excluded.error_log
                """,
                {
                    "date": status.date,
                    "news_collected": int(status.news_collected),
                    "notebooklm_generated": int(status.notebooklm_generated),
                    "audio_downloaded": int(status.audio_downloaded),
                    "video_downloaded": int(status.video_downloaded),
                    "meta_generated": int(status.meta_generated),
                    "discord_morning": int(status.discord_morning),
                    "youtube_uploaded": int(status.youtube_uploaded),
                    "discord_notified": int(status.discord_notified),
                    "youtube_url": status.youtube_url,
                    "error_log": status.error_log,
                },
            )
        log.debug("SQLite status saved for %s", status.date)

    def save_articles(self, date_str: str, articles: "List[Article]") -> None:
        data = [
            {"title": a.title, "url": a.url, "source": a.source, "language": a.language}
            for a in articles
        ]
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO daily_articles(date, articles_json) VALUES(?, ?)
                ON CONFLICT(date) DO UPDATE SET articles_json = excluded.articles_json
                """,
                (date_str, json.dumps(data, ensure_ascii=False)),
            )
        log.debug("SQLite articles saved for %s (%d items)", date_str, len(articles))

    def load_articles(self, date_str: str) -> "List[Article]":
        from collector.rss_collector import Article

        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT articles_json FROM daily_articles WHERE date = ?", (date_str,)
            ).fetchone()
        if not row:
            return []
        return [
            Article(
                title=d["title"],
                url=d["url"],
                published_at=datetime.now(timezone.utc),
                source=d["source"],
                language=d["language"],
            )
            for d in json.loads(row[0])
        ]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_status_manager() -> NotionStatusManager | SQLiteStatusManager:
    if config.NOTION_API_KEY and config.NOTION_DATABASE_ID:
        try:
            mgr = NotionStatusManager()
            log.info("Using Notion status manager")
            return mgr
        except Exception as exc:
            log.warning("Notion unavailable (%s) – falling back to SQLite", exc)
    log.info("Using SQLite status manager (%s)", SQLITE_PATH)
    return SQLiteStatusManager()
