"""Helpers for resolving Google News RSS article links to original article URLs."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, urlparse

import httpx

from logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 10.0
_GOOGLE_NEWS_HOST = "news.google.com"
_GOOGLE_NEWS_ARTICLE_RE = re.compile(
    r"^/(?:__i/rss/rd/|rss/)?articles/(?P<id>[^/?#]+)"
)
_BATCH_RESULT_RE = re.compile(r'\["garturlres","(https?://[^"]+)"')
_DATA_ATTR_RE = re.compile(r'data-n-a-(?P<kind>sg|ts)="(?P<value>[^"]+)"')
_URL_BYTES_RE = re.compile(rb"(https?://[^\s\"'<>\\\x00-\x1f\xd2]+)")
_URL_QUERY_KEYS = ("url", "u", "q", "target", "dest", "destination", "redirect", "redirect_url")
_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\'][^"\']*canonical[^"\']*["\'][^>]+href=["\'](?P<url>https?://[^"\']+)["\']',
    re.IGNORECASE,
)
_OG_URL_RE = re.compile(
    r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\'](?P<url>https?://[^"\']+)["\']',
    re.IGNORECASE,
)


@dataclass
class URLResolution:
    original_url: str
    resolved_url: str | None
    skip_reason: str | None = None

    @property
    def should_skip(self) -> bool:
        return bool(self.skip_reason or not self.resolved_url)


def is_google_news_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == _GOOGLE_NEWS_HOST


def _truncate(text: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _sanitize_candidate_urls(candidate_urls: list[str] | None) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidate_urls or []:
        normalized = (candidate or "").strip()
        if (
            not normalized
            or not normalized.startswith(("http://", "https://"))
            or is_google_news_url(normalized)
            or normalized in seen
        ):
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


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
        if candidate.startswith(("http://", "https://")):
            return candidate
    return None


def _extract_article_id(url: str) -> str | None:
    match = _GOOGLE_NEWS_ARTICLE_RE.match(urlparse(url).path)
    return match.group("id") if match else None


def _decode_base64_article_id(article_id: str) -> str | None:
    try:
        padded = article_id + "=" * (-len(article_id) % 4)
        decoded = base64.urlsafe_b64decode(padded)
    except Exception:
        return None

    match = _URL_BYTES_RE.search(decoded)
    if not match:
        return None

    candidate = match.group(1).decode("utf-8", errors="ignore").strip()
    if candidate.startswith(("http://", "https://")) and not is_google_news_url(candidate):
        return candidate
    return None


def _extract_signature_and_timestamp(html: str) -> tuple[str | None, str | None]:
    signature = None
    timestamp = None
    for match in _DATA_ATTR_RE.finditer(html):
        if match.group("kind") == "sg":
            signature = match.group("value")
        elif match.group("kind") == "ts":
            timestamp = match.group("value")
    return signature, timestamp


def _extract_canonical_url(html: str) -> str | None:
    for pattern in (_CANONICAL_RE, _OG_URL_RE):
        match = pattern.search(html)
        if not match:
            continue
        candidate = match.group("url").strip()
        if candidate.startswith(("http://", "https://")) and not is_google_news_url(candidate):
            return candidate
    return None


def _log_http_status_error(context: str, response: httpx.Response) -> None:
    log.warning(
        "%s HTTPStatusError: status_code=%s final_url=%s body_head=%s",
        context,
        response.status_code,
        str(response.url)[:200],
        _truncate(response.text),
    )


def _decode_via_batchexecute(
    client: httpx.Client,
    *,
    article_id: str,
    timestamp: str,
    signature: str,
) -> str | None:
    articles_req = [[
        "Fbv4je",
        json.dumps(
            [
                "garturlreq",
                [
                    [
                        "X",
                        "X",
                        ["X", "X"],
                        None,
                        None,
                        1,
                        1,
                        "US:en",
                        None,
                        1,
                        None,
                        None,
                        None,
                        None,
                        None,
                        0,
                        1,
                    ],
                    "X",
                    "X",
                    1,
                    [1, 1, 1],
                    1,
                    1,
                    None,
                    0,
                    0,
                    None,
                    0,
                ],
                article_id,
                int(timestamp),
                signature,
            ],
            separators=(",", ":"),
        ),
    ]]

    resp = client.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        params={"rpcids": "Fbv4je"},
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": "https://news.google.com/",
        },
        data={"f.req": json.dumps(articles_req, separators=(",", ":"))},
    )
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError:
        _log_http_status_error("Google News batchexecute", resp)
        return None

    match = _BATCH_RESULT_RE.search(resp.text)
    if not match:
        return None

    candidate = match.group(1).encode("utf-8").decode("unicode_escape").strip()
    if candidate.startswith(("http://", "https://")) and not is_google_news_url(candidate):
        return candidate
    return None


def resolve_url(url: str, candidate_urls: list[str] | None = None) -> URLResolution:
    """Best-effort resolution for Google News RSS redirect URLs."""
    if not is_google_news_url(url):
        return URLResolution(original_url=url, resolved_url=url)

    candidates = _sanitize_candidate_urls(candidate_urls)
    if candidates:
        return URLResolution(original_url=url, resolved_url=candidates[0])

    article_id = _extract_article_id(url)
    if article_id:
        decoded = _decode_base64_article_id(article_id)
        if decoded:
            return URLResolution(original_url=url, resolved_url=decoded)

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers={"User-Agent": "DailyCatchUp/1.0"},
        ) as client:
            resp = client.get(url)
            final = str(resp.url)
            if resp.is_error:
                _log_http_status_error("Google News article fetch", resp)

            if not is_google_news_url(final):
                if final != url:
                    log.debug("Resolved Google News redirect %s -> %s", url[:80], final[:80])
                return URLResolution(original_url=url, resolved_url=final)

            location = resp.headers.get("location")
            if location and not is_google_news_url(location):
                return URLResolution(original_url=url, resolved_url=location)

            parsed = urlparse(final)
            for values in parse_qs(parsed.query).values():
                for value in values:
                    if value.startswith(("http://", "https://")) and not is_google_news_url(value):
                        return URLResolution(original_url=url, resolved_url=value)

            html = resp.text
            for candidate in (
                _extract_canonical_url(html),
                _extract_embedded_url(html),
                _extract_embedded_url(final),
            ):
                if candidate and not is_google_news_url(candidate):
                    return URLResolution(original_url=url, resolved_url=candidate)

            if article_id:
                signature, timestamp = _extract_signature_and_timestamp(html)
                if signature and timestamp:
                    candidate = _decode_via_batchexecute(
                        client,
                        article_id=article_id,
                        timestamp=timestamp,
                        signature=signature,
                    )
                    if candidate:
                        return URLResolution(original_url=url, resolved_url=candidate)

        return URLResolution(
            original_url=url,
            resolved_url=None,
            skip_reason="google_news_unresolved",
        )
    except Exception as exc:
        return URLResolution(
            original_url=url,
            resolved_url=None,
            skip_reason=f"google_news_resolve_error:{type(exc).__name__}",
        )
