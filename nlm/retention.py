"""Retention policy for DailyCatchUp-managed NotebookLM notebooks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from logger import get_logger

log = get_logger(__name__)

DAILY_NOTEBOOK_TITLE_PREFIX = "AI・ゲームニュース"
_DAILY_NOTEBOOK_RE = re.compile(
    rf"^{re.escape(DAILY_NOTEBOOK_TITLE_PREFIX)} (\d{{4}}-\d{{2}}-\d{{2}})$"
)


@dataclass(frozen=True)
class RetentionResult:
    """Summary of one retention pass."""

    scanned: int
    matched: int
    deleted: int
    skipped_recent: int
    skipped_not_owned: int


def daily_notebook_date(title: str) -> date | None:
    """Return the date encoded in a DailyCatchUp notebook title.

    Only the exact title format managed by this project is accepted so the
    cleanup routine cannot delete unrelated NotebookLM notebooks.
    """
    match = _DAILY_NOTEBOOK_RE.fullmatch((title or "").strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


async def prune_daily_notebooks(
    client: Any,
    *,
    retention_days: int,
    reference_date: date | None = None,
) -> RetentionResult:
    """Delete owned DailyCatchUp notebooks that are at least ``retention_days`` old.

    The date is derived from the exact DailyCatchUp title rather than mutable
    NotebookLM metadata. Unrelated notebooks are never deletion candidates.

    Raises:
        ValueError: If ``retention_days`` is less than 1.
        RuntimeError: If one or more candidate notebooks could not be deleted.
    """
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")

    today = reference_date or date.today()
    cutoff = today - timedelta(days=retention_days)
    notebooks = await client.notebooks.list()

    candidates: list[tuple[date, Any]] = []
    matched = 0
    skipped_recent = 0
    skipped_not_owned = 0

    for notebook in notebooks:
        notebook_date = daily_notebook_date(getattr(notebook, "title", ""))
        if notebook_date is None:
            continue

        matched += 1
        if notebook_date > cutoff:
            skipped_recent += 1
            continue

        # ``is_owner`` exists on current notebooklm-py. If an older compatible
        # version does not expose it, the exact managed-title guard above still
        # limits deletion to DailyCatchUp notebooks.
        if getattr(notebook, "is_owner", True) is False:
            skipped_not_owned += 1
            log.warning(
                "Skipping retention candidate not owned by this account: id=%s title=%s",
                getattr(notebook, "id", ""),
                getattr(notebook, "title", ""),
            )
            continue

        candidates.append((notebook_date, notebook))

    errors: list[str] = []
    deleted = 0
    for notebook_date, notebook in sorted(candidates, key=lambda item: item[0]):
        notebook_id = str(getattr(notebook, "id", ""))
        notebook_title = str(getattr(notebook, "title", ""))
        if not notebook_id:
            errors.append(f"missing notebook id for {notebook_title!r}")
            continue

        try:
            await client.notebooks.delete(notebook_id)
            deleted += 1
            log.info(
                "Deleted expired DailyCatchUp notebook: id=%s title=%s age=%dd",
                notebook_id,
                notebook_title,
                (today - notebook_date).days,
            )
        except Exception as exc:
            errors.append(f"{notebook_title!r} ({notebook_id}): {exc}")
            log.error(
                "Failed to delete expired DailyCatchUp notebook id=%s title=%s: %s",
                notebook_id,
                notebook_title,
                exc,
            )

    result = RetentionResult(
        scanned=len(notebooks),
        matched=matched,
        deleted=deleted,
        skipped_recent=skipped_recent,
        skipped_not_owned=skipped_not_owned,
    )
    log.info(
        "NotebookLM retention complete: scanned=%d matched=%d deleted=%d "
        "recent=%d not_owned=%d cutoff=%s",
        result.scanned,
        result.matched,
        result.deleted,
        result.skipped_recent,
        result.skipped_not_owned,
        cutoff.isoformat(),
    )

    if errors:
        raise RuntimeError(
            "NotebookLM retention failed for one or more notebooks: " + "; ".join(errors)
        )

    return result
