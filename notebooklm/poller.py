"""Poll NotebookLM until audio, video, and summary are ready (or timeout)."""

from __future__ import annotations

import time

import config
from logger import get_logger
from notebooklm.client import GenerationResult, NotebookLMClient

log = get_logger(__name__)


def poll_until_ready(
    client: NotebookLMClient,
    notebook_id: str,
    need_audio: bool = True,
    need_video: bool = True,
    need_summary: bool = False,
    poll_interval: int | None = None,
    max_wait: int | None = None,
) -> GenerationResult:
    """Block until requested assets are ready or max_wait seconds elapse.

    Returns the final GenerationResult regardless of completion state.
    Callers should check result.audio_ready / video_ready after this returns.
    """
    poll_interval = poll_interval or config.NOTEBOOKLM_POLL_INTERVAL
    max_wait = max_wait or config.NOTEBOOKLM_MAX_WAIT

    deadline = time.monotonic() + max_wait
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        try:
            result = client.get_status(notebook_id)
        except Exception as exc:
            log.warning("Polling attempt %d failed: %s", attempt, exc)
            time.sleep(poll_interval)
            continue

        audio_ok = result.audio_ready or not need_audio
        video_ok = result.video_ready or not need_video
        summary_ok = result.summary_ready or not need_summary

        log.info(
            "Poll #%d – audio=%s video=%s summary=%s",
            attempt,
            result.audio_ready,
            result.video_ready,
            result.summary_ready,
        )

        if audio_ok and video_ok and summary_ok:
            log.info("All requested assets are ready after %d polls", attempt)
            return result

        remaining = int(deadline - time.monotonic())
        log.debug("Waiting %ds (%ds remaining)", poll_interval, remaining)
        time.sleep(poll_interval)

    log.warning(
        "Polling timed out after %ds – returning partial result (audio=%s video=%s)",
        max_wait,
        result.audio_ready if "result" in dir() else False,
        result.video_ready if "result" in dir() else False,
    )
    try:
        return result  # noqa: F821
    except NameError:
        return GenerationResult(notebook_id=notebook_id)
