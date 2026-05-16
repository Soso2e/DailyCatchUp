"""Discord Bot with slash commands for on-demand news access.

Commands:
  /news today   – Show today's news summary
  /news play    – Play today's audio in VC
  /news summary – Show bullet-point summary
  /news youtube – Show today's YouTube link

Run: python -m discord_bot.bot
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import discord
from discord import app_commands

import config
from logger import get_logger

log = get_logger(__name__)


def _today_dir() -> Path:
    return Path(config.OUTPUTS_DIR) / date.today().isoformat()


def _load_metadata() -> dict | None:
    meta_file = _today_dir() / "metadata.json"
    if meta_file.exists():
        return json.loads(meta_file.read_text(encoding="utf-8"))
    return None


def _load_summary() -> str | None:
    summary_file = _today_dir() / "summary.md"
    if summary_file.exists():
        return summary_file.read_text(encoding="utf-8")
    return None


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
    meta = _load_metadata()
    summary = _load_summary()

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


@news_group.command(name="play", description="今日の音声ニュースをVCで再生")
async def news_play(interaction: discord.Interaction) -> None:
    audio_path = _today_dir() / "audio.mp3"
    if not audio_path.exists():
        await interaction.response.send_message(
            "⚠️ 今日の音声ファイルがまだ生成されていません。", ephemeral=True
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

    try:
        vc = await vc_channel.connect()
        source = discord.FFmpegPCMAudio(str(audio_path))
        vc.play(
            source,
            after=lambda e: client.loop.create_task(vc.disconnect()) if not e else None,
        )
        await interaction.followup.send(f"🔊 `{vc_channel.name}` で音声ニュースを再生中...")
    except Exception as exc:
        log.error("VC play error: %s", exc)
        await interaction.followup.send("❌ 音声再生に失敗しました。")


client.tree.add_command(news_group)


def run() -> None:
    if not config.DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set – cannot start bot")
        return
    client.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    run()
