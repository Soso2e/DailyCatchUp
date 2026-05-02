"""Google News RSS URL → 元記事URL への解決ユーティリティ。"""

from __future__ import annotations

import httpx

from logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 10.0
_GOOGLE_NEWS_HOST = "news.google.com"


def resolve_url(url: str) -> str:
    """URLを正規化して返す。Google NewsのリダイレクトURLは元記事URLへ解決する。"""
    if _GOOGLE_NEWS_HOST not in url:
        return url

    try:
        with httpx.Client(follow_redirects=True, timeout=_TIMEOUT) as client:
            resp = client.head(url)
            final = str(resp.url)
            # HEAD が効かないサイトは GET でリトライ
            if _GOOGLE_NEWS_HOST in final:
                resp = client.get(url)
                final = str(resp.url)
        if final != url:
            log.debug("Resolved %s -> %s", url[:80], final[:80])
        return final
    except Exception as exc:
        log.warning("URL resolve failed (%s): %s – using original", url[:80], exc)
        return url
