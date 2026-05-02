"""RSS news collector for Japanese and English AI/game media."""

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

import feedparser

from logger import get_logger

log = get_logger(__name__)

RSS_FEEDS: dict[str, tuple[str, str]] = {
    # (url, language)
    "google_news_ja_ai": (
        "https://news.google.com/rss/search?q=AI+人工知能&hl=ja&gl=JP&ceid=JP:ja",
        "ja",
    ),
    "google_news_ja_game": (
        "https://news.google.com/rss/search?q=ゲーム業界+新作&hl=ja&gl=JP&ceid=JP:ja",
        "ja",
    ),
    "google_news_en_ai": (
        "https://news.google.com/rss/search?q=artificial+intelligence+generative&hl=en-US&gl=US&ceid=US:en",
        "en",
    ),
    "google_news_en_game": (
        "https://news.google.com/rss/search?q=gaming+industry+release&hl=en-US&gl=US&ceid=US:en",
        "en",
    ),
    "4gamer": ("https://www.4gamer.net/rss/index.xml", "ja"),
    "famitsu": ("https://www.famitsu.com/rss/gamers/", "ja"),
    "aimedia": ("https://aismiley.co.jp/feed/", "ja"),
    "techcrunch_ai": (
        "https://techcrunch.com/tag/artificial-intelligence/feed/",
        "en",
    ),
    "venturebeat_ai": ("https://venturebeat.com/category/ai/feed/", "en"),
}


@dataclass
class Article:
    title: str
    url: str
    published_at: datetime
    source: str
    language: str
    summary: str = ""
    score: float = field(default=0.0, compare=False)


def collect_rss(max_age_hours: int = 24) -> List[Article]:
    articles: List[Article] = []
    cutoff_ts = datetime.now(timezone.utc).timestamp() - max_age_hours * 3600

    for source_name, (feed_url, lang) in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            log.debug("RSS [%s]: %d entries", source_name, len(feed.entries))

            for entry in feed.entries[:30]:
                pub_ts: float | None = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_ts = float(calendar.timegm(entry.published_parsed))

                if pub_ts is not None and pub_ts < cutoff_ts:
                    continue

                pub_dt = (
                    datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                    if pub_ts is not None
                    else datetime.now(timezone.utc)
                )

                title = entry.get("title", "").strip()
                url = entry.get("link", "").strip()
                if not title or not url:
                    continue

                articles.append(
                    Article(
                        title=title,
                        url=url,
                        published_at=pub_dt,
                        source=source_name,
                        language=lang,
                        summary=entry.get("summary", ""),
                    )
                )
        except Exception as exc:
            log.warning("RSS collect error [%s]: %s", source_name, exc)

    log.info("RSS collected %d articles total", len(articles))
    return articles
