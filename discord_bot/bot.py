"""Discord Bot with slash commands for on-demand news access.

Commands:
  /news today          – Show today's news summary
  /news play [date]    – Play audio in VC (today or a past date)
  /news summary        – Show bullet-point summary
  /news youtube        – Show today's YouTube link
  /news collect [date] – Trigger morning data collection pipeline

Run: python -m discord_bot.bot
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import discord
from discord import app_commands

import config
from logger import get_logger

log = get_logger(__name__)


_pipeline_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")


def _date_dir(date_str: str | None = None) -> Path:
    return Path(config.OUTPUTS_DIR) / (date_str or date.today().isoformat())


def _today_dir() -> Path:
    return _date_dir()


def _load_metadata(date_str: str | None = None) -> dict | None:
    meta_file = _date_dir(date_str) / "metadata.json"
    if meta_file.exists():
        return json.loads(meta_file.read_text(encoding="utf-8"))
    return None


def _load_summary(date_str: str | None = None) -> str | None:
    summary_file = _date_dir(date_str) / "summary.md"
    if summary_file.exists():
        return summary_file.read_text(encoding="utf-8")
    return None


def _is_valid_date(date_str: str) -> bool:
    try:
        from datetime import datetime
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


class NewsBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()
        log.info("Slash commands synced")

    async def on_ready(self) -> None:
        log.info("Discord bot ready: %s (id=%s)", self.user, self.user.id)


client = NewsBot()
news_group = app_commands.Group(name="news", description="DailyCatchUp ニュースコマンド")


@news_group.command(name="today", description="今日のニュース要約を表示")
async def news_today(interaction: discord.Interaction) -> None:
    meta = _load_metadata(None)
    summary = _load_summary(None)

    if not meta and not summary:
        await interaction.response.send_message(
            "⚠️ 今日のニュースはまだ生成されていません。", ephemeral=True
        )
        return

    embed = discord.Embed(
        title=meta.get("title", "今日のAI・ゲームニュース") if meta else "今日のニュース",
        description=(summary[:1024] if summary else meta.get("description", "")),
        color=0x00C8FF,
    )

    if meta and meta.get("youtube_url"):
        embed.add_field(name="▶️ YouTube", value=meta["youtube_url"], inline=False)

    await interaction.response.send_message(embed=embed)


@news_group.command(name="summary", description="要点のみ表示")
async def news_summary(interaction: discord.Interaction) -> None:
    summary = _load_summary()
    if not summary:
        await interaction.response.send_message(
            "⚠️ 今日の要約はまだ生成されていません。", ephemeral=True
        )
        return

    lines = [l for l in summary.splitlines() if l.strip()][:10]
    content = "\n".join(lines)[:1900]
    await interaction.response.send_message(f"📋 **今日のニュース要点**\n{content}")


@news_group.command(name="youtube", description="今日のYouTube動画リンクを表示")
async def news_youtube(interaction: discord.Interaction) -> None:
    meta = _load_metadata()
    if not meta or not meta.get("youtube_url"):
        await interaction.response.send_message(
            "⚠️ 今日のYouTube動画はまだ投稿されていません。", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"🎬 **{meta.get('title', '今日の動画')}**\n{meta['youtube_url']}"
    )


@news_group.command(name="play", description="音声ニュースをVCで再生（日付省略時は今日）")
@app_commands.describe(target_date="対象日付 YYYY-MM-DD（省略時は今日）")
async def news_play(interaction: discord.Interaction, target_date: str | None = None) -> None:
    if target_date is not None and not _is_valid_date(target_date):
        await interaction.response.send_message(
            "⚠️ 日付の形式が正しくありません。`YYYY-MM-DD` 形式で指定してください。", ephemeral=True
        )
        return

    date_label = target_date or date.today().isoformat()
    audio_path = _date_dir(target_date) / "audio.mp3"

    if not audio_path.exists():
        await interaction.response.send_message(
            f"⚠️ `{date_label}` の音声ファイルが見つかりません。", ephemeral=True
        )
        return

    member = interaction.user
    if not isinstance(member, discord.Member) or not member.voice:
        await interaction.response.send_message(
            "⚠️ まずボイスチャンネルに参加してください。", ephemeral=True
        )
        return

    vc_channel = member.voice.channel
    await interaction.response.defer()

    vc: discord.VoiceClient | None = None
    try:
        loop = asyncio.get_running_loop()

        vc = discord.utils.get(client.voice_clients, guild=interaction.guild)
        if vc:
            if vc.is_playing():
                vc.stop()
            if vc.channel != vc_channel:
                await vc.move_to(vc_channel)
        else:
            vc = await vc_channel.connect()

        source = discord.FFmpegPCMAudio(str(audio_path))

        def after_play(error: Exception | None) -> None:
            if error:
                log.error("VC playback error: %s", error)
            asyncio.run_coroutine_threadsafe(vc.disconnect(), loop)

        vc.play(source, after=after_play)
        await interaction.followup.send(
            f"🔊 `{vc_channel.name}` で `{date_label}` の音声ニュースを再生中..."
        )
    except Exception as exc:
        log.error("VC play error: %s", exc, exc_info=True)
        if vc and vc.is_connected():
            await vc.disconnect()
        await interaction.followup.send(f"❌ 音声再生に失敗しました。\n```\n{exc}\n```")


@news_group.command(name="collect", description="朝のデータ収集パイプラインを手動実行")
@app_commands.describe(target_date="対象日付 YYYY-MM-DD（省略時は今日）", skip_nlm="NotebookLM をスキップして保存済みアセットをDiscordに投稿")
async def news_collect(
    interaction: discord.Interaction,
    target_date: str | None = None,
    skip_nlm: bool = False,
) -> None:
    if target_date is not None and not _is_valid_date(target_date):
        await interaction.response.send_message(
            "⚠️ 日付の形式が正しくありません。`YYYY-MM-DD` 形式で指定してください。", ephemeral=True
        )
        return

    date_label = target_date or date.today().isoformat()
    mode_label = "（NotebookLM スキップ）" if skip_nlm else ""
    await interaction.response.send_message(
        f"⏳ `{date_label}` の朝のデータ収集を開始します{mode_label}...\n"
        "完了まで数分かかる場合があります。"
    )

    loop = asyncio.get_event_loop()

    def _run_pipeline() -> str:
        from scheduler.runner import run_morning_pipeline
        run_morning_pipeline(date_str=target_date, skip_nlm=skip_nlm)
        return "ok"

    try:
        await loop.run_in_executor(_pipeline_executor, _run_pipeline)
        await interaction.followup.send(
            f"✅ `{date_label}` のデータ収集が完了しました！\n`/news today` で確認できます。"
        )
    except Exception as exc:
        log.error("Pipeline error from Discord command: %s", exc)
        await interaction.followup.send(f"❌ パイプライン実行中にエラーが発生しました:\n```\n{exc}\n```")


client.tree.add_command(news_group)


def run() -> None:
    if not config.DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set – cannot start bot")
        return
    client.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    run()
