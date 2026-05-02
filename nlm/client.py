"""teng-lin/notebooklm-py async API を同期ラッパーで包むクライアント。

Authentication: `notebooklm login` CLI で一度ログインしておく（認証情報はローカルに保存）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import List

import config
from collector.rss_collector import Article
from logger import get_logger
from nlm.url_resolver import resolve_url

log = get_logger(__name__)

NOTEBOOKLM_URL = "https://notebooklm.google.com"


@dataclass
class GenerationResult:
    notebook_id: str
    audio_task_id: str | None = None
    video_task_id: str | None = None
    audio_ready: bool = False
    video_ready: bool = False
    summary_ready: bool = False
    summary_text: str | None = None


class NotebookLMClient:
    """teng-lin/notebooklm-py の async API を同期的に呼び出すラッパー。"""

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def create_notebook(self, title: str) -> GenerationResult:
        async def _inner():
            from notebooklm import NotebookLMClient as _Lib  # type: ignore
            async with await _Lib.from_storage() as c:
                nb = await c.notebooks.create(title)
                log.info("Created notebook id=%s", nb.id)
                return GenerationResult(notebook_id=str(nb.id))

        return self._run(_inner())

    def add_sources(self, notebook_id: str, articles: List[Article]) -> int:
        async def _inner():
            from notebooklm import NotebookLMClient as _Lib  # type: ignore
            async with await _Lib.from_storage() as c:
                added = 0
                for article in articles:
                    resolved = resolve_url(article.url)
                    try:
                        await c.sources.add_url(
                            notebook_id, resolved, wait=True, wait_timeout=120.0
                        )
                        added += 1
                        log.debug("Added source: %s", article.title[:60])
                    except Exception as exc:
                        log.warning(
                            "Could not add source [%s] (resolved: %s): %s",
                            article.url[:80],
                            resolved[:80],
                            exc,
                        )
                log.info("Added %d/%d sources to notebook %s", added, len(articles), notebook_id)
                return added

        return self._run(_inner())

    def trigger_generation(
        self,
        notebook_id: str,
        *,
        audio: bool = True,
        video: bool = True,
    ) -> GenerationResult:
        async def _inner():
            from notebooklm import NotebookLMClient as _Lib  # type: ignore
            result = GenerationResult(notebook_id=notebook_id)
            async with await _Lib.from_storage() as c:
                if audio:
                    try:
                        status = await c.artifacts.generate_audio(notebook_id, language="ja")
                        result.audio_task_id = status.task_id
                        log.info("Audio generation started task_id=%s", status.task_id)
                    except Exception as exc:
                        log.warning("Audio generation trigger failed: %s", exc)
                if video:
                    try:
                        status = await c.artifacts.generate_video(notebook_id, language="ja")
                        result.video_task_id = status.task_id
                        log.info("Video generation started task_id=%s", status.task_id)
                    except Exception as exc:
                        log.warning("Video generation trigger failed: %s", exc)
            return result

        return self._run(_inner())

    def wait_for_generation(
        self,
        result: GenerationResult,
        max_wait: int | None = None,
    ) -> GenerationResult:
        """audio / video の両タスクが完了するまでブロックする。"""
        timeout = float(max_wait or config.NOTEBOOKLM_MAX_WAIT)

        async def _inner():
            from notebooklm import NotebookLMClient as _Lib  # type: ignore
            async with await _Lib.from_storage() as c:
                if result.audio_task_id:
                    try:
                        log.info(
                            "Waiting for audio (task=%s, timeout=%ds)...",
                            result.audio_task_id,
                            int(timeout),
                        )
                        st = await c.artifacts.wait_for_completion(
                            result.notebook_id, result.audio_task_id, timeout=timeout
                        )
                        result.audio_ready = st.status == "completed"
                        if not result.audio_ready:
                            log.warning("Audio ended with status=%s error=%s", st.status, st.error)
                    except TimeoutError:
                        log.warning("Audio generation timed out after %ds", int(timeout))
                    except Exception as exc:
                        log.error("Audio wait failed: %s", exc)
                else:
                    log.info("No audio task_id – skipping audio wait")

                if result.video_task_id:
                    try:
                        log.info(
                            "Waiting for video (task=%s, timeout=%ds)...",
                            result.video_task_id,
                            int(timeout),
                        )
                        st = await c.artifacts.wait_for_completion(
                            result.notebook_id, result.video_task_id, timeout=timeout
                        )
                        result.video_ready = st.status == "completed"
                        if not result.video_ready:
                            log.warning("Video ended with status=%s error=%s", st.status, st.error)
                    except TimeoutError:
                        log.warning("Video generation timed out after %ds", int(timeout))
                    except Exception as exc:
                        log.error("Video wait failed: %s", exc)
                else:
                    log.info("No video task_id – skipping video wait")

            log.info(
                "Generation complete: audio_ready=%s video_ready=%s",
                result.audio_ready,
                result.video_ready,
            )
            return result

        return self._run(_inner())

    def download_assets(
        self,
        notebook_id: str,
        output_dir: Path,
        *,
        audio: bool = True,
        video: bool = True,
    ) -> dict[str, Path | None]:
        """audio.mp3 / video.mp4 を output_dir へダウンロードする。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path | None] = {"audio": None, "video": None, "summary": None}

        async def _inner():
            from notebooklm import NotebookLMClient as _Lib  # type: ignore
            async with await _Lib.from_storage() as c:
                if audio:
                    audio_path = output_dir / "audio.mp3"
                    try:
                        await c.artifacts.download_audio(notebook_id, str(audio_path))
                        if audio_path.exists() and audio_path.stat().st_size > 0:
                            paths["audio"] = audio_path
                            log.info(
                                "Downloaded audio → %s (%.1f MB)",
                                audio_path,
                                audio_path.stat().st_size / 1_048_576,
                            )
                        else:
                            log.warning("Audio download returned empty file")
                    except Exception as exc:
                        log.error("Audio download failed: %s", exc)

                if video:
                    video_path = output_dir / "video.mp4"
                    try:
                        await c.artifacts.download_video(notebook_id, str(video_path))
                        if video_path.exists() and video_path.stat().st_size > 0:
                            paths["video"] = video_path
                            log.info(
                                "Downloaded video → %s (%.1f MB)",
                                video_path,
                                video_path.stat().st_size / 1_048_576,
                            )
                        else:
                            log.warning("Video download returned empty file")
                    except Exception as exc:
                        log.error("Video download failed: %s", exc)

            return paths

        return self._run(_inner())

    def create_notebook_with_articles(
        self, title: str, articles: List[Article]
    ) -> GenerationResult:
        result = self.create_notebook(title)
        self.add_sources(result.notebook_id, articles)
        gen = self.trigger_generation(result.notebook_id)
        result.audio_task_id = gen.audio_task_id
        result.video_task_id = gen.video_task_id
        return result
