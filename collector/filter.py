"""Deduplication, importance scoring, and article selection."""

from __future__ import annotations

from difflib import SequenceMatcher
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import config
from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)

AI_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "generative ai",
    "chatgpt",
    "gpt",
    "claude",
    "gemini",
    "llm",
    "large language model",
    "machine learning",
    "deep learning",
    "openai",
    "anthropic",
    "google ai",
    "生成ai",
    "人工知能",
    "機械学習",
]

GAME_KEYWORDS = [
    "game",
    "gaming",
    "playstation",
    "ps5",
    "xbox",
    "nintendo",
    "switch",
    "steam",
    "esports",
    "indie game",
    "dlc",
    "update",
    "patch",
    "ゲーム",
    "任天堂",
    "switch2",
]

TREND_KEYWORDS = [
    "launch",
    "release",
    "announce",
    "funding",
    "model",
    "agent",
    "breakthrough",
    "発表",
    "公開",
    "提供開始",
]

TRUSTED_DOMAINS = {
    "openai.com": 3.0,
    "anthropic.com": 3.0,
    "deepmind.google": 3.0,
    "blog.google": 2.5,
    "techcrunch.com": 2.0,
    "theverge.com": 2.0,
    "venturebeat.com": 2.0,
    "wired.com": 1.5,
    "arstechnica.com": 1.5,
    "4gamer.net": 1.5,
    "famitsu.com": 1.5,
    "aismiley.co.jp": 1.5,
}


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower().replace("www.", "")
    return urlunsplit((parts.scheme.lower(), netloc, parts.path.rstrip("/"), "", ""))


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _domain_score(article: Article) -> float:
    normalized_url = _normalize_url(article.url)
    for domain, score in TRUSTED_DOMAINS.items():
        if domain in normalized_url or domain in article.source.lower():
            return score
    return 0.0


def _keyword_hits(text: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if kw in text)


def _score_article(article: Article) -> float:
    text = f"{article.title} {article.summary}".lower()
    ai_hits = _keyword_hits(text, AI_KEYWORDS)
    game_hits = _keyword_hits(text, GAME_KEYWORDS)
    trend_hits = _keyword_hits(text, TREND_KEYWORDS)

    age_hours = max(
        0.0,
        (datetime.now(timezone.utc) - article.published_at).total_seconds() / 3600,
    )
    freshness = max(0.0, 1.0 - age_hours / 36.0)

    score = 0.0
    score += ai_hits * 3.0
    score += game_hits * 1.2
    score += trend_hits * 0.8
    score += freshness * 4.0
    score += _domain_score(article)
    if article.language == "en":
        score += 0.4

    return score


def deduplicate(articles: list[Article], threshold: float = 0.78) -> list[Article]:
    unique: list[Article] = []
    seen_urls: set[str] = set()

    for article in articles:
        normalized_url = _normalize_url(article.url)
        if normalized_url in seen_urls:
            continue

        is_dup = any(
            _title_similarity(article.title, existing.title) >= threshold
            for existing in unique
        )
        if is_dup:
            continue

        seen_urls.add(normalized_url)
        unique.append(article)

    return unique


def filter_articles(
    articles: list[Article],
    max_count: int | None = None,
    min_count: int | None = None,
) -> list[Article]:
    max_count = max_count or config.MAX_ARTICLES
    min_count = min_count or config.MIN_ARTICLES

    filtered = [article for article in articles if article.title and article.url]
    filtered = deduplicate(filtered)

    for article in filtered:
        article.score = _score_article(article)

    filtered.sort(key=lambda article: article.score, reverse=True)
    selected = filtered[:max_count]

    log.info(
        "Filtered to %d articles (from %d candidates, min=%d)",
        len(selected),
        len(filtered),
        min_count,
    )

    if len(selected) < min_count:
        log.warning(
            "Only %d articles selected (min=%d) - consider expanding search range",
            len(selected),
            min_count,
        )

    return selected
