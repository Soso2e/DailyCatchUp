"""Renew the NotebookLM session used by the pipeline.

通常用途（セッション期限切れ時）:
    python scripts/save_session.py

動作:
  1. まず headless ブラウザで自動更新を試みる（ブラウザプロファイルが有効なら
     ユーザー操作不要）。
  2. ブラウザプロファイル自体も期限切れの場合は GUI ブラウザを開き、
     Google ログイン後に Enter を押して保存する。

セッションは ~/.notebooklm/storage_state.json に保存される。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from logger import get_logger
from nlm.session_manager import SessionExpiredError, auto_refresh_session, verify_session

log = get_logger(__name__)

NOTEBOOKLM_URL = "https://notebooklm.google.com"
GOOGLE_ACCOUNTS_URL = "https://accounts.google.com/"


def _manual_login() -> Path:
    """Open a visible browser, wait for the user to log in, then save the session."""
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

    print(f"Session file   : {storage_path}")
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

        print("ブラウザが開きました。")
        print("  1. Google ログインを完了させてください")
        print("  2. NotebookLM のホーム画面が表示されたことを確認してください")
        print("  3. ここで Enter を押してセッションを保存してください")
        print()
        input("準備ができたら Enter >>> ")

        # Navigate to get .google.com cookies for regional users, then back.
        page.goto(GOOGLE_ACCOUNTS_URL, wait_until="load")
        page.goto(NOTEBOOKLM_URL, wait_until="load")

        current_url = page.url
        if "notebooklm.google.com" not in current_url:
            print(f"警告: 想定外の URL です: {current_url}")
            answer = input("このまま保存しますか？ [y/N] ").strip().lower()
            if answer != "y":
                context.close()
                print("中断しました。")
                sys.exit(1)

        context.storage_state(path=str(storage_path))
        storage_path.chmod(0o600)
        context.close()

    return storage_path


def main() -> None:
    from notebooklm.paths import get_storage_path
    storage_path = get_storage_path()

    # Step 1: Try automated headless refresh.
    print("自動更新を試みています（headless）...")
    try:
        auto_refresh_session()
        if verify_session(storage_path):
            print(f"セッションを自動更新しました: {storage_path}")
            log.info("Session auto-refreshed via save_session.py")
            return
        print("自動更新後の検証に失敗しました。手動ログインに切り替えます。")
    except SessionExpiredError as exc:
        print(f"ブラウザプロファイルが期限切れです: {exc}")
        print("手動ログインに切り替えます。")
    except Exception as exc:
        print(f"自動更新でエラーが発生しました: {exc}")
        print("手動ログインに切り替えます。")

    # Step 2: Fall back to manual browser login.
    print()
    storage_path = _manual_login()
    print()
    print(f"セッションを保存しました: {storage_path}")
    log.info("Session saved manually to %s", storage_path)

    # Verify the manually saved session.
    print("検証中...")
    if verify_session(storage_path):
        print("セッションの検証に成功しました。パイプラインを実行できます。")
        log.info("Manual session verification passed")
    else:
        print("警告: セッションの検証に失敗しました。")
        print("ファイルは保存されましたが、正常に動作しない可能性があります。")
        print("パイプラインを実行して確認してください。")
        log.warning("Manual session verification failed")


if __name__ == "__main__":
    main()
