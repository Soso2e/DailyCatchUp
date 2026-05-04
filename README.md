# DailyCatchUp

AI・ゲーム業界の最新ニュースを毎日自動収集し、NotebookLM で音声・動画を生成して Discord と YouTube に配信するパイプラインシステム。

---

## 全体フロー

```
06:00  ニュース収集（RSS / News API / Qiita / HackerNews）
         ↓ 重複除去・スコアリングで 3〜5 件に絞り込み
       NotebookLM にソース投入 → 音声・動画・要約を生成
         ↓ ポーリングで完了を待機（最大 15 分）
       アセット取得（audio.mp3 / video.mp4 / summary.md）
         ↓
       Gemini API でメタ情報生成 → Pillow でサムネイル生成

07:00  Discord 朝配信（音声 + 要約テキスト）

19:00  YouTube に動画投稿

19:05  Discord に YouTube 完了通知
```

---

## モジュール構成

```
DailyCatchUp/
├ collector/
│   ├ rss_collector.py        RSS収集（日英メディア）
│   ├ newsapi_collector.py    News API収集
│   ├ qiita_collector.py      Qiita収集
│   ├ hackernews_collector.py HackerNews収集
│   └ filter.py               重複除去・スコアリング
├ nlm/
│   ├ client.py               NotebookLM操作（notebooklm-py）
│   ├ playwright_fallback.py  Playwright fallback
│   ├ poller.py               生成完了ポーリング
│   └ url_resolver.py         URL正規化
├ downloader/
│   └ asset_downloader.py     音声・動画・要約の取得
├ meta/
│   ├ meta_generator.py       Gemini API でメタ情報生成
│   └ thumbnail.py            Pillow でサムネイル生成（1280×720）
├ notion/
│   ├ status_manager.py       日次ステータス管理（Notion DB / SQLite fallback）
│   └ article_store.py        収集記事のキャッシュ
├ discord/
│   ├ bot.py                  スラッシュコマンド Bot
│   ├ morning_post.py         朝のWebhook投稿
│   └ night_notify.py         夜のYouTube完了通知
├ youtube/
│   └ uploader.py             YouTube Data API v3 投稿
├ scheduler/
│   └ runner.py               パイプライン全体の実行
├ scripts/
│   └ save_session.py         NotebookLM セッション保存
├ outputs/                    生成物格納ディレクトリ
├ config.py                   環境変数読み込み
├ logger.py                   ロガー（stdout + ファイル）
└ setup_scheduler.ps1         Windows タスクスケジューラ登録
```

---

## セットアップ

### 1. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 2. 環境変数

`.env` ファイルをプロジェクトルートに作成する。

```dotenv
# ニュース収集
NEWS_API_KEY=...
QIITA_API_TOKEN=...           # 省略可（レート制限が緩和される）

# NotebookLM（Google アカウント）
GOOGLE_EMAIL=...
GOOGLE_PASSWORD=...
NOTEBOOKLM_SESSION_FILE=.notebooklm_session.json
CHROME_PROFILE_DIR=.chrome_profile

# メタ情報生成
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.0-flash  # デフォルト

# Discord
DISCORD_WEBHOOK_URL=...
DISCORD_BOT_TOKEN=...
DISCORD_CHANNEL_ID=...

# YouTube
YOUTUBE_CLIENT_SECRET_FILE=client_secret.json
YOUTUBE_TOKEN_FILE=.youtube_token.json
YOUTUBE_PRIVACY_STATUS=unlisted  # 本番は public

# Notion
NOTION_API_KEY=...
NOTION_DATABASE_ID=...

# パイプライン設定（省略可）
MAX_ARTICLES=5
MIN_ARTICLES=2
MIN_SOURCES_TO_GENERATE=3
NEWS_MAX_AGE_HOURS=24
NOTEBOOKLM_POLL_INTERVAL=60
NOTEBOOKLM_MAX_WAIT=900
RETRY_COUNT=3
RETRY_BACKOFF=5.0
```

### 3. YouTube 認証（初回のみ）

```bash
python -m youtube.uploader --auth
```

### 4. タスクスケジューラへの登録（Windows）

PowerShell を管理者権限で実行:

```powershell
.\setup_scheduler.ps1
```

以下の 3 タスクが登録される:

| タスク名 | トリガー | 処理 |
|---|---|---|
| `DailyCatchUp-Morning` | 毎日 06:00 | 朝パイプライン |
| `DailyCatchUp-Evening` | 毎日 19:00 | 夜パイプライン |
| `DailyCatchUp-Bot` | ログオン時 | Discord Bot 常駐 |

---

## 手動実行

```bash
# パイプライン全体を即時実行（テスト用）
python -m scheduler.runner --now

# 朝パイプラインのみ
python -m scheduler.runner --morning

# 夜パイプラインのみ
python -m scheduler.runner --evening

# 個別ステップ
python -m scheduler.runner --step collect
python -m scheduler.runner --step discord-morning
python -m scheduler.runner --step youtube
python -m scheduler.runner --step discord-night

# デーモンモード（schedule ライブラリで 06:00/19:00 に自動実行）
python -m scheduler.runner
```

---

## Discord Bot コマンド

Bot を手動起動する場合:

```bash
python -m discord.bot
```

| コマンド | 内容 |
|---|---|
| `/news today` | 今日のニュース要約を表示 |
| `/news summary` | 要点のみ表示（先頭 10 行） |
| `/news youtube` | 今日の YouTube 動画リンクを表示 |
| `/news play` | VC で音声ニュースを再生 |

---

## 出力ファイル

```
outputs/
  YYYY-MM-DD/
    audio.mp3        NotebookLM Podcast
    video.mp4        NotebookLM 動画解説
    summary.md       要約テキスト
    metadata.json    タイトル・概要欄・タグ・YouTube URL
    thumbnail.png    サムネイル（1280×720）
    run.log          実行ログ
```

---

## Notion ステータス DB スキーマ

1 日 1 レコード。Notion 連携が難しい場合は SQLite に自動フォールバック。

| カラム | 内容 |
|---|---|
| `date` | 対象日付 |
| `news_collected` | ニュース収集完了 |
| `notebook_created` | NotebookLM ノートブック作成完了 |
| `source_added` | ソース追加完了 |
| `generation_requested` | 生成リクエスト送信済み |
| `notebooklm_generated` | 生成完了 |
| `audio_downloaded` | 音声取得完了 |
| `video_downloaded` | 動画取得完了 |
| `meta_generated` | メタ情報生成完了 |
| `discord_morning` | Discord 朝配信完了 |
| `youtube_uploaded` | YouTube 投稿完了 |
| `discord_notified` | Discord 夜通知完了 |
| `youtube_url` | 投稿済み YouTube URL |
| `error_log` | エラー内容 |

---

## エラー対策

| エラー | 対策 |
|---|---|
| NotebookLM 操作失敗 | Playwright fallback → Discord エラー通知 |
| Google ログイン切れ | セッション再保存スクリプト / 手動再認証通知 |
| 生成タイムアウト | 最大待機 15 分・リトライ |
| ニュース取得不足 | 収集範囲を 48h に拡大してリトライ |
| YouTube 投稿失敗 | リトライ（最大 3 回）・Discord 通知 |
| Discord 通知失敗 | ログ保存のみ（致命的エラーとしない） |

---

## 技術スタック

| カテゴリ | 技術 |
|---|---|
| スケジューラー | Windows Task Scheduler / `schedule` ライブラリ |
| ニュース収集 | RSS, Google News RSS, News API, Qiita API, HackerNews API |
| NotebookLM 操作 | notebooklm-py, Playwright |
| メタ情報生成 | Gemini API (`gemini-2.0-flash`) |
| サムネイル生成 | Pillow |
| ステータス管理 | Notion DB（→ SQLite fallback） |
| 配信 | YouTube Data API v3, discord.py (Bot + Webhook) |
