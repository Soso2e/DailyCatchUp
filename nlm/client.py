"""NotebookLM client wrapper used by the pipeline."""

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
    audio_status: str | None = None
    video_status: str | None = None
    audio_error: str | None = None
    video_error: str | None = None
    audio_ready: bool = False
    video_ready: bool = False
    summary_ready: bool = False
    summary_text: str | None = None


@dataclass
class AddSourcesResult:
    total: int
    added: int = 0
    added_by_url: int = 0
    added_by_text: int = 0
    resolved: int = 0
    resolved_url_none: int = 0
    skipped: int = 0


def _build_text_fallback(article: Article, original_url: str) -> str:
    sections = [
        f"Title: {article.title}",
        f"Source: {article.source}",
        f"Language: {article.language}",
        f"PublishedAt: {article.published_at.isoformat()}",
        f"OriginalURL: {original_url}",
    ]

    if article.original_url_candidates:
        sections.append("OriginalURLCandidates:\n" + "\n".join(article.original_url_candidates[:5]))

    summary = (article.summary or "").strip()
    excerpt = (article.text_excerpt or "").strip()
    if summary:
        sections.append(f"Summary:\n{summary}")
    if excerpt and excerpt != summary:
        sections.append(f"Excerpt:\n{excerpt}")

    return "\n\n".join(section for section in sections if section).strip()


class NotebookLMClient:
    """Thin wrapper around `notebooklm-py`."""

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

    def add_sources(self, notebook_id: str, articles: List[Article]) -> AddSourcesResult:
        async def _inner():
            from notebooklm import NotebookLMClient as _Lib  # type: ignore

            async with await _Lib.from_storage() as c:
                stats = AddSourcesResult(total=len(articles))
                for article in articles:
                    resolution = resolve_url(
                        article.url,
                        candidate_urls=article.original_url_candidates,
                    )

                    if resolution.resolved_url and "news.google.com" not in resolution.resolved_url:
                        stats.resolved += 1
                        resolved = resolution.resolved_url
                        try:
                            await c.sources.add_url(
                                notebook_id, resolved, wait=True, wait_timeout=120.0
                            )
                            stats.added += 1
                            stats.added_by_url += 1
                            log.info(
                                "Added source [%s]: original_url=%s resolved_url=%s skip_reason=%s",
                                article.source,
                                resolution.original_url[:120],
                                resolved[:120],
                                "none",
                            )
                            continue
                        except Exception as exc:
                            log.warning(
                                "Could not add URL source [%s]: original_url=%s resolved_url=%s skip_reason=%s error=%s",
                                article.source,
                                resolution.original_url[:80],
                                resolved[:80],
                                "notebooklm_add_failed",
                                exc,
                            )
                    else:
                        if resolution.resolved_url is None:
                            stats.resolved_url_none += 1
                        log.info(
                            "URL unresolved [%s]: original_url=%s resolved_url=%s skip_reason=%s",
                            article.source,
                            resolution.original_url[:120],
                            resolution.resolved_url,
                            resolution.skip_reason,
                        )

                    fallback_title = f"{article.source}: {article.title}"[:200]
                    fallback_text = _build_text_fallback(article, resolution.original_url)
                    try:
                        await c.sources.add_text(
                            notebook_id,
                            fallback_title,
                            fallback_text,
                            wait=True,
                            wait_timeout=120.0,
                        )
                        stats.added += 1
                        stats.added_by_text += 1
                        log.info(
                            "Added text fallback [%s]: original_url=%s resolved_url=%s",
                            article.source,
                            resolution.original_url[:120],
                            resolution.resolved_url,
                        )
                    except Exception as exc:
                        stats.skipped += 1
                        log.warning(
                            "Skipping source [%s]: original_url=%s resolved_url=%s skip_reason=%s error=%s",
                            article.source,
                            resolution.original_url[:80],
                            resolution.resolved_url,
                            resolution.skip_reason or "text_fallback_add_failed",
                            exc,
                        )

                log.info(
                    "Added %d/%d sources to notebook %s (url=%d text=%d resolved: %d/%d, skipped: %d/%d, resolved_url_none=%d/%d)",
                    stats.added,
                    stats.total,
                    notebook_id,
                    stats.added_by_url,
                    stats.added_by_text,
                    stats.resolved,
                    stats.total,
                    stats.skipped,
                    stats.total,
                    stats.resolved_url_none,
                    stats.total,
                )
                return stats

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
                        task_id = (status.task_id or "").strip() or None
                        result.audio_task_id = task_id
                        result.audio_status = getattr(status, "status", None)
                        if task_id:
                            log.info("Audio generation started task_id=%s", task_id)
                        else:
                            result.audio_error = "audio_task_id_missing"
                            log.warning(
                                "Audio generation returned no task_id - skipping audio wait"
                            )
                    except Exception as exc:
                        result.audio_status = "trigger_failed"
                        result.audio_error = str(exc)
                        log.warning("Audio generation trigger failed: %s", exc)

                if video:
                    try:
                        status = await c.artifacts.generate_video(notebook_id, language="ja")
                        task_id = (status.task_id or "").strip() or None
                        result.video_task_id = task_id
                        result.video_status = getattr(status, "status", None)
                        if task_id:
                            log.info("Video generation started task_id=%s", task_id)
                        else:
                            result.video_error = "video_task_id_missing"
                            log.warning(
                                "Video generation returned no task_id - skipping video wait"
                            )
                    except Exception as exc:
                        result.video_status = "trigger_failed"
                        result.video_error = str(exc)
                        log.warning("Video generation trigger failed: %s", exc)
            return result

        return self._run(_inner())

    def wait_for_generation(
        self,
        result: GenerationResult,
        max_wait: int | None = None,
    ) -> GenerationResult:
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
                        result.audio_status = st.status
                        result.audio_error = st.error
                        result.audio_ready = st.status == "completed"
                        if not result.audio_ready:
                            log.warning("Audio ended with status=%s error=%s", st.status, st.error)
                    except TimeoutError:
                        result.audio_status = "timeout"
                        result.audio_error = f"timed out after {int(timeout)}s"
                        log.warning("Audio generation timed out after %ds", int(timeout))
                    except Exception as exc:
                        result.audio_status = "wait_failed"
                        result.audio_error = str(exc)
                        log.error("Audio wait failed: %s", exc)
                else:
                    if not result.audio_error:
                        result.audio_error = "audio_task_id_missing"
                    log.info("No audio task_id - skipping audio wait")

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
                        result.video_status = st.status
                        result.video_error = st.error
                        result.video_ready = st.status == "completed"
                        if not result.video_ready:
                            log.warning("Video ended with status=%s error=%s", st.status, st.error)
                    except TimeoutError:
                        result.video_status = "timeout"
                        result.video_error = f"timed out after {int(timeout)}s"
                        log.warning("Video generation timed out after %ds", int(timeout))
                    except Exception as exc:
                        result.video_status = "wait_failed"
                        result.video_error = str(exc)
                        log.error("Video wait failed: %s", exc)
                else:
                    if not result.video_error:
                        result.video_error = "video_task_id_missing"
                    log.info("No video task_id - skipping video wait")

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
                                "Downloaded audio: %s (%.1f MB)",
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
                                "Downloaded video: %s (%.1f MB)",
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
        added = self.add_sources(result.notebook_id, articles)
        if added.added < config.MIN_SOURCES_TO_GENERATE:
            log.warning(
                "Skipping generation for notebook %s: only %d sources added (minimum %d)",
                result.notebook_id,
                added.added,
                config.MIN_SOURCES_TO_GENERATE,
            )
            return result
        gen = self.trigger_generation(result.notebook_id)
        result.audio_task_id = gen.audio_task_id
        result.video_task_id = gen.video_task_id
        result.audio_status = gen.audio_status
        result.video_status = gen.video_status
        result.audio_error = gen.audio_error
        result.video_error = gen.video_error
        return result
