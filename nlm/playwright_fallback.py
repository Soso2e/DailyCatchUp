"""Playwright-based browser automation fallback for NotebookLM.

Uses a persistent Chrome profile (userDataDir) so Google does not flag the
browser as automated.  Run `python scripts/save_session.py` once to perform
the initial manual login; subsequent pipeline runs reuse that profile.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List

import config
from collector.rss_collector import Article
from logger import get_logger
from nlm.client import GenerationResult

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
        page.wait_for_selector("body", timeout=30_000)

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

    def _wait_for_workspace_entry(self) -> None:
        """Wait until the NotebookLM landing UI is interactive."""
        page = self._page
        page.wait_for_selector("body", timeout=30_000)
        page.wait_for_selector(
            "button, [role='button'], [role='main']",
            timeout=30_000,
        )

    def _wait_for_notebook_editor(self) -> None:
        """NotebookLM is an SPA, so wait through each transition stage."""
        page = self._page

        try:
            page.wait_for_url("**/notebook/**", timeout=30_000)
        except Exception:
            log.debug("Notebook editor URL not reached yet; current url=%s", page.url)

        page.wait_for_selector(
            "input, textarea, dialog, [role='dialog'], [role='main']",
            timeout=30_000,
        )

        textarea = page.locator("textarea")
        if textarea.count():
            textarea.first.wait_for(timeout=10_000)
            return

        page.wait_for_selector("textarea", timeout=30_000)

    def _find_url_input(self):
        page = self._page
        selectors = [
            'input[type="url"]',
            'input[placeholder*="url" i]',
            'input[placeholder*="website" i]',
            'input[aria-label*="url" i]',
            'input[aria-label*="website" i]',
            "textarea",
            'input[type="text"]',
            "[contenteditable='true']",
        ]

        for selector in selectors:
            locator = page.locator(selector)
            if locator.count():
                return locator.first

        textbox = page.get_by_role("textbox")
        if textbox.count():
            return textbox.first

        return None

    def _dismiss_overlay(self) -> None:
        page = self._page
        page.keyboard.press("Escape")
        try:
            page.wait_for_selector(".cdk-overlay-backdrop", state="hidden", timeout=3_000)
        except Exception:
            backdrop = page.locator(".cdk-overlay-backdrop")
            if backdrop.count():
                try:
                    backdrop.last.click(force=True)
                    page.wait_for_timeout(500)
                except Exception:
                    pass

    def create_notebook_with_articles(
        self, title: str, articles: List[Article]
    ) -> GenerationResult:
        result = self.create_notebook(title)
        self.add_sources(result.notebook_id, articles)
        self.trigger_generation(result.notebook_id)
        return result

    def create_notebook(self, title: str) -> GenerationResult:
        self._launch()
        try:
            self._check_logged_in()
            notebook_id = self._create_notebook(title)
            return GenerationResult(notebook_id=notebook_id)
        finally:
            self._close()

    def add_sources(self, notebook_id: str, articles: List[Article]) -> int:
        self._launch()
        try:
            self._check_logged_in()
            self._open_notebook(notebook_id)
            added = 0
            for article in articles:
                if self._add_source_url(article.url):
                    added += 1
            log.info("Playwright added %d/%d sources", added, len(articles))
            return added
        finally:
            self._close()

    def trigger_generation(
        self,
        notebook_id: str,
        *,
        audio: bool = True,
        video: bool = True,
    ) -> GenerationResult:
        self._launch()
        try:
            self._check_logged_in()
            self._open_notebook(notebook_id)
            if audio:
                self._trigger_audio()
            if video:
                self._trigger_video()
            return GenerationResult(notebook_id=notebook_id)
        finally:
            self._close()

    def _create_notebook(self, title: str) -> str:
        page = self._page
        page.goto(NOTEBOOKLM_URL)
        self._wait_for_workspace_entry()

        new_btn = page.get_by_role(
            "button", name=re.compile(r"新規|create|notebook", re.IGNORECASE)
        )
        new_btn.first.wait_for(timeout=15_000)
        new_btn.first.click()
        page.wait_for_timeout(2000)

        title_input = page.locator(
            'input[placeholder*="title" i], input[placeholder*="タイトル"],'
            ' input[aria-label*="title" i], input[aria-label*="タイトル"]'
        )
        if title_input.count():
            title_input.first.fill(title)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1000)

        self._wait_for_notebook_editor()
        url = page.url
        notebook_id = url.split("/")[-1] if "/" in url else f"pw_{int(time.time())}"
        log.info("Playwright created notebook id=%s", notebook_id)
        return notebook_id

    def _open_notebook(self, notebook_id: str) -> None:
        page = self._page
        page.goto(f"{NOTEBOOKLM_URL}/notebook/{notebook_id}")
        self._wait_for_notebook_editor()

    def _add_source_url(self, url: str) -> bool:
        page = self._page
        try:
            log.debug("Add source start: page.url=%s target=%s", page.url, url)

            add_btn = page.get_by_role(
                "button", name=re.compile(r"ソース|add.?source|追加", re.IGNORECASE)
            )
            if "addSource=true" not in page.url and add_btn.count():
                add_btn.first.click()
                page.wait_for_timeout(1000)

            for role in ("button", "tab"):
                url_tab = page.get_by_role(
                    role, name=re.compile(r"website|url|link|ウェブ", re.IGNORECASE)
                )
                if url_tab.count():
                    url_tab.first.click()
                    page.wait_for_timeout(500)
                    break

            self._dismiss_overlay()
            page.wait_for_selector(
                "input, textarea, [contenteditable='true'], [role='textbox']",
                timeout=10_000,
            )
            url_input = self._find_url_input()
            if url_input is None:
                raise RuntimeError(f"URL input not found on page {page.url}")

            url_input.wait_for(timeout=5_000)
            self._dismiss_overlay()
            url_input.focus()
            try:
                url_input.fill("")
            except Exception:
                pass
            url_input.fill(url)
            url_input.press("Enter")
            page.wait_for_timeout(3000)
            log.debug("Playwright added source: %s", url[:60])
            return True
        except Exception as exc:
            log.warning("Playwright add_source failed for %s at %s: %s", url, page.url, exc)
            return False

    def _trigger_audio(self) -> bool:
        page = self._page
        try:
            audio_btn = page.get_by_role(
                "button", name=re.compile(r"audio.?overview|音声概要|音声", re.IGNORECASE)
            )
            if audio_btn.count():
                audio_btn.first.click()
                page.wait_for_timeout(2000)
                generate_btn = page.get_by_role(
                    "button", name=re.compile(r"generate|生成", re.IGNORECASE)
                )
                if generate_btn.count():
                    generate_btn.first.click()
                log.info("Playwright triggered audio generation")
                return True
        except Exception as exc:
            log.warning("Playwright audio trigger failed: %s", exc)
        return False

    def _trigger_video(self) -> bool:
        page = self._page
        try:
            video_btn = page.get_by_role(
                "button", name=re.compile(r"video|動画", re.IGNORECASE)
            )
            if video_btn.count():
                video_btn.first.click()
                page.wait_for_timeout(2000)
                generate_btn = page.get_by_role(
                    "button", name=re.compile(r"generate|生成", re.IGNORECASE)
                )
                if generate_btn.count():
                    generate_btn.first.click()
                log.info("Playwright triggered video generation")
                return True
        except Exception as exc:
            log.warning("Playwright video trigger failed: %s", exc)
        return False

    def get_status(self, notebook_id: str) -> GenerationResult:
        self._launch()
        try:
            self._check_logged_in()
            page = self._page
            page.goto(f"{NOTEBOOKLM_URL}/notebook/{notebook_id}")
            self._wait_for_notebook_editor()

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
