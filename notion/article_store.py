"""Store and restore collected articles as Notion page blocks.

Articles are written under a heading_2 marker so they can be re-parsed
without touching any DB properties.  Format per item:

  • [ja][4gamer] タイトル  (title text is a link to the article URL)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from logger import get_logger

if TYPE_CHECKING:
    from collector.rss_collector import Article

log = get_logger(__name__)

_SECTION_MARKER = "収集記事"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_articles_to_page(client, page_id: str, articles: List["Article"]) -> None:
    """Replace the article section on *page_id* with *articles*."""
    _clear_article_section(client, page_id)

    blocks: list = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": _SECTION_MARKER}}]
            },
        }
    ]

    for a in articles:
        blocks.append({
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"[{a.language}][{a.source}] "}},
                    {
                        "type": "text",
                        "text": {"content": a.title, "link": {"url": a.url}},
                    },
                ]
            },
        })

    # Notion API accepts at most 100 blocks per request
    for i in range(0, len(blocks), 100):
        client.blocks.children.append(block_id=page_id, children=blocks[i : i + 100])

    log.info("Saved %d articles to Notion page %s", len(articles), page_id)


def load_articles_from_page(client, page_id: str) -> List["Article"]:
    """Return articles previously saved to *page_id*, or [] if none found."""
    from collector.rss_collector import Article  # local import to avoid circular

    blocks = _get_all_children(client, page_id)
    in_section = False
    articles: list[Article] = []

    for block in blocks:
        btype = block.get("type")

        if btype == "heading_2":
            in_section = _plain_text(block["heading_2"]["rich_text"]) == _SECTION_MARKER
            continue

        if btype in ("heading_1", "heading_3") and in_section:
            break  # next top-level section reached

        if in_section and btype == "bulleted_list_item":
            article = _parse_item(block["bulleted_list_item"]["rich_text"])
            if article:
                articles.append(article)

    log.info("Loaded %d articles from Notion page %s", len(articles), page_id)
    return articles


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _clear_article_section(client, page_id: str) -> None:
    """Delete the existing article section blocks so we can overwrite."""
    blocks = _get_all_children(client, page_id)
    in_section = False
    to_delete: list[str] = []

    for block in blocks:
        btype = block.get("type")

        if btype == "heading_2":
            if _plain_text(block["heading_2"]["rich_text"]) == _SECTION_MARKER:
                in_section = True
                to_delete.append(block["id"])
                continue
            elif in_section:
                break  # hit the next heading_2, stop

        if in_section:
            if btype in ("heading_1", "heading_3"):
                break
            to_delete.append(block["id"])

    for block_id in to_delete:
        try:
            client.blocks.delete(block_id=block_id)
        except Exception as exc:
            log.warning("Could not delete block %s: %s", block_id, exc)


def _get_all_children(client, page_id: str) -> list:
    results: list = []
    cursor: str | None = None
    while True:
        kwargs: dict = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.blocks.children.list(**kwargs)
        results.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return results


def _plain_text(rich_text: list) -> str:
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def _parse_item(rich_text: list) -> Optional["Article"]:
    """Parse `[lang][source] title` rich_text back into an Article."""
    from collector.rss_collector import Article

    if not rich_text:
        return None

    full_text = _plain_text(rich_text)

    url: str | None = None
    for rt in rich_text:
        link = rt.get("text", {}).get("link")
        if link and link.get("url"):
            url = link["url"]
            break

    if not url:
        return None

    m = re.match(r"^\[([^\]]+)\]\[([^\]]+)\] (.+)$", full_text)
    if not m:
        return None

    return Article(
        title=m.group(3),
        url=url,
        published_at=datetime.now(timezone.utc),
        source=m.group(2),
        language=m.group(1),
    )
