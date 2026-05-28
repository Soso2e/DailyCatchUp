"""Main pipeline runner."""

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
from collector.hackernews_collector import collect_hackernews
from collector.newsapi_collector import collect_newsapi
from collector.qiita_collector import collect_qiita
from collector.rss_collector import Article, collect_rss
from discord_bot.morning_post import post_morning_news
from discord_bot.night_notify import notify_youtube_uploaded
from downloader.asset_downloader import AssetDownloader
from logger import get_logger
from meta.meta_generator import VideoMetadata, generate_metadata
from meta.thumbnail import generate_thumbnail
from notion.status_manager import DailyStatus, get_status_manager
from nlm.client import NotebookLMAuthError, NotebookLMClient
from nlm.poller import poll_until_ready
from nlm.session_manager import SessionExpiredError, ensure_valid_session, manual_refresh_session, verify_session
from youtube.uploader import upload_video

log = get_logger(__name__)


def today_str() -> str:
    return date.today().isoformat()


def output_dir(date_str: str | None = None) -> Path:
    d = date_str or today_str()
    path = Path(config.OUTPUTS_DIR) / d
    path.mkdir(parents=True, exist_ok=True)
    return path


def _update_status(status_mgr, status: DailyStatus, **changes) -> DailyStatus:
    for key, value in changes.items():
        setattr(status, key, value)
    status_mgr.save(status)
    return status


def _append_error(status: DailyStatus, message: str) -> None:
    status.error_log = f"{status.error_log} | {message}".strip(" |")


def _collect_generation_errors(result) -> list[str]:
    errors: list[str] = []
    if result.audio_error:
        errors.append(f"audio:{result.audio_status or 'unknown'}:{result.audio_error}")
    if result.video_error:
        errors.append(f"video:{result.video_status or 'unknown'}:{result.video_error}")
    return errors


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def step_collect(date_str: str) -> list[Article]:
    log.info("=== Step: collect news (%s) ===", date_str)
    rss_articles = collect_rss(max_age_hours=config.NEWS_MAX_AGE_HOURS)
    api_articles = collect_newsapi(max_age_hours=config.NEWS_MAX_AGE_HOURS)
    qiita_articles = collect_qiita(max_age_hours=config.NEWS_MAX_AGE_HOURS)
    hn_articles = collect_hackernews(max_age_hours=config.NEWS_MAX_AGE_HOURS)

    articles = filter_articles(rss_articles + api_articles + qiita_articles + hn_articles)

    if len(articles) < config.MIN_ARTICLES:
        log.warning("Too few articles (%d). Retrying with extended range (48h)", len(articles))
        articles = filter_articles(
            collect_rss(max_age_hours=48)
            + collect_newsapi(max_age_hours=48)
            + collect_qiita(max_age_hours=48)
            + collect_hackernews(max_age_hours=48)
        )

    log.info("Selected %d articles for today", len(articles))
    for idx, article in enumerate(articles, 1):
        log.info("  %d. [%s] %.80s", idx, article.language.upper(), article.title)
    return articles


def step_download(result, date_str: str, client: NotebookLMClient | None = None) -> dict[str, Path | None]:
    log.info("=== Step: download assets ===")
    if client is not None:
        paths = client.download_assets(
            result.notebook_id,
            output_dir(date_str),
            audio=result.audio_ready,
            video=result.video_ready,
        )
    else:
        downloader = AssetDownloader(output_dir(date_str))
        paths = downloader.download_all(result)
    log.info(
        "Assets: audio=%s video=%s summary=%s",
        paths["audio"],
        paths["video"],
        paths["summary"],
    )
    return paths


def _empty_asset_paths() -> dict[str, Path | None]:
    return {"audio": None, "video": None, "summary": None}


def step_meta(articles: list[Article], date_str: str, paths: dict) -> VideoMetadata | None:
    log.info("=== Step: generate metadata ===")
    metadata = generate_metadata(articles, date_str)
    if metadata is None:
        log.warning("Metadata generation skipped")
        return None

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


def step_agenda(notebook_id: str, client: "NotebookLMClient", date_str: str) -> str | None:
    log.info("=== Step: query NotebookLM agenda ===")
    try:
        text = client.query_agenda(notebook_id)
    except Exception as exc:
        log.warning("Agenda step failed: %s", exc)
        return None
    if text:
        agenda_file = output_dir(date_str) / "agenda.txt"
        agenda_file.write_text(text, encoding="utf-8")
        log.info("Agenda saved: %d chars", len(text))
    else:
        log.warning("Agenda query returned empty response")
    return text


def step_discord_morning(
    articles: list[Article],
    paths: dict,
    date_str: str,
    *,
    agenda_text: str | None = None,
) -> bool:
    log.info("=== Step: Discord morning post ===")
    return post_morning_news(
        audio_path=paths.get("audio"),
        summary_path=paths.get("summary"),
        articles=articles,
        date_str=date_str,
        agenda_text=agenda_text,
    )


def step_youtube(paths: dict, metadata: VideoMetadata, date_str: str) -> str | None:
    log.info("=== Step: YouTube upload ===")
    video_path = paths.get("video")
    thumbnail_path = output_dir(date_str) / "thumbnail.png"

    if not video_path or not Path(video_path).exists():
        log.info("YouTube upload skipped – video file not yet available (video may still be generating)")
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
    youtube_url: str,
    metadata: VideoMetadata,
    articles: list[Article],
    date_str: str,
) -> bool:
    log.info("=== Step: Discord night notification ===")
    return notify_youtube_uploaded(youtube_url, metadata, articles, date_str)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

_pipeline_state: dict = {}


def run_morning_pipeline(date_str: str | None = None, skip_nlm: bool = False) -> None:
    """Run the morning pipeline with resumable status updates.

    Args:
        date_str: Target date (YYYY-MM-DD). Defaults to today.
        skip_nlm: If True, skip NotebookLM steps and post to Discord
                  using already-downloaded assets from output_dir.
    """
    date_str = date_str or today_str()
    log.info("====== Morning pipeline start (%s) ======", date_str)

    status_mgr = get_status_manager()
    status = status_mgr.get(date_str)

    if skip_nlm:
        _run_morning_discord_only(date_str, status, status_mgr)
        log.info("====== Morning pipeline complete (skip-nlm mode) ======")
        return

    try:
        _update_status(status_mgr, status, last_step="starting")

        try:
            ensure_valid_session()
        except SessionExpiredError as exc:
            log.warning("Headless session refresh failed (%s) — attempting GUI refresh...", exc)
            try:
                refreshed = manual_refresh_session()
                if not verify_session(refreshed):
                    raise SessionExpiredError("GUI refresh succeeded but verification still failed.")
                log.info("Session refreshed via GUI browser — continuing pipeline")
            except Exception as gui_exc:
                log.error("GUI session refresh also failed: %s", gui_exc)
                _append_error(status, f"auth: {gui_exc}")
                _update_status(status_mgr, status, last_step="auth_failed")
                _notify_auth_required()
                return

        if status.news_collected:
            log.info("news_collected already True - loading cached articles")
            articles = status_mgr.load_articles(date_str)
            if not articles:
                log.warning("Cache empty despite news_collected=True, re-collecting")
                articles = step_collect(date_str)
                status_mgr.save_articles(date_str, articles)
                _update_status(
                    status_mgr,
                    status,
                    news_collected=True,
                    selected_article_count=len(articles),
                    last_step="news_collected",
                )
        else:
            articles = step_collect(date_str)
            status_mgr.save_articles(date_str, articles)
            _update_status(
                status_mgr,
                status,
                news_collected=True,
                selected_article_count=len(articles),
                last_step="news_collected",
            )
        _pipeline_state["articles"] = articles

        client = NotebookLMClient()

        if status.notebook_created and status.notebook_id:
            notebook_id = status.notebook_id
            log.info("Reusing existing notebook id=%s", notebook_id)
        else:
            create_result = client.create_notebook(f"AI・ゲームニュース {date_str}")
            notebook_id = create_result.notebook_id
            nb_url = f"https://notebooklm.google.com/notebook/{notebook_id}"
            _update_status(
                status_mgr,
                status,
                notebook_created=True,
                notebook_id=notebook_id,
                notebook_url=nb_url,
                last_step="notebook_created",
            )
            log.info("Notebook created: id=%s url=%s", notebook_id, nb_url)
            if hasattr(status_mgr, "save_notebook_link"):
                try:
                    status_mgr.save_notebook_link(date_str, nb_url)
                except Exception as _exc:
                    log.warning("Could not write NotebookLM link to Notion page: %s", _exc)

        if status.source_added and status.sources_added_count > 0:
            added_count = status.sources_added_count
            log.info("source_added already True - keeping previous count=%d", added_count)
        else:
            add_result = client.add_sources(notebook_id, articles)
            added_count = add_result.added
            _update_status(
                status_mgr,
                status,
                source_added=added_count > 0,
                sources_added_count=added_count,
                last_step="source_added" if added_count > 0 else "source_add_failed",
            )
            if added_count <= 0:
                _append_error(status, "failed: no sources added")
                _update_status(status_mgr, status, last_step="source_add_failed")
                raise RuntimeError("No sources were added to NotebookLM (all URLs failed to resolve or were rejected)")

        agenda_file = output_dir(date_str) / "agenda.txt"
        if agenda_file.exists() and agenda_file.stat().st_size > 0:
            agenda_text = agenda_file.read_text(encoding="utf-8")
            log.info("Loaded cached agenda (%d chars)", len(agenda_text))
        else:
            agenda_text = step_agenda(notebook_id, client, date_str)
        _pipeline_state["agenda_text"] = agenda_text

        should_generate = added_count >= config.MIN_SOURCES_TO_GENERATE
        if not should_generate:
            log.warning(
                "Skipping NotebookLM generation: only %d sources added (minimum %d)",
                added_count,
                config.MIN_SOURCES_TO_GENERATE,
            )
            _append_error(
                status,
                f"generation skipped: only {added_count} sources added (<{config.MIN_SOURCES_TO_GENERATE})",
            )
            _update_status(
                status_mgr,
                status,
                generation_requested=False,
                notebooklm_generated=False,
                last_step="generation_skipped_low_sources",
            )
            nlm_result = None
        else:
            if not status.generation_requested:
                gen_result = client.trigger_generation(notebook_id, audio=True, video=True)
                _pipeline_state["gen_result"] = gen_result
                for message in _collect_generation_errors(gen_result):
                    _append_error(status, message)
                _update_status(
                    status_mgr,
                    status,
                    generation_requested=bool(gen_result.audio_task_id or gen_result.video_task_id),
                    last_step="generation_requested"
                    if (gen_result.audio_task_id or gen_result.video_task_id)
                    else "generation_trigger_skipped",
                )
            else:
                gen_result = _pipeline_state.get("gen_result")

            log.info("Waiting for generation completion (max %ds)...", config.NOTEBOOKLM_MAX_WAIT)
            nlm_result = poll_until_ready(
                client, notebook_id, gen_result=gen_result, need_audio=True, need_video=True
            )
            generated = nlm_result.audio_ready or nlm_result.video_ready
            for message in _collect_generation_errors(nlm_result):
                _append_error(status, message)
            gen_updates: dict = {
                "notebooklm_generated": generated,
                "last_step": "generated" if generated else "generation_incomplete",
            }
            if not status.notebook_url:
                gen_updates["notebook_url"] = f"https://notebooklm.google.com/notebook/{notebook_id}"
            _update_status(status_mgr, status, **gen_updates)
            _pipeline_state["nlm_result"] = nlm_result

        if nlm_result is not None:
            paths = step_download(nlm_result, date_str, client=client)
            audio_p = paths.get("audio")
            _update_status(
                status_mgr,
                status,
                audio_downloaded=audio_p is not None,
                video_downloaded=paths.get("video") is not None,
                audio_path=str(audio_p) if audio_p else "",
                last_step="assets_downloaded",
            )
        else:
            paths = _empty_asset_paths()
            _update_status(
                status_mgr,
                status,
                audio_downloaded=False,
                video_downloaded=False,
                last_step=status.last_step,
            )
        _pipeline_state["paths"] = paths

        metadata = step_meta(articles, date_str, paths)
        if metadata is None:
            _append_error(status, "metadata: skipped (gemini_api_key_missing_or_invalid_or_request_failed)")
            _update_status(
                status_mgr,
                status,
                meta_generated=False,
                last_step="meta_skipped",
            )
        else:
            _update_status(status_mgr, status, meta_generated=True, last_step="meta_generated")
        _pipeline_state["metadata"] = metadata

    except NotebookLMAuthError as exc:
        log.error("NotebookLM authentication failed: %s", exc)
        _append_error(status, f"auth: {exc}")
        _update_status(status_mgr, status, last_step="auth_failed")
        _notify_auth_required()
        return
    except Exception as exc:
        tb = traceback.format_exc()
        log.error("Morning pipeline error: %s\n%s", exc, tb)
        _append_error(status, f"morning: {exc}")
        _update_status(status_mgr, status, last_step="morning_failed")
        _notify_error(f"Morning pipeline failed: {exc}")
        return

    _run_discord_morning(date_str, status, status_mgr)
    log.info("====== Morning pipeline complete ======")


def _run_morning_discord_only(date_str: str, status: DailyStatus, status_mgr) -> None:
    """Restore state from disk and post the morning Discord message only."""
    log.info("skip-nlm mode: loading saved articles and assets for %s", date_str)
    _restore_state(date_str)
    if not _pipeline_state.get("articles"):
        log.warning("No cached articles found for %s – morning post will have no article list", date_str)
    _run_discord_morning(date_str, status, status_mgr)


def _run_discord_morning(date_str: str, status: DailyStatus, status_mgr) -> None:
    articles = _pipeline_state.get("articles", [])
    paths = _pipeline_state.get("paths", {})
    agenda_text = _pipeline_state.get("agenda_text")
    try:
        ok = step_discord_morning(articles, paths, date_str, agenda_text=agenda_text)
        _update_status(status_mgr, status, discord_morning=ok, last_step="discord_morning")
    except Exception as exc:
        log.error("Discord morning post error: %s", exc)
        _append_error(status, f"discord_morning: {exc}")
        _update_status(status_mgr, status, last_step="discord_morning_failed")


def run_evening_pipeline() -> None:
    """Run the evening pipeline."""
    date_str = today_str()
    log.info("====== Evening pipeline start (%s) ======", date_str)

    status_mgr = get_status_manager()
    status = status_mgr.get(date_str)

    if "metadata" not in _pipeline_state:
        _restore_state(date_str)

    articles = _pipeline_state.get("articles", [])
    paths = _pipeline_state.get("paths", {})
    metadata = _pipeline_state.get("metadata")

    if metadata is None:
        log.error("No metadata available - cannot proceed with evening pipeline")
        _append_error(status, "evening: metadata_missing")
        _update_status(status_mgr, status, last_step="evening_skipped_no_metadata")
        return

    # If video was not downloaded in the morning, attempt re-download from existing notebook
    video_path = paths.get("video")
    if (not video_path or not Path(video_path).exists()) and status.notebook_id:
        log.info(
            "Video not yet downloaded – attempting re-download from notebook %s",
            status.notebook_id,
        )
        try:
            client = NotebookLMClient()
            new_paths = client.download_assets(
                status.notebook_id,
                output_dir(date_str),
                audio=False,
                video=True,
            )
            if new_paths.get("video") and Path(new_paths["video"]).exists():
                paths["video"] = new_paths["video"]
                _pipeline_state["paths"] = paths
                _update_status(
                    status_mgr, status,
                    video_downloaded=True,
                    last_step="video_redownloaded",
                )
                log.info("Video re-download succeeded: %s", paths["video"])
            else:
                log.info("Video still not ready – YouTube upload will be skipped")
        except NotebookLMAuthError as exc:
            log.warning("Video re-download skipped (auth error): %s", exc)
        except Exception as exc:
            log.warning("Video re-download attempt failed: %s", exc)

    try:
        youtube_url = step_youtube(paths, metadata, date_str)
        changes = {
            "youtube_uploaded": youtube_url is not None,
            "last_step": "youtube_uploaded" if youtube_url else "youtube_missing",
        }
        if youtube_url:
            changes["youtube_url"] = youtube_url
        _update_status(status_mgr, status, **changes)
    except Exception as exc:
        log.error("YouTube upload error: %s", exc)
        _append_error(status, f"youtube: {exc}")
        _update_status(status_mgr, status, last_step="youtube_failed")
        _notify_error(f"YouTube upload failed: {exc}")
        youtube_url = None

    log.info("Waiting 5 minutes before Discord night notification...")
    time.sleep(300)

    if youtube_url:
        try:
            ok = step_discord_night(youtube_url, metadata, articles, date_str)
            _update_status(status_mgr, status, discord_notified=ok, last_step="discord_night")
        except Exception as exc:
            log.error("Discord night notification error: %s", exc)
            _append_error(status, f"discord_night: {exc}")
            _update_status(status_mgr, status, last_step="discord_night_failed")

    log.info("====== Evening pipeline complete ======")


def _restore_state(date_str: str) -> None:
    """Restore state from saved metadata.json and status-backed articles."""
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

    agenda_file = out_dir / "agenda.txt"
    if agenda_file.exists() and agenda_file.stat().st_size > 0:
        _pipeline_state["agenda_text"] = agenda_file.read_text(encoding="utf-8")

    status_mgr = get_status_manager()
    _pipeline_state["articles"] = status_mgr.load_articles(date_str)


def _notify_error(message: str) -> None:
    try:
        import httpx

        if config.DISCORD_WEBHOOK_URL:
            httpx.post(
                config.DISCORD_WEBHOOK_URL,
                json={"content": f"**DailyCatchUp Error**\n{message}"},
                timeout=10,
            )
    except Exception:
        pass


def _notify_auth_required() -> None:
    message = (
        "**DailyCatchUp** NotebookLM の認証セッションが期限切れです。\n"
        "次のコマンドでセッションを更新してください:\n"
        "```\npython scripts/save_session.py\n```"
    )
    try:
        import httpx

        if config.DISCORD_WEBHOOK_URL:
            httpx.post(
                config.DISCORD_WEBHOOK_URL,
                json={"content": message},
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
    """Run the full pipeline immediately."""
    log.info("Running full pipeline NOW (test mode)")
    run_morning_pipeline()
    run_evening_pipeline()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DailyCatchUp pipeline runner")
    parser.add_argument("--now", action="store_true", help="Run full pipeline immediately")
    parser.add_argument("--morning", action="store_true", help="Run morning pipeline manually")
    parser.add_argument("--evening", action="store_true", help="Run evening pipeline manually")
    parser.add_argument(
        "--step",
        choices=[
            "collect", "notebooklm", "download", "meta",
            "discord-morning", "youtube", "discord-night",
            "video-download",
        ],
        help="Run a single pipeline step",
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Target date for manual runs (default: today)",
    )
    parser.add_argument(
        "--skip-nlm",
        action="store_true",
        help="Skip NotebookLM steps; post to Discord using already-downloaded assets",
    )
    args = parser.parse_args()

    date_str = args.date or today_str()

    if args.now:
        run_now()
    elif args.morning:
        run_morning_pipeline(date_str=date_str, skip_nlm=args.skip_nlm)
    elif args.evening:
        run_evening_pipeline()
    elif args.step:
        if args.step == "collect":
            articles = step_collect(date_str)
            print(f"Collected {len(articles)} articles")
        elif args.step == "discord-morning":
            _restore_state(date_str)
            step_discord_morning(
                _pipeline_state.get("articles", []),
                _pipeline_state.get("paths", {}),
                date_str,
            )
        elif args.step == "youtube":
            _restore_state(date_str)
            metadata = _pipeline_state.get("metadata")
            if metadata:
                url = step_youtube(_pipeline_state.get("paths", {}), metadata, date_str)
                print(f"YouTube URL: {url}")
        elif args.step == "discord-night":
            _restore_state(date_str)
            metadata = _pipeline_state.get("metadata")
            status = get_status_manager().get(date_str)
            if metadata and status.youtube_url:
                step_discord_night(
                    status.youtube_url,
                    metadata,
                    _pipeline_state.get("articles", []),
                    date_str,
                )
        elif args.step == "video-download":
            status_mgr = get_status_manager()
            status = status_mgr.get(date_str)
            if not status.notebook_id:
                log.error("No notebook_id in status for %s – run morning pipeline first", date_str)
                sys.exit(1)
            log.info("Attempting video download from notebook %s", status.notebook_id)
            client = NotebookLMClient()
            new_paths = client.download_assets(
                status.notebook_id,
                output_dir(date_str),
                audio=False,
                video=True,
            )
            if new_paths.get("video") and Path(new_paths["video"]).exists():
                _update_status(status_mgr, status, video_downloaded=True, last_step="video_redownloaded")
                print(f"Video downloaded: {new_paths['video']}")
            else:
                print("Video not yet available – try again later or check NotebookLM directly")
        else:
            log.error("Step '%s' requires full pipeline context. Use --now instead.", args.step)
            sys.exit(1)
    else:
        run_daemon()
