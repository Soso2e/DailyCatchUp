"""News API collector for English AI/gaming news (TechCrunch, The Verge, VentureBeat, etc.)."""

from datetime import datetime, timezone, timedelta
from typing import List

import httpx

import config
from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)

NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"

SEARCH_QUERIES = [
    "artificial intelligence",
    "generative AI LLM",
    "gaming industry",
    "video game release",
    "large language model",
]

PREFERRED_SOURCES = [
    "techcrunch.com",
    "theverge.com",
    "venturebeat.com",
    "wired.com",
    "arstechnica.com",
    "engadget.com",
    "gamespot.com",
    "ign.com",
]


def collect_newsapi(max_age_hours: int = 24) -> List[Article]:
    if not config.NEWS_API_KEY:
        log.warning("NEWS_API_KEY not set – skipping News API collection")
        return []

    articles: List[Article] = []
    from_date = (
        datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    for query in SEARCH_QUERIES:
        try:
            resp = httpx.get(
                NEWSAPI_ENDPOINT,
                params={
                    "q": query,
                    "from": from_date,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "apiKey": config.NEWS_API_KEY,
                    "pageSize": 15,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                log.warning("NewsAPI non-ok status for query [%s]: %s", query, data.get("message"))
                continue

            for item in data.get("articles", []):
                pub_str = item.get("publishedAt", "")
                try:
                    pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pub_dt = datetime.now(timezone.utc)

                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                if not title or not url or title == "[Removed]":
                    continue

                source_name = item.get("source", {}).get("name") or "NewsAPI"
                articles.append(
                    Article(
                        title=title,
                        url=url,
                        published_at=pub_dt,
                        source=source_name,
                        language="en",
                        summary=item.get("description") or "",
                    )
                )

            log.debug("NewsAPI [%s]: fetched %d items", query, len(data.get("articles", [])))

        except httpx.HTTPStatusError as exc:
            log.warning("NewsAPI HTTP error [%s]: %s", query, exc)
        except Exception as exc:
            log.warning("NewsAPI error [%s]: %s", query, exc)

    log.info("NewsAPI collected %d articles total", len(articles))
    return articles
