"""Hacker News API collector — surfaces community-validated AI/game stories."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import httpx

from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)

HN_BASE = "https://hacker-news.firebaseio.com/v0"
MAX_STORIES_TO_CHECK = 100
MAX_WORKERS = 10
MIN_SCORE = 10  # ignore stories with too few upvotes

AI_GAME_KEYWORDS = [
    # AI
    "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
    "machine learning", "deep learning", "neural", "generative",
    "artificial intelligence", "language model",
    # Game
    "game", "gaming", "playstation", "xbox", "nintendo", "steam",
    "indie", "esports", "unreal", "unity",
]


def _fetch_item(story_id: int, client: httpx.Client) -> Optional[dict]:
    try:
        resp = client.get(f"{HN_BASE}/item/{story_id}.json", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _is_relevant(item: dict) -> bool:
    title = (item.get("title") or "").lower()
    return any(kw in title for kw in AI_GAME_KEYWORDS)


def collect_hackernews(max_age_hours: int = 24) -> List[Article]:
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    articles: List[Article] = []

    try:
        with httpx.Client() as client:
            # Combine top + best for broader coverage
            top_ids = client.get(f"{HN_BASE}/topstories.json", timeout=15).json()
            best_ids = client.get(f"{HN_BASE}/beststories.json", timeout=15).json()

        # Merge, deduplicate, take first MAX_STORIES_TO_CHECK
        seen: set[int] = set()
        candidate_ids: list[int] = []
        for sid in top_ids + best_ids:
            if sid not in seen:
                seen.add(sid)
                candidate_ids.append(sid)
            if len(candidate_ids) >= MAX_STORIES_TO_CHECK:
                break

    except Exception as exc:
        log.warning("HackerNews fetch story list error: %s", exc)
        return []

    with httpx.Client() as client:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_item, sid, client): sid for sid in candidate_ids}
            items = [f.result() for f in as_completed(futures) if f.result()]

    for item in items:
        if not item or item.get("type") != "story":
            continue
        if not _is_relevant(item):
            continue

        score = item.get("score") or 0
        if score < MIN_SCORE:
            continue

        pub_dt = datetime.fromtimestamp(item.get("time", 0), tz=timezone.utc)
        if pub_dt < cutoff_dt:
            continue

        title = (item.get("title") or "").strip()
        url = (item.get("url") or f"https://news.ycombinator.com/item?id={item['id']}").strip()
        if not title:
            continue

        articles.append(
            Article(
                title=title,
                url=url,
                published_at=pub_dt,
                source="hackernews",
                language="en",
                summary="",
                popularity=score,
            )
        )

    log.info("HackerNews collected %d articles total", len(articles))
    return articles
