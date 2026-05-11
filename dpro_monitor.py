#!/usr/bin/env python3
import json, os, sys, subprocess, requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

DRY_RUN = "--dry-run" in sys.argv

load_dotenv(Path(__file__).parent / ".env")

DPRO_EMAIL         = os.environ.get("DPRO_EMAIL", "")
DPRO_PASSWORD      = os.environ.get("DPRO_PASSWORD", "")
CHATWORK_API_TOKEN = os.environ.get("CHATWORK_API_TOKEN", "")
CHATWORK_ROOM_ID   = os.environ.get("CHATWORK_ROOM_ID", "")

FIREBASE_API_KEY = "AIzaSyACvJBtwGAk7ZHKzNhWiRY4_g-q0oF8VGQ"
SCRIPT_DIR   = Path(__file__).parent
TARGETS_FILE = SCRIPT_DIR / "watch_targets.json"
SEEN_FILE    = SCRIPT_DIR / "seen_ads.json"
BUZZ_FILE    = SCRIPT_DIR / "buzz_notified.json"
DPRO_API_URL = "https://api.kashika-20mile.com/api/v1/items"
BUZZ_TOP_PCT = 0.20

GOG_ACCOUNT  = "yotayamaguchi2@gmail.com"
SHEETS_ID    = "1_d-H-6C_deAbtDySeC6ywbqQ_tFRZyR5EXR79mwnShE"
REWRITE_SHEETS_URL = "https://docs.google.com/spreadsheets/d/1Rw4Ywsk7LIwmDEhtPdmNwoMW_qID9doH45URV7Klttk"


def ensure_monthly_sheet():
    """当月シート（例: 2026_04）がなければ作成し、ヘッダーを書き込む"""
    sheet_name = datetime.now().strftime("%Y_%m")
    result = subprocess.run(
        ["gog", "sheets", "metadata", SHEETS_ID, "-a", GOG_ACCOUNT, "-j"],
        capture_output=True, text=True)
    existing_tabs = []
    if result.returncode == 0:
        meta = json.loads(result.stdout)
        for s in meta.get("sheets", []):
            existing_tabs.append(s.get("properties", {}).get("title", ""))
    if sheet_name not in existing_tabs:
        print(f"📊 シート「{sheet_name}」を新規作成中...")
        subprocess.run(
            ["gog", "sheets", "add-tab", SHEETS_ID, sheet_name, "-a", GOG_ACCOUNT],
            capture_output=True, text=True)
        # ヘッダー行を追加
        header = json.dumps([["会社名", "文字起こしテキスト", "URL", "再生数", "日にち"]])
        subprocess.run(
            ["gog", "sheets", "append", SHEETS_ID, f"{sheet_name}!A:E",
             "--values-json", header, "-a", GOG_ACCOUNT],
            capture_output=True, text=True)
        print(f"✅ シート「{sheet_name}」作成完了")
    return sheet_name


def get_existing_urls(sheet_name):
    """当月シートから既存URLを取得して重複チェック用のsetを返す"""
    result = subprocess.run(
        ["gog", "sheets", "get", SHEETS_ID, f"{sheet_name}!C:C",
         "-a", GOG_ACCOUNT, "-j"],
        capture_output=True, text=True)
    urls = set()
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            for row in data.get("values", [])[1:]:  # ヘッダー行をスキップ
                if row and row[0]:
                    urls.add(row[0])
        except (json.JSONDecodeError, IndexError):
            pass
    return urls


# みかみ関連のラベルを統一するマッピング
MIKAMI_LABELS = {"みかみ", "アドネス", "スキルプラス"}


def normalize_label(label):
    """みかみ関連のラベルは全て「みかみ」に統一"""
    return "みかみ" if label in MIKAMI_LABELS else label


def append_to_sheets(ads, sheet_name):
    """広告データをGoogle Sheetsに追記する（URL重複チェック付き）"""
    if not ads:
        return
    existing_urls = get_existing_urls(sheet_name)
    today = datetime.now().strftime("%Y/%m/%d")
    rows = []
    for ad in ads:
        url = ad.get("production_share_url") or ad.get("production_url") or ""
        if url in existing_urls:
            continue
        company = normalize_label(ad.get("label", ""))
        transcript = ad.get("ad_all_sentence") or ""
        play_count = str(pc(ad.get("play_count", 0)))
        rows.append([company, transcript, url, play_count, today])
    if not rows:
        print("📊 Google Sheets: 新規追記なし（全て既存）")
        return
    values_json = json.dumps(rows, ensure_ascii=False)
    result = subprocess.run(
        ["gog", "sheets", "append", SHEETS_ID, f"{sheet_name}!A:E",
         "--values-json", values_json, "-a", GOG_ACCOUNT],
        capture_output=True, text=True)
    if result.returncode == 0:
        print(f"📊 Google Sheets追記完了（{len(rows)}件）")
    else:
        print(f"❌ Google Sheets追記失敗: {result.stderr}")


def get_token():
    print("🔑 ログイン中...")
    resp = requests.post(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
        params={"key": FIREBASE_API_KEY},
        json={"email": DPRO_EMAIL, "password": DPRO_PASSWORD, "returnSecureToken": True},
        timeout=15)
    print("✅ ログイン成功！")
    return resp.json().get("idToken", "")


def load_json(path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_by_keyword(token, search_kw, adv_names, interval=30, prod_names=None):
    """search_kwで検索し、advertiser_names or product_namesでフィルタ"""
    today = datetime.now().strftime("%Y-%m-%d")
    all_items = []
    for page in range(1, 6):
        resp = None
        for attempt in range(3):
            try:
                resp = requests.get(DPRO_API_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    params={"to_date": today, "interval": interval, "media_type": "video",
                            "version": 2, "keyword_logic": "or",
                            "keyword": f"in:{search_kw}", "page": page},
                    timeout=120)
                break
            except requests.exceptions.ReadTimeout:
                if attempt < 2:
                    print(f"    ⏳ タイムアウト、リトライ中... ({attempt+1}/3)")
                    import time; time.sleep(5)
                else:
                    print(f"    ❌ 3回タイムアウト、スキップ")
                    return all_items
        if resp is None:
            return all_items
        try:
            items = resp.json().get("items", [])
        except (requests.exceptions.JSONDecodeError, ValueError):
            print(f"    ⚠️ 空レスポンス、スキップ (status={resp.status_code})")
            break
        if not items:
            break
        # advertiser_name or product_name(部分一致)でフィルタ
        matched = []
        for i in items:
            if i.get("advertiser_name") in adv_names:
                matched.append(i)
            elif prod_names:
                pname = (i.get("product_name") or "")
                if any(pn in pname for pn in prod_names):
                    matched.append(i)
        all_items.extend(matched)
        if not matched:
            break
    return all_items


def pc(v):
    if not v:
        return 0
    return int(str(v).replace(",", ""))


def percentile(values, pct):
    if not values:
        return 0
    s = sorted(values)
    return s[min(int(len(s) * pct), len(s) - 1)]


def check_buzz(items):
    """ターゲット内で中央値の3倍以上伸びている動画を返す"""
    diffs = [pc(i.get("play_count_difference")) for i in items if pc(i.get("play_count_difference")) > 0]
    if len(diffs) < 3:
        return []
    median = sorted(diffs)[len(diffs) // 2]
    threshold = median * 3
    if threshold < 1000:
        threshold = 1000
    candidates = [i for i in items if pc(i.get("play_count_difference")) >= threshold]
    return sorted(candidates, key=lambda i: pc(i.get("play_count_difference")), reverse=True)


def build_new(label, ads):
    today = datetime.now().strftime("%Y/%m/%d %H:%M")
    lines = [f"[info][title]{label}｜新着広告通知（{len(ads)}件）｜{today}[/title]"]
    for ad in ads:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"{ad.get('product_name') or ad.get('advertiser_name')}")
        lines.append(f"投稿日: {ad.get('creation_time', '')}")
        lines.append(f"再生数: {pc(ad.get('play_count')):,}")
        lines.append(f"{ad.get('production_share_url') or ad.get('production_url', '')}")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"→ {REWRITE_SHEETS_URL}")
    lines.append("[/info]")
    return "\n".join(lines)


def build_buzz(ads):
    today = datetime.now().strftime("%Y/%m/%d %H:%M")
    lines = [f"[info][title]最近伸びている動画｜{today}[/title]"]
    lines.append("過去の動画の中で最近再生数が伸びています！")
    lines.append("")
    for ad in ads:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append(f"{ad['label']}｜{ad.get('product_name') or ad.get('advertiser_name')}")
        lines.append(f"再生数: {pc(ad.get('play_count')):,}（直近+{pc(ad.get('play_count_difference')):,}）")
        lines.append(f"{ad.get('production_share_url') or ad.get('production_url', '')}")
    lines += ["━━━━━━━━━━━━━━━"]
    lines.append("スプシに追記しました")
    lines.append(f"→ {REWRITE_SHEETS_URL}")
    lines.append("[/info]")
    return "\n".join(lines)


def send_cw(message):
    if DRY_RUN:
        print("── [DRY-RUN] 送信メッセージ ──")
        print(message)
        print("── [DRY-RUN] ここまで ──\n")
        return
    resp = requests.post(f"https://api.chatwork.com/v2/rooms/{CHATWORK_ROOM_ID}/messages",
        headers={"X-ChatWorkToken": CHATWORK_API_TOKEN},
        data={"body": message, "self_unread": 0}, timeout=15)
    print("✅ ChatWork送信成功" if resp.status_code == 200 else f"❌ 失敗: {resp.status_code}")


def main():
    token         = get_token()
    targets       = load_json(TARGETS_FILE, [])
    seen          = load_json(SEEN_FILE, {})
    buzz_notified = load_json(BUZZ_FILE, {})
    new_ads, buzz_ads, all_fetched = [], [], []
    notified_urls = set()

    for target in targets:
        label        = target["label"]
        search_kws   = target["search_keywords"]
        adv_names    = set(target["advertiser_names"])
        prod_names   = target.get("product_names")
        interval     = target.get("interval", 30)

        seen.setdefault(label, [])
        buzz_notified.setdefault(label, [])

        target_items, seen_urls = [], set()
        for kw in search_kws:
            print(f"  🔍 [{label}] 「{kw}」で検索中...")
            for item in fetch_by_keyword(token, kw, adv_names, interval, prod_names):
                url = item.get("production_url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    target_items.append(item)

        print(f"  [{label}] {len(target_items)}件")

        # 全取得アイテムにラベルを付与し、直近7日以内のものをリライト候補に蓄積
        for item in target_items:
            item["label"] = label
            if not (item.get("ad_all_sentence") or "").strip():
                continue
            pub = item.get("creation_time", "")
            if pub:
                try:
                    pub_dt = datetime.strptime(pub[:10], "%Y-%m-%d")
                    if (datetime.now() - pub_dt).days > 7:
                        continue
                except ValueError:
                    pass
            all_fetched.append(item)

        for item in target_items:
            url = item.get("production_url", "")
            if not url or url in seen[label] or url in notified_urls:
                continue
            # public_dateが直近30日以内のものだけ新着通知
            pub = item.get("creation_time", "")
            if pub:
                try:
                    pub_dt = datetime.strptime(pub[:10], "%Y-%m-%d")
                    if (datetime.now() - pub_dt).days > 30:
                        continue
                except ValueError:
                    pass
            new_ads.append(item)
            seen[label].append(url)
            notified_urls.add(url)

        for item in check_buzz(target_items):
            url = item.get("production_url", "")
            if not url or url in buzz_notified[label]:
                continue
            buzz_ads.append(item)

    # バズり候補を全体でplay_count_differenceの上位5件に絞る
    buzz_ads = sorted(buzz_ads, key=lambda i: pc(i.get("play_count_difference")), reverse=True)[:5]
    for ad in buzz_ads:
        buzz_notified[ad["label"]].append(ad.get("production_url", ""))

    save_json(SEEN_FILE, seen)
    save_json(BUZZ_FILE, buzz_notified)

    # Google Sheets: 月別シートを確保
    sheet_name = ensure_monthly_sheet()

    if new_ads:
        from collections import defaultdict
        by_label = defaultdict(list)
        for ad in new_ads:
            by_label[ad["label"]].append(ad)
        for label, ads in by_label.items():
            print(f"\n🎉 [{label}] 新着 {len(ads)}件")
            send_cw(build_new(label, ads))
        # Google Sheets「新着」に追記
        append_to_sheets(new_ads, sheet_name)
    else:
        print("\n✅ 新着なし")

    if buzz_ads:
        print(f"\n📈 伸びてる動画 {len(buzz_ads)}件")
        try:
            from buzz_pipeline import process_buzz_ads
            process_buzz_ads(buzz_ads, dry_run=DRY_RUN)
        except Exception as e:
            print(f"❌ buzz_pipeline error: {e}")
        send_cw(build_buzz(buzz_ads))

    # Google Sheets「リライト」: 取得した全広告をパイプライン処理
    # URL重複チェックで既にリライト済みのものは自動スキップ
    if all_fetched:
        print(f"\n📝 リライトパイプライン: {len(all_fetched)}件を処理")
        try:
            from buzz_pipeline import process_new_ads
            process_new_ads(all_fetched, dry_run=DRY_RUN)
        except Exception as e:
            print(f"❌ rewrite pipeline error: {e}")
    else:
        print("\n✅ 伸びてる動画なし")


if __name__ == "__main__":
    main()
