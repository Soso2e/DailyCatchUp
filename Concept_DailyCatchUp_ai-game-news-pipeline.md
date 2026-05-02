# AI・ゲーム業界ニュース自動生成・配信システム 設計ドキュメント v2

最終更新：2026-05-02

---

## 概要

毎日、AI・ゲーム業界の最新ニュースを自動収集し、NotebookLMを用いて要約・音声化・動画化を行う。
朝はDiscordで音声ニュースとして配信し、夜はYouTubeへ動画投稿する。
最終的には、Discord Botからニュース再生・要約確認・リンク取得もできるようにする。

---

## 目的

- 毎日のAI・ゲーム業界ニュース（英日混在）を自動で届ける
- NotebookLMを活用して、要約・音声・動画コンテンツを生成する
- DiscordとYouTubeを連携し、朝と夜で異なる導線を作る
- 将来的に「AIニュース番組の自動生成システム」として運用する

---

## 確定方針

| 項目 | 決定内容 |
|---|---|
| ニュース収集言語 | 英語・日本語 混在 |
| ニュース収集ソース | RSS + News API 両方 |
| NotebookLM操作 | notebooklm-py（Playwright fallback） |
| 動画生成 | NotebookLM 動画解説機能（検証込み） |
| ステータス管理 | Notion DB（1日1レコード）→ 詰まったらSQLite |
| YouTube API | 無料枠で問題なし（動画アップロード約100ユニット） |
| YouTube投稿 | 毎日・公開前提 |
| メタ情報生成 | Claude or ローカルLLM |
| スケジューラー | Claude Code or cron / Task Scheduler |

---

## 投稿スケジュール

| 時刻 | 処理 |
|---|---|
| 06:00 | 前日のニュース収集・NotebookLM投入・生成開始 |
| 07:00 前後 | Discordに音声ニュース・要約テキストを投稿 |
| 19:00 | YouTubeへ動画投稿 |
| 19:05 | DiscordへYouTube投稿完了を通知 |

---

## 全体フロー

```
[Scheduler]
   ↓
[ニュース収集]
  ├ RSS（日本語・英語メディア）
  └ News API（英語強化）
   ↓
[記事フィルタリング]
  ├ 重複除去
  ├ 重要度スコアリング
  └ 3〜5件に絞り込み
   ↓
[NotebookLM操作]（notebooklm-py）
  ├ 記事投入
  ├ 要約生成
  ├ Podcast（音声）生成
  └ 動画解説生成
   ↓
[生成待機]（5〜15分、完了ポーリング）
   ↓
[成果物取得]
  ├ audio.mp3
  ├ video.mp4
  └ summary.md
   ↓
[メタ情報生成]（Claude）
  ├ YouTubeタイトル
  ├ 概要欄
  ├ タグ・ハッシュタグ
  └ サムネイル文言
   ↓
[サムネイル生成]（Python + Pillow）
   ↓
[Notion ステータス更新]
   ↓
[Discord朝配信]（07:00）
  ├ audio.mp3 投稿
  └ summary.md 簡易テキスト添付
   ↓
[YouTube夜投稿]（19:00）
  ├ video.mp4
  ├ タイトル・概要欄・タグ
  └ サムネイル
   ↓
[Discord夜通知]（19:05）
  ├ YouTube URL
  └ トピック概要
```

---

## モジュール構成

```
project/
  ├ collector/
  │   ├ rss_collector.py        # RSS収集（日本語・英語）
  │   ├ newsapi_collector.py    # News API収集
  │   └ filter.py               # 重複除去・スコアリング・絞り込み
  │
  ├ notebooklm/
  │   ├ client.py               # notebooklm-py ラッパー
  │   ├ playwright_fallback.py  # Playwright fallback操作
  │   └ poller.py               # 生成完了ポーリング
  │
  ├ downloader/
  │   └ asset_downloader.py     # audio / video / summary 取得
  │
  ├ meta/
  │   ├ meta_generator.py       # Claude API でメタ情報生成
  │   └ thumbnail.py            # Pillow でサムネイル生成
  │
  ├ notion/
  │   └ status_manager.py       # Notion DB ステータス更新
  │
  ├ discord/
  │   ├ morning_post.py         # 朝の音声・要約投稿
  │   └ night_notify.py         # 夜のYouTube完了通知
  │
  ├ youtube/
  │   └ uploader.py             # YouTube Data API 動画投稿
  │
  ├ scheduler/
  │   └ runner.py               # 全体パイプライン実行
  │
  └ outputs/
      └ YYYY-MM-DD/
          ├ audio.mp3
          ├ video.mp4
          ├ summary.md
          ├ metadata.json
          └ thumbnail.png
```

---

## ニュース収集

### 対象ソース

| ソース | 言語 | 内容 |
|---|---|---|
| Google News RSS | 日本語・英語 | AI / ゲーム業界全般 |
| 各メディア公式RSS | 日本語 | 4Gamer, Famitsu, AImedia等 |
| News API | 英語 | TechCrunch, The Verge, VentureBeat等 |

### フィルタリング方針

- 重複記事の除去（タイトル類似度チェック）
- 重要度スコアリング（キーワード・新鮮度）
- 最終的に3〜5件に絞り込み
- 2件以下の場合：収集範囲を広げてリトライ

---

## NotebookLM操作

### 使用ライブラリ

- **主：** notebooklm-py
- **Fallback：** Playwright によるブラウザ自動操作

### 生成コンテンツ

| 種類 | 機能 | 備考 |
|---|---|---|
| 要約テキスト | ノートブック要約 | summary.md として保存 |
| 音声（Podcast） | Audio Overview 機能 | audio.mp3 |
| 動画 | 動画解説機能 | video.mp4（今回検証） |

### 安定化方針

NotebookLMは不安定になる前提で設計する。

```
notebooklm-py 失敗
  ↓
Playwright に切り替え
  ↓
それも失敗 → Discord にエラー通知 + ログ保存
```

---

## Notionステータス管理

### DBスキーマ（1日1レコード）

| カラム名 | 型 | 内容 |
|---|---|---|
| date | Date | 対象日付（主キー相当） |
| news_collected | Checkbox | ニュース収集完了 |
| notebooklm_generated | Checkbox | NotebookLM生成完了 |
| audio_downloaded | Checkbox | 音声取得完了 |
| video_downloaded | Checkbox | 動画取得完了 |
| meta_generated | Checkbox | メタ情報生成完了 |
| discord_morning | Checkbox | Discord朝配信完了 |
| youtube_uploaded | Checkbox | YouTube投稿完了 |
| discord_notified | Checkbox | Discord夜通知完了 |
| error_log | Text | エラー内容（あれば） |
| youtube_url | URL | 投稿済みYouTube URL |

### フォールバック

Notion連携が難しい場合はSQLiteで同等のスキーマを実装する。

---

## YouTube Data API

### クォータ（2025年更新後）

| 操作 | コスト |
|---|---|
| 動画アップロード（videos.insert） | 約100ユニット |
| サムネイル設定（thumbnails.set） | 50ユニット |
| 動画情報更新（videos.update） | 50ユニット |
| 1日のデフォルト上限 | 10,000ユニット |

→ 毎日投稿でもクォータは余裕で問題なし。料金は発生しない。

### 投稿設定

- 初期テスト時：限定公開
- 本運用：公開

---

## Discord Bot コマンド

| コマンド | 内容 |
|---|---|
| `/news today` | 今日のニュース要約を表示 |
| `/news play` | 今日の音声ニュースをVCで再生 |
| `/news summary` | 要点のみ表示 |
| `/news youtube` | 今日のYouTube動画リンクを表示 |

---

## エラー対策

### 想定されるエラーと対策

| エラー | 対策 |
|---|---|
| NotebookLM操作失敗 | Playwright fallback → Discord通知 |
| Googleログイン切れ | セッション維持スクリプト / 手動再認証通知 |
| 生成タイムアウト | 最大待機時間設定・リトライ |
| ダウンロード失敗 | リトライ・前回データ再利用 |
| YouTube投稿失敗 | リトライ・Discord通知 |
| Discord通知失敗 | ログ保存のみ |
| ニュース取得失敗 | ソース切り替え・件数不足時リトライ |

### 共通対策

- 全処理にリトライ処理
- ログ保存（outputs/YYYY-MM-DD/run.log）
- 失敗時のDiscord通知
- Notionステータスにエラー内容を記録

---

## 段階的実装計画

### Phase 1：コア生成パイプライン
- notebooklm-py 単体検証
- RSS + News API 収集モジュール
- NotebookLM投入・音声生成
- 音声ダウンロード

### Phase 2：Discord朝配信
- 音声投稿
- 要約テキスト投稿
- `/news today` 実装

### Phase 3：YouTube夜投稿
- 動画解説生成（検証）
- メタ情報生成（Claude）
- サムネイル生成（Pillow）
- YouTube投稿

### Phase 4：Discord VC再生
- VC参加・音声再生
- `/news play` 実装

### Phase 5：安定化・管理
- リトライ全面実装
- ログ基盤
- Notion / SQLite ステータス管理
- fallback全面整備

> **推奨：** Phase 5のログ基盤は Phase 3 前に薄く先行して作る。動画周りのトラブル追跡に必要。

---

## 使用技術スタック

| カテゴリ | 技術 |
|---|---|
| スケジューラー | Claude Code / cron / Windows Task Scheduler |
| ニュース収集 | RSS, Google News RSS, News API |
| NotebookLM操作 | notebooklm-py, Playwright |
| メタ情報生成 | Claude API |
| サムネイル生成 | Python + Pillow |
| 動画・音声 | NotebookLM生成 |
| ステータス管理 | Notion DB（→ SQLite fallback） |
| 配信 | YouTube Data API v3, Discord Bot, Discord Webhook |

---

## 出力ファイル構成

```
outputs/
  YYYY-MM-DD/
    audio.mp3        # NotebookLM Podcast
    video.mp4        # NotebookLM 動画解説
    summary.md       # 要約テキスト
    metadata.json    # タイトル・概要欄・タグ・YouTube URL等
    thumbnail.png    # サムネイル
    run.log          # 実行ログ
```

---

## ステータスJSONサンプル

```json
{
  "date": "2026-05-02",
  "news_collected": true,
  "notebooklm_generated": true,
  "audio_downloaded": true,
  "video_downloaded": true,
  "meta_generated": true,
  "discord_morning": true,
  "youtube_uploaded": true,
  "discord_notified": true,
  "youtube_url": "https://youtu.be/xxxxxxxx",
  "error_log": ""
}
```

---

## コンセプト名案

- Daily AI Game News Bot
- AI Game News Radio
- NotebookLM News Pipeline
- Auto News Studio
- AIニュース自動放送局
- Game & AI Daily Briefing
