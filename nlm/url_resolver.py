"""Helpers for resolving Google News RSS article links to original article URLs."""

from __future__ import annotations

from html import unescape
from urllib.parse import parse_qs, urlparse

import httpx

from logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 10.0
_GOOGLE_NEWS_HOST = "news.google.com"
_URL_QUERY_KEYS = ("url", "u", "q", "target", "dest", "destination", "redirect", "redirect_url")


def _extract_embedded_url(text: str) -> str | None:
    lower = text.lower()
    for key in _URL_QUERY_KEYS:
        marker = f"{key}="
        idx = lower.find(marker)
        if idx < 0:
            continue

        value = text[idx + len(marker) :]
        for sep in ("&amp;", "&", '"', "'", " ", ">"):
            if sep in value:
                value = value.split(sep, 1)[0]

        candidate = unescape(value).strip()
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate
    return None


def resolve_url(url: str) -> str:
    """Best-effort resolution for Google News RSS redirect URLs."""
    if _GOOGLE_NEWS_HOST not in url:
        return url

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers={"User-Agent": "DailyCatchUp/1.0"},
        ) as client:
            resp = client.get(url)
            final = str(resp.url)

            if _GOOGLE_NEWS_HOST not in final:
                if final != url:
                    log.debug("Resolved %s -> %s", url[:80], final[:80])
                return final

            location = resp.headers.get("location")
            if location and _GOOGLE_NEWS_HOST not in location:
                log.debug("Resolved via location header %s -> %s", url[:80], location[:80])
                return location

            parsed = urlparse(final)
            for values in parse_qs(parsed.query).values():
                for value in values:
                    if value.startswith(("http://", "https://")):
                        log.debug("Resolved via query string %s -> %s", url[:80], value[:80])
                        return value

            html = resp.text
            for candidate in (
                _extract_embedded_url(html),
                _extract_embedded_url(final),
            ):
                if candidate and _GOOGLE_NEWS_HOST not in candidate:
                    log.debug("Resolved via embedded URL %s -> %s", url[:80], candidate[:80])
                    return candidate

        log.info("Skipping unresolved Google News redirect URL: %s", url[:120])
        return url
    except Exception as exc:
        log.warning("URL resolve failed (%s): %s - using original", url[:80], exc)
        return url
