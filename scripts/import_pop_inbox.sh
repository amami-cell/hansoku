#!/usr/bin/env bash
# config/creatives_inbox.tsv に並ぶ Drive 公開POPを、
# ランナー（自由なネット）から落として Worker の /api/creatives へ流し込む。
# Worker 側が R2 保存＋promo_creatives 追記まで一括でやる（アプリ内アップロードと同じ経路）。
#
# 何度流しても増えないように、既にある (施策id|タイトル) はスキップする。
set -euo pipefail

WORKER="${WORKER_BASE_URL:?WORKER_BASE_URL is required}"
INBOX="${INBOX_TSV:-config/creatives_inbox.tsv}"
STORE="${STORE_CODE:-1160}"

echo "== 取り込み先: $WORKER =="
echo "== 台帳: $INBOX =="

# 既存の (campaign_id|title) を集める（重複投入を防ぐ）
existing="$(mktemp)"
curl -fsS "$WORKER/api/creatives" \
  | python3 -c 'import sys,json
d=json.load(sys.stdin)
for c in d.get("creatives",[]):
    print("%s\t%s" % (c.get("campaign_id",""), c.get("title","")))' > "$existing" || true
echo "既存の制作物: $(wc -l < "$existing") 件"

put=0; skip=0; fail=0
# ヘッダを飛ばして読む。列: 店 販促 制作物 期間 driveId filename campaign_id
tail -n +2 "$INBOX" | while IFS=$'\t' read -r shop bucket title period drive_id filename campaign_id; do
  [ -z "${drive_id:-}" ] && continue
  [ -z "${campaign_id:-}" ] && { echo "SKIP(施策id無し): $title"; continue; }

  start="${period%%〜*}"        # 期間の開始を掲出日に
  key="$(printf '%s\t%s' "$campaign_id" "$title")"
  if grep -qxF "$key" "$existing"; then
    echo "SKIP(既存): [$campaign_id] $title"
    skip=$((skip+1)); continue
  fi

  tmp="$(mktemp).pdf"
  # 公開ファイルの直ダウンロード（ウイルススキャン確認ページを confirm=t で回避）
  curl -fsSL "https://drive.usercontent.google.com/download?id=${drive_id}&export=download&confirm=t" -o "$tmp" || {
    echo "FAIL(DL): [$campaign_id] $title ($drive_id)"; fail=$((fail+1)); rm -f "$tmp"; continue; }

  # 本当にPDFか（HTMLの確認ページが返っていないか）を頭4バイトで確認
  magic="$(head -c4 "$tmp" | tr -d '\0')"
  if [ "$magic" != "%PDF" ]; then
    echo "FAIL(非PDF: '$magic'): [$campaign_id] $title — Drive公開設定を確認"; fail=$((fail+1)); rm -f "$tmp"; continue
  fi
  size="$(wc -c < "$tmp")"

  resp="$(curl -sS -X POST "$WORKER/api/creatives" \
    -F "file=@${tmp};type=application/pdf;filename=${filename}" \
    -F "campaign=${campaign_id}" \
    -F "store=${STORE}" \
    -F "title=${title}" \
    -F "kind=dev" \
    -F "date=${start}")" || resp='{"error":"curl-failed"}'
  rm -f "$tmp"

  if printf '%s' "$resp" | grep -q '"ok":true'; then
    echo "PUT : [$campaign_id] $title (${size} bytes)"
    put=$((put+1))
  else
    echo "FAIL(POST): [$campaign_id] $title -> $resp"; fail=$((fail+1))
  fi
done

# サブシェル(while|)の集計は表示用に再取得
echo "== 完了。最終状態を確認 =="
curl -fsS "$WORKER/api/creatives" \
  | python3 -c 'import sys,json
d=json.load(sys.stdin)
cs=[c for c in d.get("creatives",[]) if c.get("uploaded")]
print("アップロード済み制作物: %d 件" % len(cs))
for c in sorted(cs, key=lambda x:(x.get("campaign_id",""),x.get("title",""))):
    print("  [%s] %s (%s)" % (c.get("campaign_id",""), c.get("title",""), c.get("mime","")))'
