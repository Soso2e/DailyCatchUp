"""RSS news collector for Japanese and English AI/game media."""

import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from typing import List
from urllib.parse import unquote, urlparse

import feedparser

from logger import get_logger

log = get_logger(__name__)

RSS_FEEDS: dict[str, tuple[str, str]] = {
    # (url, language)
    # --- Google News ---
    "google_news_ja_ai": (
        "https://news.google.com/rss/search?q=AI+人工知能&hl=ja&gl=JP&ceid=JP:ja",
        "ja",
    ),
    "google_news_ja_game": (
        "https://news.google.com/rss/search?q=ゲーム業界+新作&hl=ja&gl=JP&ceid=JP:ja",
        "ja",
    ),
    "google_news_en_ai": (
        "https://news.google.com/rss/search?q=(AI+OR+%22artificial+intelligence%22+OR+%22machine+learning%22+OR+LLM+OR+%22generative+AI%22)&hl=en-US&gl=US&ceid=US:en",
        "en",
    ),
    "google_news_en_game": (
        "https://news.google.com/rss/search?q=(gaming+industry+OR+game+release)+AI&hl=en-US&gl=US&ceid=US:en",
        "en",
    ),
    "google_news_ja_ai_global": (
        "https://news.google.com/rss/search?q=(生成AI+OR+人工知能+OR+機械学習+OR+LLM)&hl=ja&gl=JP&ceid=JP:ja",
        "ja",
    ),
    # --- ゲーム専門メディア ---
    "4gamer": ("https://www.4gamer.net/rss/index.xml", "ja"),
    "famitsu": ("https://www.famitsu.com/rss/gamers/", "ja"),
    # --- AI メディア ---
    "aimedia": ("https://aismiley.co.jp/feed/", "ja"),
    # --- 英語テックメディア ---
    "techcrunch_ai": (
        "https://techcrunch.com/tag/artificial-intelligence/feed/",
        "en",
    ),
    "venturebeat_ai": ("https://venturebeat.com/category/ai/feed/", "en"),
    # --- Zenn (日本語開発者コミュニティ) ---
    "zenn_ai": ("https://zenn.dev/topics/ai/feed", "ja"),
    "zenn_llm": ("https://zenn.dev/topics/llm/feed", "ja"),
    "zenn_chatgpt": ("https://zenn.dev/topics/chatgpt/feed", "ja"),
    "zenn_game": ("https://zenn.dev/topics/game/feed", "ja"),
    # --- はてなブックマーク テクノロジー ---
    "hatena_bookmark_it": ("https://b.hatena.ne.jp/hotentry/it.rss", "ja"),
    # --- 企業テックブログ ---
    "mercari_engineering": (
        "https://engineering.mercari.com/blog/feed.xml",
        "ja",
    ),
    "dena_engineering": ("https://engineering.dena.com/feed", "ja"),
    "ntt_engineers": ("https://engineers.ntt.com/feed", "ja"),
    "cyberagent_ai_blog": ("https://cyberagent.ai/blog/feed", "ja"),
    "line_engineering": ("https://engineering.linecorp.com/feed", "ja"),
}

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href=["\'](?P<url>https?://[^"\']+)["\']', re.IGNORECASE)
_URL_QUERY_KEYS = ("url", "u", "q", "target", "dest", "destination", "redirect", "redirect_url")


@dataclass
class Article:
    title: str
    url: str
    published_at: datetime
    source: str
    language: str
    summary: str = ""
    original_url_candidates: list[str] = field(default_factory=list, compare=False)
    text_excerpt: str = field(default="", compare=False)
    score: float = field(default=0.0, compare=False)
    popularity: int = field(default=0, compare=False)  # likes / bookmarks / HN score


def _parse_popularity(entry) -> int:
    """Try to extract bookmark/like count from feed entry metadata."""
    # はてなブックマーク: hatena:bookmarkcount
    for attr in ("hatena_bookmarkcount", "bookmarkcount"):
        val = getattr(entry, attr, None) or entry.get(attr)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return 0


def _strip_html(text: str) -> str:
    text = unescape(_HTML_TAG_RE.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _extract_urls_from_text(text: str) -> list[str]:
    urls: list[str] = []
    for match in _HREF_RE.finditer(text or ""):
        urls.append(match.group("url"))

    lowered = (text or "").lower()
    for key in _URL_QUERY_KEYS:
        marker = f"{key}="
        start = 0
        while True:
            idx = lowered.find(marker, start)
            if idx < 0:
                break
            value = text[idx + len(marker):]
            for sep in ("&amp;", "&", '"', "'", " ", "<", ">"):
                if sep in value:
                    value = value.split(sep, 1)[0]
            candidate = unquote(unescape(value).strip())
            if candidate.startswith(("http://", "https://")):
                urls.append(candidate)
            start = idx + len(marker)
    return urls


def _is_google_news_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == "news.google.com"


def _collect_original_url_candidates(entry) -> list[str]:
    candidates: list[str] = []

    for key in ("feedburner_origlink", "origlink"):
        value = (entry.get(key) or "").strip()
        if value:
            candidates.append(value)

    source = entry.get("source")
    if isinstance(source, dict):
        href = (source.get("href") or "").strip()
        if href:
            candidates.append(href)

    for link_item in entry.get("links", []) or []:
        if not isinstance(link_item, dict):
            continue
        href = (link_item.get("href") or "").strip()
        if href:
            candidates.append(href)

    for field_name in ("summary", "description", "title_detail", "summary_detail"):
        value = entry.get(field_name)
        if isinstance(value, dict):
            value = value.get("value") or value.get("base") or ""
        if isinstance(value, str) and value:
            candidates.extend(_extract_urls_from_text(value))

    for content_item in entry.get("content", []) or []:
        if not isinstance(content_item, dict):
            continue
        value = content_item.get("value") or ""
        if value:
            candidates.extend(_extract_urls_from_text(value))

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if (
            not normalized
            or not normalized.startswith(("http://", "https://"))
            or _is_google_news_url(normalized)
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _build_text_excerpt(entry) -> str:
    parts: list[str] = []

    summary = entry.get("summary") or entry.get("description") or ""
    if isinstance(summary, str) and summary:
        parts.append(_strip_html(summary))

    for content_item in entry.get("content", []) or []:
        if not isinstance(content_item, dict):
            continue
        value = (content_item.get("value") or "").strip()
        if value:
            parts.append(_strip_html(value))

    merged = " ".join(part for part in parts if part).strip()
    if len(merged) > 800:
        return merged[:797].rstrip() + "..."
    return merged


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
                        original_url_candidates=_collect_original_url_candidates(entry),
                        text_excerpt=_build_text_excerpt(entry),
                        popularity=_parse_popularity(entry),
                    )
                )
        except Exception as exc:
            log.warning("RSS collect error [%s]: %s", source_name, exc)

    log.info("RSS collected %d articles total", len(articles))
    return articles
