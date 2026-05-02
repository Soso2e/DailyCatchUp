"""Playwright-based browser automation fallback for NotebookLM.

Uses a persistent Chrome profile (userDataDir) so Google does not flag the
browser as automated.  Run `python scripts/save_session.py` once to perform
the initial manual login; subsequent pipeline runs reuse that profile.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List

import config
from collector.rss_collector import Article
from logger import get_logger
from notebooklm.client import GenerationResult

log = get_logger(__name__)

NOTEBOOKLM_URL = "https://notebooklm.google.com"


class PlaywrightClient:
    """Controls NotebookLM via a persistent Chrome profile."""

    def __init__(self) -> None:
        self._context = None
        self._page = None
        self._profile_dir = Path(config.CHROME_PROFILE_DIR)

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    def _launch(self) -> None:
        from playwright.sync_api import sync_playwright  # type: ignore

        if not self._profile_dir.exists():
            msg = (
                f"Chrome profile not found at {self._profile_dir}. "
                "Run `python scripts/save_session.py` to log in and create it."
            )
            raise RuntimeError(msg)

        self._pw_cm = sync_playwright()
        self._pw = self._pw_cm.__enter__()
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def _close(self) -> None:
        if self._context:
            self._context.close()
            self._pw_cm.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # Google session check
    # ------------------------------------------------------------------

    def _check_logged_in(self) -> None:
        page = self._page
        page.goto(NOTEBOOKLM_URL)
        page.wait_for_load_state("networkidle", timeout=30_000)

        if "notebooklm.google.com" in page.url and "accounts.google.com" not in page.url:
            log.info("Chrome profile session valid")
            return

        msg = (
            "Google session expired. "
            "Run `python scripts/save_session.py` to renew the session."
        )
        log.error(msg)
        self._notify_auth_required()
        raise RuntimeError(msg)

    def _notify_auth_required(self) -> None:
        try:
            import httpx

            if config.DISCORD_WEBHOOK_URL:
                httpx.post(
                    config.DISCORD_WEBHOOK_URL,
                    json={"content": "⚠️ **DailyCatchUp** Google session expired. Run `python scripts/save_session.py` to renew."},
                    timeout=10,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Notebook operations
    # ------------------------------------------------------------------

    def create_notebook_with_articles(
        self, title: str, articles: List[Article]
    ) -> GenerationResult:
        self._launch()
        try:
            self._check_logged_in()
            notebook_id = self._create_notebook(title)
            for article in articles:
                self._add_source_url(article.url)
            self._trigger_audio()
            self._trigger_video()
            return GenerationResult(notebook_id=notebook_id)
        finally:
            self._close()

    def _create_notebook(self, title: str) -> str:
        page = self._page
        page.goto(NOTEBOOKLM_URL)
        page.wait_for_load_state("networkidle", timeout=30_000)

        new_btn = page.locator("button:has-text('New notebook'), button:has-text('新規ノートブック')")
        new_btn.first.click()
        page.wait_for_timeout(2000)

        title_input = page.locator('input[placeholder*="title"], input[placeholder*="タイトル"]')
        if title_input.count():
            title_input.first.fill(title)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1000)

        page.wait_for_load_state("networkidle", timeout=30_000)
        url = page.url
        notebook_id = url.split("/")[-1] if "/" in url else f"pw_{int(time.time())}"
        log.info("Playwright created notebook id=%s", notebook_id)
        return notebook_id

    def _add_source_url(self, url: str) -> None:
        page = self._page
        try:
            add_btn = page.locator("button:has-text('Add source'), button:has-text('ソースを追加')")
            add_btn.first.click()
            page.wait_for_timeout(1000)

            url_tab = page.locator("button:has-text('Website'), a:has-text('Website')")
            if url_tab.count():
                url_tab.first.click()
                page.wait_for_timeout(500)

            url_input = page.locator('input[placeholder*="URL"], input[type="url"]')
            url_input.first.fill(url)
            page.keyboard.press("Enter")
            page.wait_for_timeout(3000)
            log.debug("Playwright added source: %s", url[:60])
        except Exception as exc:
            log.warning("Playwright add_source failed for %s: %s", url, exc)

    def _trigger_audio(self) -> None:
        page = self._page
        try:
            audio_btn = page.locator(
                "button:has-text('Audio Overview'), button:has-text('音声概要')"
            )
            if audio_btn.count():
                audio_btn.first.click()
                page.wait_for_timeout(2000)
                generate_btn = page.locator("button:has-text('Generate'), button:has-text('生成')")
                if generate_btn.count():
                    generate_btn.first.click()
                log.info("Playwright triggered audio generation")
        except Exception as exc:
            log.warning("Playwright audio trigger failed: %s", exc)

    def _trigger_video(self) -> None:
        page = self._page
        try:
            video_btn = page.locator(
                "button:has-text('Video'), button:has-text('動画')"
            )
            if video_btn.count():
                video_btn.first.click()
                page.wait_for_timeout(2000)
                generate_btn = page.locator("button:has-text('Generate'), button:has-text('生成')")
                if generate_btn.count():
                    generate_btn.first.click()
                log.info("Playwright triggered video generation")
        except Exception as exc:
            log.warning("Playwright video trigger failed: %s", exc)

    def get_status(self, notebook_id: str) -> GenerationResult:
        self._launch()
        try:
            self._check_logged_in()
            page = self._page
            page.goto(f"{NOTEBOOKLM_URL}/notebook/{notebook_id}")
            page.wait_for_load_state("networkidle", timeout=30_000)

            result = GenerationResult(notebook_id=notebook_id)

            audio_download = page.locator(
                "a[href*='.mp3'], button:has-text('Download audio')"
            )
            if audio_download.count():
                result.audio_ready = True
                result.audio_url = audio_download.first.get_attribute("href")

            video_download = page.locator(
                "a[href*='.mp4'], button:has-text('Download video')"
            )
            if video_download.count():
                result.video_ready = True
                result.video_url = video_download.first.get_attribute("href")

            return result
        finally:
            self._close()
