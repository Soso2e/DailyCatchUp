from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from discord_bot.thread_qa import (
    build_notebook_prompt,
    infer_daily_date,
    is_dailycatchup_starter,
    split_discord_message,
)


class ThreadQATest(unittest.TestCase):
    def test_detects_dailycatchup_embed(self) -> None:
        message = SimpleNamespace(
            content="",
            embeds=[
                SimpleNamespace(
                    title="🌅 AI・ゲームニュース朝刊 2026-07-19",
                    description="本日のニュース",
                    footer=SimpleNamespace(text="DailyCatchUp • 毎朝07:00配信"),
                )
            ],
        )
        self.assertTrue(is_dailycatchup_starter(message))

    def test_rejects_unrelated_message(self) -> None:
        message = SimpleNamespace(content="普通のBotメッセージ", embeds=[])
        self.assertFalse(is_dailycatchup_starter(message))

    def test_infers_explicit_date_from_embed(self) -> None:
        message = SimpleNamespace(
            content="",
            embeds=[
                SimpleNamespace(
                    title="🌅 AI・ゲームニュース朝刊 2026-07-18",
                    description=None,
                    footer=SimpleNamespace(text="DailyCatchUp"),
                )
            ],
            created_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
        )
        self.assertEqual(infer_daily_date(message), "2026-07-18")

    def test_falls_back_to_jst_creation_date(self) -> None:
        message = SimpleNamespace(
            content="🎙️ 本日の音声ニュース",
            embeds=[],
            created_at=datetime(2026, 7, 18, 16, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(infer_daily_date(message), "2026-07-19")

    def test_prompt_contains_discord_copy_paste_instruction(self) -> None:
        prompt = build_notebook_prompt("一番重要なニュースは？", "2026-07-19")
        self.assertIn("Discordにそのままコピペできる形", prompt)
        self.assertIn("このノートブック内のソースだけ", prompt)
        self.assertIn("一番重要なニュースは？", prompt)

    def test_split_discord_message_respects_limit(self) -> None:
        chunks = split_discord_message("A" * 25 + "\n" + "B" * 25, limit=20)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 20 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
