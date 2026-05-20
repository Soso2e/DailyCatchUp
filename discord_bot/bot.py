"""Discord Bot with slash commands for on-demand news access.

Commands:
  /news today          – Show today's news summary
  /news play [date]    – Play audio in VC (today or a past date)
  /news summary        – Show bullet-point summary
  /news youtube        – Show today's YouTube link
  /news collect [date] – Trigger morning data collection pipeline
  /news channel add    – Register the current channel for daily notifications
  /news channel remove – Unregister the current channel
  /news channel list   – List all registered notification channels

Run: python -m discord_bot.bot
"""

from __future__ import annotations

import asyncio
import sys
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib import metadata
from pathlib import Path

import discord
from discord import app_commands

import config
from logger import get_logger

log = get_logger(__name__)


_pipeline_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")

VOICE_CONNECT_RETRIES = 3
VOICE_CONNECT_TIMEOUT = 25.0
VOICE_CONNECTED_WAIT_SECONDS = 10.0
VOICE_CONNECTED_STABLE_SECONDS = 1.5


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


def _package_version(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return "not installed"


async def _disconnect_existing_voice_client(guild: discord.Guild | None) -> None:
    if guild is None:
        return

    existing = discord.utils.get(client.voice_clients, guild=guild)
    if not existing:
        return

    log.info(
        "Cleaning existing voice client before reconnect: guild=%s channel=%s "
        "connected=%s playing=%s",
        guild.id,
        getattr(existing.channel, "id", None),
        existing.is_connected(),
        existing.is_playing(),
    )
    if existing.is_playing():
        existing.stop()
    await existing.disconnect(force=True)


async def _wait_until_voice_connected(
    vc: discord.VoiceClient,
    *,
    timeout: float = VOICE_CONNECTED_WAIT_SECONDS,
    stable_for: float = VOICE_CONNECTED_STABLE_SECONDS,
) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    stable_since: float | None = None

    while asyncio.get_running_loop().time() < deadline:
        connected = vc.is_connected()
        channel_id = getattr(vc.channel, "id", None)
        log.debug(
            "Voice connection probe: connected=%s channel=%s endpoint=%s",
            connected,
            channel_id,
            getattr(vc, "endpoint", None),
        )

        if connected:
            now = asyncio.get_running_loop().time()
            stable_since = stable_since or now
            if now - stable_since >= stable_for:
                return True
        else:
            stable_since = None

        await asyncio.sleep(0.25)

    return False


async def _connect_voice_channel(vc_channel: discord.abc.Connectable) -> discord.VoiceClient:
    last_error: Exception | None = None

    for attempt in range(1, VOICE_CONNECT_RETRIES + 1):
        await _disconnect_existing_voice_client(getattr(vc_channel, "guild", None))
        log.info(
            "Connecting to voice channel: attempt=%s/%s channel=%s",
            attempt,
            VOICE_CONNECT_RETRIES,
            getattr(vc_channel, "id", None),
        )

        vc: discord.VoiceClient | None = None
        try:
            vc = await vc_channel.connect(
                timeout=VOICE_CONNECT_TIMEOUT,
                reconnect=False,
                self_deaf=True,
            )
            log.info(
                "Voice connect returned: connected=%s channel=%s endpoint=%s",
                vc.is_connected(),
                getattr(vc.channel, "id", None),
                getattr(vc, "endpoint", None),
            )

            if await _wait_until_voice_connected(vc):
                await asyncio.sleep(0.5)
                log.info(
                    "Voice connection stabilized: connected=%s channel=%s playing=%s",
                    vc.is_connected(),
                    getattr(vc.channel, "id", None),
                    vc.is_playing(),
                )
                if vc.is_connected():
                    return vc

            log.warning(
                "Voice connection did not stabilize: attempt=%s connected=%s channel=%s",
                attempt,
                vc.is_connected(),
                getattr(vc.channel, "id", None),
            )
            last_error = discord.errors.ClientException("VC接続が安定しませんでした。")
        except Exception as exc:
            last_error = exc
            log.warning("Voice connect attempt failed: attempt=%s error=%s", attempt, exc, exc_info=True)
        finally:
            if vc and not vc.is_connected():
                await vc.disconnect(force=True)

        await asyncio.sleep(1.0 * attempt)

    raise discord.errors.ClientException(
        f"VC接続が確立できませんでした。再試行してください。last_error={last_error}"
    )


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
        if sys.version_info >= (3, 13):
            log.warning(
                "Python 3.13 detected. If Discord voice keeps failing, verify again "
                "with Python 3.11 or 3.12 before deeper protocol debugging."
            )
        log.info(
            "Runtime versions: python=%s discord.py=%s PyNaCl=%s davey=%s",
            sys.version.split()[0],
            _package_version("discord.py"),
            _package_version("PyNaCl"),
            _package_version("davey"),
        )


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
    bot_member = interaction.guild.me if interaction.guild else None
    if bot_member is None:
        await interaction.response.send_message(
            "⚠️ Botのサーバー情報を取得できませんでした。", ephemeral=True
        )
        return

    permissions = vc_channel.permissions_for(bot_member)
    missing_permissions = [
        label
        for label, allowed in (
            ("Connect", permissions.connect),
            ("Speak", permissions.speak),
            ("Use Voice Activity", permissions.use_voice_activation),
        )
        if not allowed
    ]
    if missing_permissions:
        await interaction.response.send_message(
            "⚠️ Botに必要なVC権限が不足しています: "
            + ", ".join(missing_permissions),
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    vc: discord.VoiceClient | None = None
    try:
        loop = asyncio.get_running_loop()

        vc = await _connect_voice_channel(vc_channel)

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


channel_group = app_commands.Group(name="channel", description="通知チャンネル管理（複数サーバー・チャンネルへの配信設定）")
news_group.add_command(channel_group)


def _has_manage_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return True  # DMs: allow
    member = interaction.guild.get_member(interaction.user.id)
    if member is None:
        return False
    perms = member.guild_permissions
    return perms.manage_channels or perms.administrator


@channel_group.command(name="add", description="このチャンネルを毎日の通知先に登録")
async def channel_add(interaction: discord.Interaction) -> None:
    if not _has_manage_permission(interaction):
        await interaction.response.send_message(
            "⚠️ このコマンドは **チャンネルの管理** または **管理者** 権限が必要です。",
            ephemeral=True,
        )
        return

    from discord_bot.channel_store import add_channel

    channel_id = str(interaction.channel_id)
    guild_name = interaction.guild.name if interaction.guild else "DM"
    channel_name = (
        interaction.channel.name
        if hasattr(interaction.channel, "name")
        else str(interaction.channel_id)
    )
    added_by = str(interaction.user)

    if add_channel(channel_id, guild_name, channel_name, added_by):
        await interaction.response.send_message(
            f"✅ **#{channel_name}** を通知チャンネルに登録しました！\n"
            "次回の朝刊・夜間通知からこのチャンネルに配信されます。",
        )
    else:
        await interaction.response.send_message(
            "⚠️ このチャンネルはすでに登録済みです。\n"
            "`/news channel list` で確認できます。",
            ephemeral=True,
        )


@channel_group.command(name="remove", description="このチャンネルを通知先から解除")
async def channel_remove(interaction: discord.Interaction) -> None:
    if not _has_manage_permission(interaction):
        await interaction.response.send_message(
            "⚠️ このコマンドは **チャンネルの管理** または **管理者** 権限が必要です。",
            ephemeral=True,
        )
        return

    from discord_bot.channel_store import remove_channel

    removed_name = remove_channel(str(interaction.channel_id))
    if removed_name:
        await interaction.response.send_message(
            f"✅ **#{removed_name}** を通知チャンネルから解除しました。",
        )
    else:
        await interaction.response.send_message(
            "⚠️ このチャンネルは通知先に登録されていません。\n"
            "`/news channel list` で登録済みチャンネルを確認してください。",
            ephemeral=True,
        )


@channel_group.command(name="list", description="登録済み通知チャンネルの一覧を表示")
async def channel_list(interaction: discord.Interaction) -> None:
    from discord_bot.channel_store import load_channels

    entries = load_channels()
    lines: list[str] = []

    for i, e in enumerate(entries, 1):
        lines.append(
            f"{i}. **{e['guild_name']} / #{e['channel_name']}**\n"
            f"   追加者: {e['added_by']} / {e['added_at'][:10]}"
        )

    if not lines:
        await interaction.response.send_message(
            "ℹ️ 通知チャンネルが登録されていません。\n"
            "通知を受け取りたいチャンネルで `/news channel add` を実行してください。",
            ephemeral=True,
        )
        return

    content = "📡 **登録済み通知チャンネル**\n\n" + "\n\n".join(lines)
    await interaction.response.send_message(content[:1900], ephemeral=True)


client.tree.add_command(news_group)


def run() -> None:
    if not config.DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN not set – cannot start bot")
        return
    client.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    run()
