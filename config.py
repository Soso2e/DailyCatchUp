import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", str(BASE_DIR / "outputs")))

# News API
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# NotebookLM (Google account)
GOOGLE_EMAIL = os.getenv("GOOGLE_EMAIL", "")
GOOGLE_PASSWORD = os.getenv("GOOGLE_PASSWORD", "")
NOTEBOOKLM_SESSION_FILE = os.getenv(
    "NOTEBOOKLM_SESSION_FILE", str(BASE_DIR / ".notebooklm_session.json")
)
CHROME_PROFILE_DIR = os.getenv(
    "CHROME_PROFILE_DIR", str(BASE_DIR / ".chrome_profile")
)

# Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# Discord
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "0")

# YouTube
YOUTUBE_CLIENT_SECRET_FILE = os.getenv(
    "YOUTUBE_CLIENT_SECRET_FILE", str(BASE_DIR / "client_secret.json")
)
YOUTUBE_TOKEN_FILE = os.getenv(
    "YOUTUBE_TOKEN_FILE", str(BASE_DIR / ".youtube_token.json")
)
YOUTUBE_PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "unlisted")

# Notion
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

# Pipeline settings
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "5"))
MIN_ARTICLES = int(os.getenv("MIN_ARTICLES", "2"))
MIN_SOURCES_TO_GENERATE = int(os.getenv("MIN_SOURCES_TO_GENERATE", "3"))
NEWS_MAX_AGE_HOURS = int(os.getenv("NEWS_MAX_AGE_HOURS", "24"))
NOTEBOOKLM_POLL_INTERVAL = int(os.getenv("NOTEBOOKLM_POLL_INTERVAL", "60"))
NOTEBOOKLM_MAX_WAIT = int(os.getenv("NOTEBOOKLM_MAX_WAIT", "900"))  # 15 min
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "3"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "5.0"))
