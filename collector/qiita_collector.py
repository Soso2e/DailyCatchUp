"""Qiita API collector for Japanese AI/game tech articles."""

from datetime import datetime, timezone, timedelta
from typing import List

import httpx

import config
from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)

QIITA_ENDPOINT = "https://qiita.com/api/v2/items"

QIITA_QUERIES = [
    "AI LLM",
    "生成AI",
    "ChatGPT",
    "機械学習 深層学習",
    "ゲーム 開発",
]


def collect_qiita(max_age_hours: int = 24) -> List[Article]:
    articles: List[Article] = []
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

    headers = {}
    if config.QIITA_API_TOKEN:
        headers["Authorization"] = f"Bearer {config.QIITA_API_TOKEN}"

    for query in QIITA_QUERIES:
        try:
            resp = httpx.get(
                QIITA_ENDPOINT,
                params={"query": query, "per_page": 20, "sort": "created"},
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()

            for item in resp.json():
                pub_str = item.get("created_at", "")
                try:
                    pub_dt = datetime.fromisoformat(pub_str)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError):
                    pub_dt = datetime.now(timezone.utc)

                if pub_dt < cutoff_dt:
                    continue

                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if not title or not url:
                    continue

                likes = item.get("likes_count") or 0
                stocks = item.get("stocks_count") or 0

                articles.append(
                    Article(
                        title=title,
                        url=url,
                        published_at=pub_dt,
                        source="qiita",
                        language="ja",
                        summary=_extract_summary(item.get("body") or ""),
                        popularity=likes + stocks,
                    )
                )

            log.debug("Qiita [%s]: fetched %d items", query, len(resp.json()))

        except httpx.HTTPStatusError as exc:
            log.warning("Qiita HTTP error [%s]: %s", query, exc)
        except Exception as exc:
            log.warning("Qiita error [%s]: %s", query, exc)

    log.info("Qiita collected %d articles total", len(articles))
    return articles


def _extract_summary(body: str, max_chars: int = 200) -> str:
    """Strip markdown and return a plain-text summary snippet."""
    import re
    text = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    text = re.sub(r"[#*`>\[\]!]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]
