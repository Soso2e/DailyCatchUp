"""Deduplication, importance scoring, and article selection."""

from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import List

import config
from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)

AI_KEYWORDS = [
    "AI", "artificial intelligence", "generative AI", "ChatGPT", "GPT",
    "Claude", "Gemini", "LLM", "large language model", "machine learning",
    "deep learning", "neural network", "OpenAI", "Anthropic", "Google AI",
    "人工知能", "生成AI", "大規模言語モデル", "機械学習", "ディープラーニング",
]

GAME_KEYWORDS = [
    "game", "gaming", "PlayStation", "PS5", "Xbox", "Nintendo", "Switch",
    "Steam", "esports", "indie game", "AAA", "DLC", "update", "patch",
    "ゲーム", "プレイステーション", "任天堂", "スイッチ", "ゲーム業界",
]

ALL_KEYWORDS = AI_KEYWORDS + GAME_KEYWORDS

HIGH_VALUE_SOURCES = {
    "techcrunch.com", "theverge.com", "venturebeat.com",
    "4gamer", "famitsu", "aimedia",
}


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _score_article(article: Article) -> float:
    score = 0.0
    text = (article.title + " " + article.summary).lower()

    for kw in ALL_KEYWORDS:
        if kw.lower() in text:
            score += 1.5 if kw in AI_KEYWORDS else 1.0

    age_hours = (datetime.now(timezone.utc) - article.published_at).total_seconds() / 3600
    freshness = max(0.0, 1.0 - age_hours / 24.0)
    score += freshness * 3.0

    if any(src in article.source.lower() or src in article.url.lower() for src in HIGH_VALUE_SOURCES):
        score += 1.0

    return score


def deduplicate(articles: List[Article], threshold: float = 0.72) -> List[Article]:
    unique: List[Article] = []
    for article in articles:
        is_dup = any(
            _title_similarity(article.title, u.title) >= threshold
            for u in unique
        )
        if not is_dup:
            unique.append(article)
    return unique


def filter_articles(
    articles: List[Article],
    max_count: int | None = None,
    min_count: int | None = None,
) -> List[Article]:
    max_count = max_count or config.MAX_ARTICLES
    min_count = min_count or config.MIN_ARTICLES

    articles = [a for a in articles if a.title and a.url]
    articles = deduplicate(articles)

    for a in articles:
        a.score = _score_article(a)

    articles.sort(key=lambda a: a.score, reverse=True)
    selected = articles[:max_count]

    log.info(
        "Filtered to %d articles (from %d candidates, min=%d)",
        len(selected),
        len(articles),
        min_count,
    )

    if len(selected) < min_count:
        log.warning(
            "Only %d articles selected (min=%d) – consider expanding search range",
            len(selected),
            min_count,
        )

    return selected
