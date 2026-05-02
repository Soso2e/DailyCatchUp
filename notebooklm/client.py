"""notebooklm-py wrapper.

Wraps the notebooklm-py library to create notebooks, add article sources,
and trigger audio/video/summary generation.  Falls back to PlaywrightClient
automatically on any failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

import config
from collector.rss_collector import Article
from logger import get_logger

log = get_logger(__name__)


@dataclass
class GenerationResult:
    notebook_id: str
    audio_url: str | None = None
    video_url: str | None = None
    summary_text: str | None = None
    audio_ready: bool = False
    video_ready: bool = False
    summary_ready: bool = False


class NotebookLMClient:
    """Thin wrapper around notebooklm-py with automatic Playwright fallback."""

    def __init__(self) -> None:
        self._client = None
        self._use_playwright = False

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            # notebooklm-py: https://github.com/JustinSteventon/notebooklm-py
            from notebooklm import NotebookLM  # type: ignore

            self._client = NotebookLM(
                email=config.GOOGLE_EMAIL,
                password=config.GOOGLE_PASSWORD,
                session_file=config.NOTEBOOKLM_SESSION_FILE,
            )
            log.info("notebooklm-py client initialized")
        except Exception as exc:
            log.warning("notebooklm-py unavailable (%s) – switching to Playwright fallback", exc)
            self._use_playwright = True
            from notebooklm.playwright_fallback import PlaywrightClient

            self._client = PlaywrightClient()
        return self._client

    def create_notebook_with_articles(
        self, title: str, articles: List[Article]
    ) -> GenerationResult:
        result = self.create_notebook(title)
        self.add_sources(result.notebook_id, articles)
        self.trigger_generation(result.notebook_id)
        return result

    def create_notebook(self, title: str) -> GenerationResult:
        client = self._get_client()
        try:
            if self._use_playwright:
                return client.create_notebook(title)
            notebook = client.create_notebook(title=title)
            notebook_id: str = str(notebook.id)
            log.info("Created notebook id=%s", notebook_id)
            return GenerationResult(notebook_id=notebook_id)
        except Exception as exc:
            log.error("NotebookLM create failed: %s", exc)
            if not self._use_playwright:
                log.info("Retrying notebook create with Playwright fallback")
                self._client = None
                self._use_playwright = True
                return self.create_notebook(title)
            raise

    def add_sources(self, notebook_id: str, articles: List[Article]) -> int:
        client = self._get_client()
        try:
            if self._use_playwright:
                return client.add_sources(notebook_id, articles)

            notebook = client.get_notebook(notebook_id)
            added = 0
            for article in articles:
                try:
                    notebook.add_source(url=article.url, title=article.title)
                    added += 1
                    log.debug("Added source: %s", article.title[:60])
                except Exception as exc:
                    log.warning("Could not add source [%s]: %s", article.url, exc)
            return added
        except Exception as exc:
            log.error("NotebookLM add_sources failed: %s", exc)
            if not self._use_playwright:
                log.info("Retrying add_sources with Playwright fallback")
                self._client = None
                self._use_playwright = True
                return self.add_sources(notebook_id, articles)
            raise

    def trigger_generation(
        self,
        notebook_id: str,
        *,
        audio: bool = True,
        video: bool = True,
    ) -> GenerationResult:
        client = self._get_client()
        try:
            if self._use_playwright:
                return client.trigger_generation(notebook_id, audio=audio, video=video)

            notebook = client.get_notebook(notebook_id)
            if audio:
                log.info("Triggering Audio Overview generation")
                notebook.generate_audio_overview()
            if video:
                log.info("Triggering Video Overview generation")
                try:
                    notebook.generate_video_overview()
                except Exception as exc:
                    log.warning("Video generation not supported or failed: %s", exc)

            return GenerationResult(notebook_id=notebook_id)
        except Exception as exc:
            log.error("NotebookLM trigger_generation failed: %s", exc)
            if not self._use_playwright:
                log.info("Retrying trigger_generation with Playwright fallback")
                self._client = None
                self._use_playwright = True
                return self.trigger_generation(notebook_id, audio=audio, video=video)
            raise

    def get_status(self, notebook_id: str) -> GenerationResult:
        client = self._get_client()
        try:
            if self._use_playwright:
                return client.get_status(notebook_id)
            notebook = client.get_notebook(notebook_id)
            result = GenerationResult(notebook_id=notebook_id)

            audio = getattr(notebook, "audio_overview", None)
            if audio and getattr(audio, "is_ready", False):
                result.audio_ready = True
                result.audio_url = getattr(audio, "download_url", None)

            video = getattr(notebook, "video_overview", None)
            if video and getattr(video, "is_ready", False):
                result.video_ready = True
                result.video_url = getattr(video, "download_url", None)

            summary = getattr(notebook, "summary", None)
            if summary:
                result.summary_ready = True
                result.summary_text = str(summary)

            return result
        except Exception as exc:
            log.error("get_status failed for notebook %s: %s", notebook_id, exc)
            raise
