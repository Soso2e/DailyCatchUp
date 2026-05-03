"""Deduplication, importance scoring, and article selection."""

import math
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import List

import config
from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Keyword lists
# ---------------------------------------------------------------------------

AI_KEYWORDS = [
    "AI", "artificial intelligence", "generative AI", "ChatGPT", "GPT",
    "Claude", "Gemini", "LLM", "large language model", "machine learning",
    "deep learning", "neural network", "OpenAI", "Anthropic", "Google AI",
    "Mistral", "Llama", "Stable Diffusion", "diffusion model",
    "人工知能", "生成AI", "大規模言語モデル", "機械学習", "ディープラーニング",
    "深層学習", "基盤モデル",
]

GAME_KEYWORDS = [
    "game", "gaming", "PlayStation", "PS5", "Xbox", "Nintendo", "Switch",
    "Steam", "esports", "indie game", "AAA", "DLC", "update", "patch",
    "Unreal Engine", "Unity", "game engine",
    "ゲーム", "プレイステーション", "任天堂", "スイッチ", "ゲーム業界",
    "ゲームエンジン", "eスポーツ",
]

ALL_KEYWORDS = AI_KEYWORDS + GAME_KEYWORDS

# Innovation signals → higher score
INNOVATION_KEYWORDS = [
    # English
    "breakthrough", "revolutionary", "first ever", "world first", "unprecedented",
    "state-of-the-art", "open source", "open-source", "research", "paper",
    "new model", "releases", "launches", "announces", "introduces",
    # Japanese
    "世界初", "初公開", "新モデル", "発表", "リリース", "オープンソース",
    "研究", "論文", "新機能", "新発表",
]

HIGH_VALUE_SOURCES = {
    "techcrunch.com", "theverge.com", "venturebeat.com",
    "4gamer", "famitsu", "aimedia",
    "hackernews", "qiita", "zenn",
    "hatena_bookmark_it",
}

# ---------------------------------------------------------------------------
# Seminar / event exclusion
# ---------------------------------------------------------------------------

# Strong single-phrase signals that clearly mark an event/seminar article
_SEMINAR_STRONG_PATTERNS = [
    r"開催のお知らせ", r"開催します", r"参加者募集", r"申し込み受付", r"申込受付",
    r"参加無料", r"無料セミナー", r"無料ウェビナー", r"無料webinar",
    r"セミナー申込", r"ウェビナー登録",
    r"webinar registration", r"register now", r"join us for", r"free workshop",
    r"sign up (?:for )?free", r"seats? (are )?limited",
]
_SEMINAR_STRONG_RE = re.compile(
    "|".join(_SEMINAR_STRONG_PATTERNS), re.IGNORECASE
)

# Weak signals — two or more together indicate a seminar article
_SEMINAR_WEAK = [
    "セミナー", "ウェビナー", "勉強会", "申し込み", "申込",
    "webinar", "seminar", "workshop",
]


def _is_seminar_or_event(article: Article) -> bool:
    """Return True if the article appears to be a seminar/event announcement."""
    text = (article.title + " " + article.summary[:150]).lower()

    if _SEMINAR_STRONG_RE.search(text):
        return True

    weak_hits = sum(1 for kw in _SEMINAR_WEAK if kw in text)
    return weak_hits >= 2


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _score_article(article: Article) -> float:
    score = 0.0
    text = (article.title + " " + article.summary).lower()

    # Keyword relevance
    for kw in ALL_KEYWORDS:
        if kw.lower() in text:
            score += 1.5 if kw in AI_KEYWORDS else 1.0

    # Innovation bonus
    for kw in INNOVATION_KEYWORDS:
        if kw.lower() in text:
            score += 0.5

    # Freshness (dominant signal)
    age_hours = (datetime.now(timezone.utc) - article.published_at).total_seconds() / 3600
    freshness = max(0.0, 1.0 - age_hours / 24.0)
    score += freshness * 3.0

    # Source authority
    if any(src in article.source.lower() or src in article.url.lower() for src in HIGH_VALUE_SOURCES):
        score += 1.0

    # Community engagement signal (log-scaled to avoid domination)
    if article.popularity > 0:
        score += math.log10(article.popularity + 1) * 0.8

    return score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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

    # Exclude seminar / event announcement articles
    before = len(articles)
    articles = [a for a in articles if not _is_seminar_or_event(a)]
    excluded = before - len(articles)
    if excluded:
        log.info("Excluded %d seminar/event articles", excluded)

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
