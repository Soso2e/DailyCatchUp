"""One-time script to manually log in to Google and save the session.

Run this interactively whenever the NotebookLM session expires:

    python scripts/save_session.py

A real Chrome window opens. Log in to Google (including any 2FA),
navigate to NotebookLM, then press Enter in this terminal to save the session.
The browser profile is stored in .chrome_profile/ and reused by the pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from logger import get_logger

log = get_logger(__name__)

NOTEBOOKLM_URL = "https://notebooklm.google.com"
PROFILE_DIR = Path(config.CHROME_PROFILE_DIR)


def main() -> None:
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Chrome profile: {PROFILE_DIR}")
    print()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(NOTEBOOKLM_URL)

        print("Chrome opened. Please:")
        print("  1. Complete Google login (including any 2FA)")
        print("  2. Confirm you can see the NotebookLM home page")
        print("  3. Press Enter here to finish")
        print()
        input("Press Enter when ready >>> ")

        if "accounts.google.com" in page.url:
            print("ERROR: Still on Google login page – login was not completed.")
            context.close()
            sys.exit(1)

        context.close()
        print(f"Session saved in {PROFILE_DIR}")
        log.info("Chrome profile saved to %s", PROFILE_DIR)


if __name__ == "__main__":
    main()
