"""Download audio.mp3, video.mp4, and summary.md from NotebookLM generation results."""

from __future__ import annotations

import time
from pathlib import Path

import httpx

import config
from logger import get_logger
from notebooklm.client import GenerationResult

log = get_logger(__name__)


class AssetDownloader:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def download_all(self, result: GenerationResult) -> dict[str, Path | None]:
        paths: dict[str, Path | None] = {
            "audio": None,
            "video": None,
            "summary": None,
        }

        if result.audio_ready and result.audio_url:
            paths["audio"] = self._download_file(result.audio_url, "audio.mp3")
        else:
            log.warning("Audio not ready – skipping download")

        if result.video_ready and result.video_url:
            paths["video"] = self._download_file(result.video_url, "video.mp4")
        else:
            log.warning("Video not ready – skipping download")

        if result.summary_ready and result.summary_text:
            paths["summary"] = self._save_text(result.summary_text, "summary.md")

        return paths

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _download_file(
        self, url: str, filename: str, retries: int | None = None
    ) -> Path | None:
        retries = retries or config.RETRY_COUNT
        dest = self.output_dir / filename

        for attempt in range(1, retries + 1):
            try:
                log.info("Downloading %s (attempt %d)", filename, attempt)
                with httpx.stream("GET", url, timeout=120, follow_redirects=True) as resp:
                    resp.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            f.write(chunk)

                size_mb = dest.stat().st_size / 1_048_576
                log.info("Downloaded %s (%.1f MB) → %s", filename, size_mb, dest)
                return dest

            except Exception as exc:
                log.warning("Download failed [%s] attempt %d: %s", filename, attempt, exc)
                if attempt < retries:
                    backoff = config.RETRY_BACKOFF * attempt
                    log.debug("Retrying in %.1fs", backoff)
                    time.sleep(backoff)

        log.error("All %d download attempts failed for %s", retries, filename)
        return None

    def _save_text(self, text: str, filename: str) -> Path:
        dest = self.output_dir / filename
        dest.write_text(text, encoding="utf-8")
        log.info("Saved %s (%d chars)", filename, len(text))
        return dest
