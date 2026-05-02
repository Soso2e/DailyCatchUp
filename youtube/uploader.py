"""Upload videos to YouTube using the Data API v3.

OAuth2 token is cached in YOUTUBE_TOKEN_FILE after first auth.
Run `python -m youtube.uploader --auth` to perform initial authentication.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List

import config
from logger import get_logger
from meta.meta_generator import VideoMetadata

log = get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"


def _get_authenticated_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_file = Path(config.YOUTUBE_TOKEN_FILE)
    creds: Credentials | None = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secret_file = Path(config.YOUTUBE_CLIENT_SECRET_FILE)
            if not secret_file.exists():
                raise FileNotFoundError(
                    f"YouTube client_secret.json not found: {secret_file}\n"
                    "Download it from Google Cloud Console → APIs & Services → Credentials"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), SCOPES)
            creds = flow.run_local_server(port=0)

        token_file.write_text(creds.to_json(), encoding="utf-8")
        log.info("YouTube token saved to %s", token_file)

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION, credentials=creds)


def upload_video(
    video_path: Path,
    thumbnail_path: Path | None,
    metadata: VideoMetadata,
    date_str: str,
) -> str | None:
    """Upload video and thumbnail.  Returns the YouTube video URL on success."""
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    if not video_path.exists():
        log.error("Video file not found: %s", video_path)
        return None

    youtube = _get_authenticated_service()

    body = {
        "snippet": {
            "title": metadata.title,
            "description": metadata.description,
            "tags": metadata.tags,
            "categoryId": "28",  # Science & Technology
            "defaultLanguage": "ja",
        },
        "status": {
            "privacyStatus": config.YOUTUBE_PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 10,  # 10 MB chunks
    )

    log.info("Starting YouTube upload: %s", metadata.title)
    video_id: str | None = None

    for attempt in range(1, config.RETRY_COUNT + 1):
        try:
            request = youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media,
            )
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    log.debug("Upload progress: %d%%", progress)

            video_id = response["id"]
            log.info("YouTube upload complete: video_id=%s", video_id)
            break

        except HttpError as exc:
            if exc.resp.status in (500, 502, 503, 504):
                log.warning("YouTube server error (attempt %d): %s", attempt, exc)
                if attempt < config.RETRY_COUNT:
                    time.sleep(config.RETRY_BACKOFF * attempt)
            else:
                log.error("YouTube upload HTTP error: %s", exc)
                return None
        except Exception as exc:
            log.error("YouTube upload failed (attempt %d): %s", attempt, exc)
            if attempt < config.RETRY_COUNT:
                time.sleep(config.RETRY_BACKOFF * attempt)
            else:
                return None

    if not video_id:
        return None

    # Set thumbnail
    if thumbnail_path and thumbnail_path.exists():
        _set_thumbnail(youtube, video_id, thumbnail_path)

    return f"https://youtu.be/{video_id}"


def _set_thumbnail(youtube, video_id: str, thumbnail_path: Path) -> None:
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError

    try:
        media = MediaFileUpload(str(thumbnail_path), mimetype="image/png")
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        log.info("Thumbnail set for video %s", video_id)
    except HttpError as exc:
        log.warning("Thumbnail upload failed: %s", exc)


if __name__ == "__main__":
    if "--auth" in sys.argv:
        log.info("Running YouTube OAuth2 authentication flow")
        _get_authenticated_service()
        log.info("Authentication successful. Token saved.")
