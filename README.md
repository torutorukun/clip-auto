# dpro_notify — 競合広告モニタリング＆Chatwork通知システム

DPro（動画広告データベース）を毎日自動で巡回し、**指定した競合の新着動画広告**と**最近伸びている（バズっている）動画**を検知して、Chatworkに通知するシステム。あわせて文字起こしテキストをGoogle Sheetsに蓄積し、リライトパイプラインに流す。

GitHub Actions で毎日定時に無人実行される。Macを起動しておく必要はない。

---

## 1. システム概要

このシステムがやること:

1. **DProの広告DBを検索** — `watch_targets.json` に定義した競合ごとに、キーワード＋広告主名で動画広告を取得
2. **新着広告を検知** — 過去に通知していない広告のうち、投稿30日以内・再生数1万以上のものを「新着」として通知
3. **バズり動画を検知** — ターゲット内で再生数の伸び（`play_count_difference`）が中央値の3倍以上に急伸した過去動画を「伸びている動画」として通知（全体上位5件）
4. **Chatworkに通知** — 新着／バズりをそれぞれ整形してChatworkルームに投稿
5. **Google Sheetsに蓄積** — 会社名・文字起こし・URL・再生数・日付を月別シートに追記（URL重複は自動スキップ）
6. **リライトパイプライン** — 取得広告を `buzz_pipeline.py` に渡し、原稿リライト用スプレッドシートに蓄積

通知先・蓄積先:

- Chatwork: `CHATWORK_ROOM_ID` で指定したルーム
- リライト用スプレッドシート: `REWRITE_SHEETS_URL`（`dpro_monitor.py` 内）
- 蓄積用スプレッドシート: `SHEETS_ID`（`dpro_monitor.py` 内、月別タブ `YYYY_MM` に追記）

---

## 2. 仕組み（DPro → GitHub Actions → Chatwork の流れ）

```
┌─────────────────┐
│ GitHub Actions   │  cron: 毎日 02:30 UTC（＝11:30 JST）
│ (スケジューラ)   │  手動実行も可（workflow_dispatch）
└────────┬────────┘
         │ 1. リポジトリをcheckout
         │ 2. Python 3.12 + requirements.txt をインストール
         │ 3. gog CLI（Google Sheets操作）をインストール・認証
         ▼
┌─────────────────┐
│ dpro_monitor.py  │  メイン処理
└────────┬────────┘
         │ ① Firebaseログイン（DPRO_EMAIL / DPRO_PASSWORD → idToken取得）
         │ ② watch_targets.json の各ターゲットをDPro APIで検索
         │    GET https://api.kashika-20mile.com/api/v1/items
         │ ③ 新着判定（未通知・30日以内・再生数1万以上）
         │ ④ バズり判定（伸び率が中央値の3倍以上・上位5件）
         ▼
┌──────────────────────────────┬──────────────────────────┐
│ Chatwork API                  │ Google Sheets (gog CLI)   │
│ 新着通知 / バズり通知を投稿   │ 月別タブに文字起こし蓄積  │
└──────────────────────────────┴──────────────────────────┘
         │
         ▼
┌─────────────────┐
│ 状態ファイルを   │  seen_ads.json（通知済み新着URL）
│ git commit&push  │  buzz_notified.json（通知済みバズりURL）
└─────────────────┘  → 次回実行時の重複通知を防ぐ
```

### 重複通知を防ぐ仕組み（重要）

再通知を防ぐため、通知済みURLを2つのJSONファイルに記録する:

- `seen_ads.json` — ラベルごとに通知済みの新着広告URL
- `buzz_notified.json` — ラベルごとに通知済みのバズり動画URL

GitHub Actionsは実行後にこの2ファイルを **リポジトリへ commit & push で書き戻す**（ワークフロー末尾の `Commit state files` ステップ）。これにより次回の実行で「前回どこまで通知したか」を引き継ぐ。**この書き戻しがシステムの心臓部**なので、`permissions: contents: write` は必須。

> 補足: 再生数1万未満の広告は「まだ伸びていない」とみなし、あえて `seen_ads.json` に記録しない。後日伸びて1万を超えたら、そのタイミングで新着通知される。

### 実行タイミング

- `.github/workflows/dpro_monitor.yml` の `cron: "30 2 * * *"`（UTC）で毎日1回
- GitHub Actionsのcronは負荷により数分〜十数分ずれることがある（仕様）
- リポジトリの Actions タブ → DPro Monitor → `Run workflow` で手動実行も可能

---

## 3. セットアップ手順（別の競合向けに同じシステムを作る）

別チーム・別競合セット向けに複製する場合の手順。**新しいGitHubリポジトリを1つ用意する**前提。

### 3-1. ファイルを新リポジトリにコピー

最低限必要なファイル:

```
dpro_monitor.py              # メイン処理
buzz_pipeline.py             # リライトパイプライン（Sheets蓄積）
watch_targets.json           # 監視ターゲット定義（★ここを競合に合わせて書き換える）
requirements.txt             # 依存パッケージ
.github/workflows/dpro_monitor.yml   # GitHub Actions定義
.env.example                 # ローカル実行用の環境変数サンプル
seen_ads.json                # 初期は {} で作成
buzz_notified.json           # 初期は {} で作成
```

`seen_ads.json` と `buzz_notified.json` は空オブジェクトで初期化しておく:

```bash
echo '{}' > seen_ads.json
echo '{}' > buzz_notified.json
```

> 注意: `.gitignore` に `.env` が入っていることを確認する（認証情報をコミットしない）。

### 3-2. `watch_targets.json` を競合に合わせて編集

監視したい競合を定義する（書き方は「4. watch_targets.json の書き方」参照）。

### 3-3. GitHub Secrets を設定

リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で以下を登録する。

| Secret名 | 内容 | 必須 | 備考 |
|---|---|---|---|
| `DPRO_EMAIL` | DProのログインメールアドレス | ✅ | Firebase認証に使用 |
| `DPRO_PASSWORD` | DProのログインパスワード | ✅ | Firebase認証に使用 |
| `CHATWORK_API_TOKEN` | ChatworkのAPIトークン | ✅ | 通知の投稿に使用 |
| `CHATWORK_ROOM_ID` | 通知先ChatworkルームID | ✅ | 数字のみ（例: `422901645`） |
| `ANTHROPIC_API_KEY` | Claude APIキー | ⛅️ | リライトパイプライン用。無くても新着/バズり通知は動く |
| `OPENAI_API_KEY` | OpenAI APIキー | ⛅️ | 同上（Whisper等） |
| `GOG_CREDENTIALS_JSON` | gog CLI用OAuthクライアント認証情報（JSON丸ごと） | ⛅️ | Google Sheets蓄積に使用 |
| `GOG_REFRESH_TOKEN` | gog CLI用リフレッシュトークン（JSON丸ごと） | ⛅️ | Google Sheets蓄積に使用 |

- ✅ = 必須（これが無いと通知そのものが動かない）
- ⛅️ = Sheets蓄積・リライトを使う場合のみ必須。通知だけで良ければ未設定でも動く（該当ステップがエラーになるがtry/exceptで握りつぶす設計）

> **DProの認証方式について**: 現行の `dpro_monitor.py` は Firebaseの Email/Password ログイン（`DPRO_EMAIL` / `DPRO_PASSWORD`）でBearerトークンを都度取得する。手元の `.env.example` には旧方式の `DPRO_BEARER_TOKEN` が残っているが**現行コードでは使っていない**。トークンは約1時間で失効するため、email/password方式にしている。

#### Google Sheets（gog）の認証情報を用意する場合

Sheets蓄積を使うなら、ローカルで [gog CLI](https://github.com/steipete/gogcli) を一度認証し、生成された認証ファイルの中身をSecretsに貼る:

- `GOG_CREDENTIALS_JSON` … `~/.config/gogcli/credentials.json` の中身
- `GOG_REFRESH_TOKEN` … `gog auth tokens export` で出力したトークンJSONの中身

あわせて `dpro_monitor.py` / `buzz_pipeline.py` 冒頭の `SHEETS_ID`・`GOG_ACCOUNT`・`REWRITE_SHEETS_URL` を、新しいスプレッドシート・アカウントに書き換える。

### 3-4. GitHub Actions を有効化する

1. コードをリポジトリに push する
2. リポジトリの **Actions** タブを開く
3. 初回はワークフロー実行が無効化されているので、「I understand my workflows, go ahead and enable them」を押して有効化する
4. 左メニューの **DPro Monitor** を選択 → 右上の **Run workflow** で手動実行し、動作確認する
5. ログ（各ステップ）でエラーが無いこと・Chatworkに通知が届くことを確認する
6. 以降は `cron` で毎日自動実行される

> Secretsが正しく渡っているかは、`Run dpro_monitor.py` ステップのログで「🔑 ログイン中... → ✅ ログイン成功！」が出ているかで判断できる。

### 3-5. ローカルで動作確認する（任意）

GitHub Actionsに乗せる前に手元で試す場合:

```bash
cd dpro_notify
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # 中身を実際の値に編集（下記フォーマットに注意）
python dpro_monitor.py --dry-run   # Chatworkに送らず、送信内容を標準出力に表示
python dpro_monitor.py             # 本番実行
```

`.env` は現行コードに合わせて以下のキーで記述する（`.env.example` の `DPRO_BEARER_TOKEN` は使わない）:

```
DPRO_EMAIL=あなたのDProログインメール
DPRO_PASSWORD=あなたのDProログインパスワード
CHATWORK_API_TOKEN=ChatworkのAPIトークン
CHATWORK_ROOM_ID=通知先ルームID
ANTHROPIC_API_KEY=（任意）
OPENAI_API_KEY=（任意）
```

`--dry-run` を付けるとChatworkへは送信せず、通知メッセージだけを画面に表示する（テストに便利）。

---

## 4. `watch_targets.json` の書き方

監視ターゲットの配列。1要素＝1競合（1ラベル）。

### フィールド一覧

| キー | 必須 | 型 | 説明 |
|---|---|---|---|
| `label` | ✅ | string | 通知に表示される競合名。`seen_ads.json` のキーにもなる |
| `search_keywords` | ✅ | string[] | DProの全文検索に投げるキーワード群（`in:キーワード`, OR検索） |
| `advertiser_names` | ✅ | string[] | 検索結果を**この広告主名に完全一致**するものだけに絞る |
| `product_names` | 任意 | string[] | 広告主名が一致しなくても、商品名に**部分一致**すれば拾う（表記揺れ対策） |
| `interval` | 任意 | number | 再生数の伸びを見る期間（日）。デフォルト30 |

### 動作イメージ

1. `search_keywords` の各キーワードでDProを全文検索（ページング最大5ページ）
2. ヒットした広告のうち `advertiser_names` に完全一致するものを採用
3. 一致しなくても `product_names` のいずれかが商品名に部分一致すれば採用
4. `EXCLUDE_ADVERTISERS` / `EXCLUDE_KEYWORDS`（`dpro_monitor.py` 内）に該当するものは除外（例: freee）

### 記入サンプル

```json
[
  {
    "label": "みかみ",
    "search_keywords": ["みかみ", "アドネス", "スキルプラス"],
    "advertiser_names": ["アドネス株式会社", "スキルプラス", "みかみ@AI活用の専門家"]
  },
  {
    "label": "かずくん",
    "search_keywords": ["AI ONE"],
    "advertiser_names": ["株式会社AI ONE"]
  },
  {
    "label": "Chokusai",
    "search_keywords": ["Chokusai", "チョク採", "直接採用"],
    "advertiser_names": ["株式会社これから"],
    "product_names": ["チョク採", "Chokusai", "COREKARA"]
  },
  {
    "label": "Driven Properties",
    "search_keywords": ["Driven Properties", "ドリブン・プロパティーズ"],
    "advertiser_names": ["Driven Properties", "ドリブン・プロパティーズ"],
    "product_names": ["Driven Properties", "ドリブン・プロパティーズ"]
  }
]
```

### 書くときのコツ

- **`search_keywords` は広めに、`advertiser_names` は正確に**。検索で広く拾い、広告主名で厳密に絞る二段構え
- 広告主名は表記揺れ（英語表記・全角半角・法人格の有無）を複数登録しておくと取りこぼしが減る（例: `"Beprime Co., Ltd."`, `"eprime Co., Ltd."` のようにOCR/入力揺れも入れる）
- 広告主名が安定しない競合は `product_names`（部分一致）で補完する
- `label` を変えると `seen_ads.json` 側のキーも変わるため、途中でリネームすると過去の通知履歴と紐付かなくなる点に注意

---

## 5. カスタマイズポイント

主要なしきい値・設定は `dpro_monitor.py` 冒頭の定数にまとまっている。

| 変数 / 場所 | デフォルト | 説明 | 変更するとどうなる |
|---|---|---|---|
| `MIN_PLAY_COUNT`（`dpro_monitor.py`） | `10000` | 通知する最低再生数 | 下げると小さい広告も通知・上げると大手だけに絞れる |
| `check_buzz()` の `median * 3` | 中央値の3倍 | バズり判定の伸び倍率 | 数字を上げるほど「急伸」の基準が厳しくなる |
| `check_buzz()` の `threshold < 1000` | 最低1000 | バズり判定の伸びの下限 | 全体が小規模なターゲットで誤検知を防ぐ下駄 |
| `buzz_ads[:5]`（`main()` 内） | 上位5件 | 1回の通知に載せるバズり動画の最大数 | 通知1回あたりの件数を増減 |
| 新着の `days > 30`（`main()` 内） | 30日 | 新着とみなす投稿日の範囲 | 「新着」の定義を広げる/狭める |
| リライト候補の `days > 7`（`main()` 内） | 7日 | Sheets/リライトに回す投稿日の範囲 | リライト対象の鮮度 |
| `BUZZ_TOP_PCT`（`dpro_monitor.py`） | `0.20` | パーセンタイル計算用の定数 | 上位何%を見るかの調整用 |
| `interval`（各ターゲット） | `30` | 再生数の伸びを見る期間（日） | ターゲット単位で伸びの観測窓を変える |
| `EXCLUDE_ADVERTISERS` / `EXCLUDE_KEYWORDS` | freee等 | 通知・リライトから除外する広告主/語 | 特定企業を丸ごと除外したいとき追加 |
| `cron`（`.github/workflows/dpro_monitor.yml`） | `30 2 * * *`（UTC） | 実行時刻 | 実行タイミングをずらす（UTC基準なのでJST-9時間で計算） |
| `SHEETS_ID` / `REWRITE_SHEETS_URL` / `GOG_ACCOUNT` | — | 蓄積先スプレッドシート | 別チームで複製するとき必ず差し替え |

### よくある調整

- **通知が多すぎる** → `MIN_PLAY_COUNT` を上げる／`buzz_ads[:5]` を減らす
- **通知が少なすぎる／取りこぼす** → `MIN_PLAY_COUNT` を下げる／`watch_targets.json` の `search_keywords`・`advertiser_names` を増やす
- **バズり判定が敏感すぎる** → `check_buzz()` の `median * 3` を `* 5` などに上げる
- **実行時刻を変えたい** → workflowの `cron` をUTCで指定（JST 9:00 にしたいなら `0 0 * * *`）

---

## ファイル構成（この仕組みに関係するもの）

| ファイル | 役割 |
|---|---|
| `dpro_monitor.py` | メイン。DPro検索→新着/バズり判定→Chatwork通知→Sheets蓄積 |
| `buzz_pipeline.py` | リライトパイプライン（Sheetsへの原稿蓄積・整形） |
| `watch_targets.json` | 監視ターゲット定義 |
| `seen_ads.json` | 通知済み新着URLの状態（自動更新・git書き戻し） |
| `buzz_notified.json` | 通知済みバズりURLの状態（自動更新・git書き戻し） |
| `.github/workflows/dpro_monitor.yml` | GitHub Actions定義（cron・Secrets・実行手順） |
| `requirements.txt` | Python依存パッケージ |
| `.env.example` | ローカル実行用の環境変数サンプル（※現行はemail/password方式） |
| `run_dpro_daily.sh` | （旧）Mac launchdでローカル毎日実行するためのラッパー。GitHub Actions運用では不要 |
