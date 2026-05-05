"""Automated NotebookLM session management.

Refreshes ~/.notebooklm/storage_state.json using the persistent browser
profile.  If the browser profile already has a valid Google session
(the common case), this requires no user interaction and runs headless.

If the browser profile itself is also expired, it raises SessionExpiredError
so the caller can fall back to the interactive manual flow.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from logger import get_logger

log = get_logger(__name__)

NOTEBOOKLM_URL = "https://notebooklm.google.com"
GOOGLE_ACCOUNTS_URL = "https://accounts.google.com/"


class SessionExpiredError(Exception):
    """Raised when the browser profile is also expired and needs manual login."""


def auto_refresh_session() -> Path:
    """Refresh storage_state.json non-interactively using the persistent profile.

    Opens Chromium headless with the existing browser profile.  If Google
    still considers the user logged in, saves fresh cookies to
    storage_state.json and returns the path.

    Raises:
        SessionExpiredError: browser profile is expired; manual login required.
        RuntimeError: unexpected error during browser automation.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed. Run: pip install 'notebooklm-py[browser]'"
        ) from exc

    from notebooklm.paths import get_browser_profile_dir, get_storage_path

    storage_path = get_storage_path()
    browser_profile = get_browser_profile_dir()

    if not browser_profile.exists():
        raise SessionExpiredError(
            f"Browser profile not found at {browser_profile}. "
            "Run `python scripts/save_session.py` to log in."
        )

    storage_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    log.info("Auto-refreshing NotebookLM session (headless)...")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile),
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--password-store=basic",
            ],
            ignore_default_args=["--enable-automation"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()

            # Navigate to accounts.google.com first so regional users get
            # .google.com cookies, then to NotebookLM.
            page.goto(GOOGLE_ACCOUNTS_URL, wait_until="load", timeout=30_000)
            page.goto(NOTEBOOKLM_URL, wait_until="load", timeout=30_000)

            final_url = page.url
            if "accounts.google.com" in final_url:
                raise SessionExpiredError(
                    "Browser profile session has expired (redirected to Google login). "
                    "Run `python scripts/save_session.py` to re-authenticate."
                )

            context.storage_state(path=str(storage_path))
            storage_path.chmod(0o600)
        finally:
            context.close()

    log.info("Session refreshed → %s", storage_path)
    return storage_path


def verify_session(storage_path: Path | None = None) -> bool:
    """Return True if the session file has valid, working credentials."""
    async def _check():
        from notebooklm.auth import AuthTokens
        await AuthTokens.from_storage(storage_path)
        return True

    try:
        return asyncio.run(_check())
    except Exception as exc:
        log.debug("Session verify failed: %s", exc)
        return False


def ensure_valid_session() -> None:
    """Guarantee a valid session exists before the pipeline runs.

    Strategy:
    1. Check if current storage_state.json is already valid (fast network ping).
    2. If not, run auto_refresh_session() using the persistent browser profile.
    3. If browser profile is also expired, raise SessionExpiredError.

    Raises:
        SessionExpiredError: full manual re-login is required.
        RuntimeError: unexpected playwright / filesystem error.
    """
    from notebooklm.paths import get_storage_path
    storage_path = get_storage_path()

    if verify_session(storage_path):
        log.info("NotebookLM session is valid")
        return

    log.info("Session invalid or missing — attempting auto-refresh...")
    refreshed = auto_refresh_session()

    if not verify_session(refreshed):
        raise SessionExpiredError(
            "Session refresh succeeded but verification still failed. "
            "Run `python scripts/save_session.py` to re-authenticate."
        )

    log.info("Session auto-refreshed successfully")
