"""Main pipeline runner.

Schedules and executes the full daily news pipeline:
  06:00  collect → notebooklm → wait for generation → download assets → generate meta
  07:00  Discord morning post
  19:00  YouTube upload
  19:05  Discord night notification

Usage:
  python -m scheduler.runner          # daemon mode (runs continuously with schedule)
  python -m scheduler.runner --now    # run full pipeline immediately (for testing)
  python -m scheduler.runner --step <step>  # run a single step
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path

import schedule

import config
from collector.filter import filter_articles
from collector.newsapi_collector import collect_newsapi
from collector.rss_collector import collect_rss, Article
from discord.morning_post import post_morning_news
from discord.night_notify import notify_youtube_uploaded
from downloader.asset_downloader import AssetDownloader
from logger import get_logger
from meta.meta_generator import VideoMetadata, generate_metadata
from meta.thumbnail import generate_thumbnail
from notion.status_manager import DailyStatus, get_status_manager
from notebooklm.client import NotebookLMClient
from notebooklm.poller import poll_until_ready
from youtube.uploader import upload_video

log = get_logger(__name__)


def today_str() -> str:
    return date.today().isoformat()


def output_dir(date_str: str | None = None) -> Path:
    d = date_str or today_str()
    p = Path(config.OUTPUTS_DIR) / d
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def step_collect(date_str: str) -> list[Article]:
    log.info("=== Step: collect news (%s) ===", date_str)
    rss_articles = collect_rss(max_age_hours=config.NEWS_MAX_AGE_HOURS)
    api_articles = collect_newsapi(max_age_hours=config.NEWS_MAX_AGE_HOURS)
    all_articles = rss_articles + api_articles

    articles = filter_articles(all_articles)

    if len(articles) < config.MIN_ARTICLES:
        log.warning("Too few articles (%d). Retrying with extended range (48h)", len(articles))
        rss2 = collect_rss(max_age_hours=48)
        api2 = collect_newsapi(max_age_hours=48)
        articles = filter_articles(rss2 + api2)

    log.info("Selected %d articles for today", len(articles))
    for i, a in enumerate(articles, 1):
        log.info("  %d. [%s] %s", i, a.language.upper(), a.title[:80])

    return articles


def step_notebooklm(articles: list[Article], date_str: str):
    log.info("=== Step: NotebookLM generation ===")
    client = NotebookLMClient()
    title = f"AI・ゲームニュース {date_str}"
    result = client.create_notebook_with_articles(title, articles)
    log.info("Notebook created: id=%s", result.notebook_id)

    log.info("Polling for generation completion (max %ds)...", config.NOTEBOOKLM_MAX_WAIT)
    result = poll_until_ready(client, result.notebook_id, need_audio=True, need_video=True)
    return client, result


def step_download(result, date_str: str) -> dict[str, Path | None]:
    log.info("=== Step: download assets ===")
    downloader = AssetDownloader(output_dir(date_str))
    paths = downloader.download_all(result)
    log.info("Assets: audio=%s video=%s summary=%s",
             paths["audio"], paths["video"], paths["summary"])
    return paths


def step_meta(articles: list[Article], date_str: str, paths: dict) -> VideoMetadata:
    log.info("=== Step: generate metadata (Claude) ===")
    metadata = generate_metadata(articles, date_str)
    log.info("Title: %s", metadata.title)

    out_dir = output_dir(date_str)
    generate_thumbnail(metadata, date_str, out_dir)

    meta_file = out_dir / "metadata.json"
    meta_dict = {
        "date": date_str,
        "title": metadata.title,
        "description": metadata.description,
        "tags": metadata.tags,
        "thumbnail_headline": metadata.thumbnail_headline,
        "thumbnail_subtext": metadata.thumbnail_subtext,
        "youtube_url": "",
    }
    meta_file.write_text(json.dumps(meta_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("metadata.json saved")
    return metadata


def step_discord_morning(articles: list[Article], paths: dict, date_str: str) -> bool:
    log.info("=== Step: Discord morning post ===")
    return post_morning_news(
        audio_path=paths.get("audio"),
        summary_path=paths.get("summary"),
        articles=articles,
        date_str=date_str,
    )


def step_youtube(paths: dict, metadata: VideoMetadata, date_str: str) -> str | None:
    log.info("=== Step: YouTube upload ===")
    video_path = paths.get("video")
    thumbnail_path = output_dir(date_str) / "thumbnail.png"

    if not video_path or not Path(video_path).exists():
        log.error("No video file available for YouTube upload")
        return None

    url = upload_video(
        video_path=Path(video_path),
        thumbnail_path=thumbnail_path if thumbnail_path.exists() else None,
        metadata=metadata,
        date_str=date_str,
    )

    if url:
        meta_file = output_dir(date_str) / "metadata.json"
        if meta_file.exists():
            meta_dict = json.loads(meta_file.read_text(encoding="utf-8"))
            meta_dict["youtube_url"] = url
            meta_file.write_text(json.dumps(meta_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    return url


def step_discord_night(
    youtube_url: str, metadata: VideoMetadata, articles: list[Article], date_str: str
) -> bool:
    log.info("=== Step: Discord night notification ===")
    return notify_youtube_uploaded(youtube_url, metadata, articles, date_str)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

_pipeline_state: dict = {}


def run_morning_pipeline() -> None:
    """Runs at 06:00: collect → notebooklm → download → meta → discord morning."""
    date_str = today_str()
    log.info("====== Morning pipeline start (%s) ======", date_str)

    status_mgr = get_status_manager()
    status = status_mgr.get(date_str)

    try:
        # 1. Collect
        articles = step_collect(date_str)
        status.news_collected = True
        status_mgr.save(status)
        _pipeline_state["articles"] = articles

        # 2. NotebookLM
        _, nlm_result = step_notebooklm(articles, date_str)
        status.notebooklm_generated = nlm_result.audio_ready or nlm_result.video_ready
        status_mgr.save(status)
        _pipeline_state["nlm_result"] = nlm_result

        # 3. Download
        paths = step_download(nlm_result, date_str)
        status.audio_downloaded = paths.get("audio") is not None
        status.video_downloaded = paths.get("video") is not None
        status_mgr.save(status)
        _pipeline_state["paths"] = paths

        # 4. Meta
        metadata = step_meta(articles, date_str, paths)
        status.meta_generated = True
        status_mgr.save(status)
        _pipeline_state["metadata"] = metadata

    except Exception as exc:
        tb = traceback.format_exc()
        log.error("Morning pipeline error: %s\n%s", exc, tb)
        status.error_log = f"morning: {exc}"
        status_mgr.save(status)
        _notify_error(f"Morning pipeline failed: {exc}")
        return

    # 5. Discord morning (targeted at 07:00 but run immediately in --now mode)
    _run_discord_morning(date_str, status, status_mgr)

    log.info("====== Morning pipeline complete ======")


def _run_discord_morning(date_str: str, status: DailyStatus, status_mgr) -> None:
    articles = _pipeline_state.get("articles", [])
    paths = _pipeline_state.get("paths", {})
    try:
        ok = step_discord_morning(articles, paths, date_str)
        status.discord_morning = ok
        status_mgr.save(status)
    except Exception as exc:
        log.error("Discord morning post error: %s", exc)
        status.error_log += f" | discord_morning: {exc}"
        status_mgr.save(status)


def run_evening_pipeline() -> None:
    """Runs at 19:00: youtube upload → discord night notification."""
    date_str = today_str()
    log.info("====== Evening pipeline start (%s) ======", date_str)

    status_mgr = get_status_manager()
    status = status_mgr.get(date_str)

    # Load from saved metadata if state was lost (e.g. process restart)
    if "metadata" not in _pipeline_state:
        _restore_state(date_str)

    articles = _pipeline_state.get("articles", [])
    paths = _pipeline_state.get("paths", {})
    metadata = _pipeline_state.get("metadata")

    if metadata is None:
        log.error("No metadata available – cannot proceed with evening pipeline")
        return

    try:
        youtube_url = step_youtube(paths, metadata, date_str)
        status.youtube_uploaded = youtube_url is not None
        if youtube_url:
            status.youtube_url = youtube_url
        status_mgr.save(status)
    except Exception as exc:
        log.error("YouTube upload error: %s", exc)
        status.error_log += f" | youtube: {exc}"
        status_mgr.save(status)
        _notify_error(f"YouTube upload failed: {exc}")
        youtube_url = None

    log.info("Waiting 5 minutes before Discord night notification...")
    time.sleep(300)

    if youtube_url:
        try:
            ok = step_discord_night(youtube_url, metadata, articles, date_str)
            status.discord_notified = ok
            status_mgr.save(status)
        except Exception as exc:
            log.error("Discord night notification error: %s", exc)
            status.error_log += f" | discord_night: {exc}"
            status_mgr.save(status)

    log.info("====== Evening pipeline complete ======")


def _restore_state(date_str: str) -> None:
    """Restore pipeline state from saved metadata.json and article list."""
    out_dir = output_dir(date_str)
    meta_file = out_dir / "metadata.json"
    if meta_file.exists():
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        _pipeline_state["metadata"] = VideoMetadata(
            title=data.get("title", ""),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            thumbnail_headline=data.get("thumbnail_headline", ""),
            thumbnail_subtext=data.get("thumbnail_subtext", ""),
        )
        _pipeline_state["paths"] = {
            "audio": out_dir / "audio.mp3",
            "video": out_dir / "video.mp4",
            "summary": out_dir / "summary.md",
        }
        log.info("Restored state from %s", meta_file)


def _notify_error(message: str) -> None:
    try:
        import httpx
        if config.DISCORD_WEBHOOK_URL:
            httpx.post(
                config.DISCORD_WEBHOOK_URL,
                json={"content": f"❌ **DailyCatchUp Error**\n{message}"},
                timeout=10,
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Scheduler daemon
# ---------------------------------------------------------------------------

def run_daemon() -> None:
    log.info("Starting DailyCatchUp scheduler daemon")
    log.info("Schedule: 06:00 morning pipeline, 07:00 Discord, 19:00 evening pipeline")

    schedule.every().day.at("06:00").do(run_morning_pipeline)
    schedule.every().day.at("19:00").do(run_evening_pipeline)

    while True:
        schedule.run_pending()
        time.sleep(30)


def run_now() -> None:
    """Run the full pipeline immediately (for manual testing)."""
    log.info("Running full pipeline NOW (test mode)")
    run_morning_pipeline()
    run_evening_pipeline()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DailyCatchUp pipeline runner")
    parser.add_argument("--now", action="store_true", help="Run full pipeline immediately")
    parser.add_argument(
        "--step",
        choices=["collect", "notebooklm", "download", "meta", "discord-morning", "youtube", "discord-night"],
        help="Run a single pipeline step",
    )
    args = parser.parse_args()

    date_str = today_str()

    if args.now:
        run_now()
    elif args.step:
        if args.step == "collect":
            articles = step_collect(date_str)
            print(f"Collected {len(articles)} articles")
        elif args.step == "discord-morning":
            _restore_state(date_str)
            articles = _pipeline_state.get("articles", [])
            paths = _pipeline_state.get("paths", {})
            step_discord_morning(articles, paths, date_str)
        elif args.step == "youtube":
            _restore_state(date_str)
            metadata = _pipeline_state.get("metadata")
            paths = _pipeline_state.get("paths", {})
            if metadata:
                url = step_youtube(paths, metadata, date_str)
                print(f"YouTube URL: {url}")
        elif args.step == "discord-night":
            _restore_state(date_str)
            metadata = _pipeline_state.get("metadata")
            articles = _pipeline_state.get("articles", [])
            status = get_status_manager().get(date_str)
            if metadata and status.youtube_url:
                step_discord_night(status.youtube_url, metadata, articles, date_str)
        else:
            log.error("Step '%s' requires full pipeline context. Use --now instead.", args.step)
            sys.exit(1)
    else:
        run_daemon()
