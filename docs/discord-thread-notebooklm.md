# DiscordスレッドからNotebookLMへ質問する

DailyCatchUpが投稿した、その日の朝刊メッセージからDiscordスレッドを作成すると、スレッド内の通常メッセージを質問として受け取り、その日のNotebookLMへ送信して回答します。

## 使い方

1. DailyCatchUpの以下いずれかの投稿からスレッドを作成します。
   - AI・ゲームニュース朝刊
   - 本日のアジェンダ（NotebookLM）
   - 本日の音声ニュース
   - 本日の要約テキスト
2. スレッド内に質問を投稿します。
3. Botが、その投稿日に対応するNotebookLMのソースを使って回答します。

NotebookLMへは毎回、次の条件を付けて質問します。

- ノートブック内のソースだけを根拠にする
- 情報不足の場合は推測しない
- 日本語で結論を先に書く
- Discordにそのままコピペできる形で返信する
- 表を避け、見出し・箇条書き・短い段落を使う

## Discord Developer Portalの設定

Botの **Message Content Intent** を有効にしてください。

1. Discord Developer PortalでDailyCatchUpのApplicationを開く
2. **Bot** を開く
3. **Privileged Gateway Intents** の **Message Content Intent** をONにする
4. Botを再起動する

## Botに必要なテキスト権限

通知先チャンネルと、そのスレッドで以下を許可してください。

- View Channel
- Send Messages
- Send Messages in Threads
- Read Message History
- Embed Links
- Attach Files

既存の音声再生を使う場合は、別途 Connect / Speak / Use Voice Activity も必要です。

## 日付とNotebookLMの対応

朝刊Embedに `YYYY-MM-DD` が含まれる場合は、その日付を使います。アジェンダや添付ファイル投稿のように本文へ日付がない場合は、投稿時刻を日本時間へ変換した日付を使います。

対象日のステータスに `notebook_id` が保存されていない場合は、スレッドへエラーを返します。朝のパイプラインが完了し、NotebookLMのIDが保存されている必要があります。

## 運用上の注意

- Discord Botは質問を受け取るため常時起動している必要があります。
- NotebookLMへの問い合わせは1件ずつ処理し、同時投稿は順番に回答します。
- Discordの文字数上限を超える回答は複数メッセージへ分割します。
- Bot自身や他のBotが投稿したメッセージは質問として処理しません。
- DailyCatchUp以外の投稿から作成したスレッドには反応しません。
