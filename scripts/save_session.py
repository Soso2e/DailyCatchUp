"""Renew the NotebookLM session used by the pipeline.

Run this interactively whenever the pipeline stops with an authentication error:

    python scripts/save_session.py

A real Chrome window opens.  Log in to Google, confirm you can see the
NotebookLM homepage, then press Enter here.  The session is saved to
~/.notebooklm/storage_state.json and reused by all subsequent pipeline runs.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from logger import get_logger

log = get_logger(__name__)

NOTEBOOKLM_URL = "https://notebooklm.google.com"
GOOGLE_ACCOUNTS_URL = "https://accounts.google.com/"


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright is not installed.")
        print("       Run: pip install 'notebooklm-py[browser]'")
        sys.exit(1)

    from notebooklm.paths import get_browser_profile_dir, get_storage_path

    storage_path = get_storage_path()
    browser_profile = get_browser_profile_dir()

    storage_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    browser_profile.mkdir(parents=True, exist_ok=True, mode=0o700)

    print(f"Session file : {storage_path}")
    print(f"Browser profile: {browser_profile}")
    print()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile),
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--password-store=basic",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(NOTEBOOKLM_URL)

        print("Browser opened.  Please:")
        print("  1. Complete Google login (including any 2FA)")
        print("  2. Confirm you can see the NotebookLM home page")
        print("  3. Press Enter here to save the session")
        print()
        input("Press Enter when ready >>> ")

        # Navigate to accounts.google.com first so regional users get
        # .google.com cookies, then back to NotebookLM.
        page.goto(GOOGLE_ACCOUNTS_URL, wait_until="load")
        page.goto(NOTEBOOKLM_URL, wait_until="load")

        current_url = page.url
        if "notebooklm.google.com" not in current_url:
            print(f"WARNING: Expected NotebookLM but current URL is: {current_url}")
            answer = input("Save session anyway? [y/N] ").strip().lower()
            if answer != "y":
                context.close()
                print("Aborted.")
                sys.exit(1)

        context.storage_state(path=str(storage_path))
        storage_path.chmod(0o600)
        context.close()

    print()
    print(f"Session saved to: {storage_path}")
    log.info("NotebookLM session saved to %s", storage_path)

    _verify_session(storage_path)


def _verify_session(storage_path: Path) -> None:
    """Quick smoke-test: load cookies and fetch CSRF token from NotebookLM."""
    print("Verifying session...")
    try:
        from notebooklm.auth import AuthTokens

        asyncio.run(AuthTokens.from_storage(storage_path))
        print("Session verified successfully.  The pipeline is ready to run.")
        log.info("Session verification passed")
    except Exception as exc:
        print(f"WARNING: Session verification failed: {exc}")
        print("The session file was saved but may not be valid yet.")
        print("Try running the pipeline; if it fails again, re-run this script.")
        log.warning("Session verification failed: %s", exc)


if __name__ == "__main__":
    main()
