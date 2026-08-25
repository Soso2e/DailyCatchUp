"""Tests for DailyCatchUp NotebookLM retention."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import pytest

from nlm.retention import daily_notebook_date, prune_daily_notebooks


@dataclass
class FakeNotebook:
    id: str
    title: str
    is_owner: bool = True


class FakeNotebooksAPI:
    def __init__(self, notebooks: list[FakeNotebook], fail_ids: set[str] | None = None):
        self._notebooks = notebooks
        self._fail_ids = fail_ids or set()
        self.deleted_ids: list[str] = []

    async def list(self) -> list[FakeNotebook]:
        return list(self._notebooks)

    async def delete(self, notebook_id: str) -> None:
        if notebook_id in self._fail_ids:
            raise RuntimeError("delete failed")
        self.deleted_ids.append(notebook_id)


class FakeClient:
    def __init__(self, notebooks: list[FakeNotebook], fail_ids: set[str] | None = None):
        self.notebooks = FakeNotebooksAPI(notebooks, fail_ids)


def test_daily_notebook_date_accepts_only_managed_title() -> None:
    assert daily_notebook_date("AI・ゲームニュース 2026-06-27") == date(2026, 6, 27)
    assert daily_notebook_date("AI・ゲームニュース2026-06-27") is None
    assert daily_notebook_date("My Notebook 2026-06-27") is None
    assert daily_notebook_date("AI・ゲームニュース 2026-99-99") is None


def test_prune_deletes_notebooks_at_least_60_days_old() -> None:
    client = FakeClient(
        [
            FakeNotebook("old", "AI・ゲームニュース 2026-06-27"),  # exactly 60 days
            FakeNotebook("older", "AI・ゲームニュース 2026-06-01"),
            FakeNotebook("recent", "AI・ゲームニュース 2026-06-28"),
            FakeNotebook("other", "Personal research 2026-01-01"),
        ]
    )

    result = asyncio.run(
        prune_daily_notebooks(
            client,
            retention_days=60,
            reference_date=date(2026, 8, 26),
        )
    )

    assert client.notebooks.deleted_ids == ["older", "old"]
    assert result.scanned == 4
    assert result.matched == 3
    assert result.deleted == 2
    assert result.skipped_recent == 1


def test_prune_never_deletes_not_owned_daily_notebook() -> None:
    client = FakeClient(
        [FakeNotebook("shared", "AI・ゲームニュース 2026-01-01", is_owner=False)]
    )

    result = asyncio.run(
        prune_daily_notebooks(
            client,
            retention_days=60,
            reference_date=date(2026, 8, 26),
        )
    )

    assert client.notebooks.deleted_ids == []
    assert result.skipped_not_owned == 1


def test_prune_raises_if_candidate_delete_fails() -> None:
    client = FakeClient(
        [FakeNotebook("broken", "AI・ゲームニュース 2026-01-01")],
        fail_ids={"broken"},
    )

    with pytest.raises(RuntimeError, match="retention failed"):
        asyncio.run(
            prune_daily_notebooks(
                client,
                retention_days=60,
                reference_date=date(2026, 8, 26),
            )
        )
