"""poll_until_ready は wait_for_generation の薄いシム。

teng-lin/notebooklm-py の wait_for_completion が内部でポーリングするため、
外側でのポーリングループは不要になった。
"""

from __future__ import annotations

import config
from logger import get_logger
from nlm.client import GenerationResult, NotebookLMClient

log = get_logger(__name__)


def poll_until_ready(
    client: NotebookLMClient,
    notebook_id: str,
    *,
    gen_result: GenerationResult | None = None,
    need_audio: bool = True,
    need_video: bool = True,
    need_summary: bool = False,
    poll_interval: int | None = None,
    max_wait: int | None = None,
) -> GenerationResult:
    """generation_requested 後に呼び出し、完了まで待機して GenerationResult を返す。

    gen_result に trigger_generation() の戻り値（task_id 入り）を渡すこと。
    """
    if gen_result is None:
        gen_result = GenerationResult(notebook_id=notebook_id)

    result = client.wait_for_generation(gen_result, max_wait=max_wait)

    log.info(
        "poll_until_ready done: audio=%s video=%s",
        result.audio_ready,
        result.video_ready,
    )
    return result
