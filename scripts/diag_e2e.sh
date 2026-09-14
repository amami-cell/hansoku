#!/usr/bin/env bash
# 一時診断：公開プレビューに対して login(8888) → /setpw(GET) → /setpw(POST) を実際に叩き、
# どのステップで server-error になるか・その detail を出す。済んだら削除する。
set -uo pipefail
URL="https://hansoku-preview.amami-c1e.workers.dev"
NAME="診断E2E"
CJ=/tmp/cj.txt; : > "$CJ"

echo "===== 1) POST /login （参加コードで設定へ誘導される想定）====="
curl -sS -c "$CJ" -b "$CJ" -D /tmp/h1 -o /tmp/b1 \
  --data-urlencode "name=$NAME" --data-urlencode "password=$APP_PASSWORD" \
  "$URL/login" -w "http=%{http_code}\n"
echo "-- 応答ヘッダ(先頭) --"; sed -n '1,12p' /tmp/h1
echo "-- pending cookie --"; grep -i "hansoku_setpw" "$CJ" | sed -E 's/(hansoku_setpw\t)[^\t]+/\1<token>/' || echo "(なし)"

echo; echo "===== 2) GET /setpw （設定フォームが出る想定）====="
curl -sS -b "$CJ" -D /tmp/h2 -o /tmp/b2 "$URL/setpw" -w "http=%{http_code}\n"
echo "-- ヘッダ(先頭) --"; sed -n '1,8p' /tmp/h2
echo "-- body 先頭400 --"; head -c 400 /tmp/b2; echo

echo; echo "===== 3) POST /setpw （保存→/loginへ303の想定）====="
curl -sS -b "$CJ" -c "$CJ" -D /tmp/h3 -o /tmp/b3 \
  --data-urlencode "oldpw=$APP_PASSWORD" \
  --data-urlencode "newpw=diagpw123" --data-urlencode "newpw2=diagpw123" \
  "$URL/setpw" -w "http=%{http_code}\n"
echo "-- ヘッダ(先頭) --"; sed -n '1,10p' /tmp/h3
echo "-- body 先頭600 --"; head -c 600 /tmp/b3; echo

echo; echo "===== 4) 診断ユーザーを掃除 ====="
NEON_DATABASE_URL="$NEON_DATABASE_URL" node -e '
import("@neondatabase/serverless").then(async ({neon})=>{
  const sql=neon(process.env.NEON_DATABASE_URL);
  await sql`DELETE FROM app_users WHERE name = ${"診断E2E"}`;
  console.log("削除 ok");
}).catch(e=>console.log("削除失敗:", String(e&&e.message||e).replace(/postgres(ql)?:\/\/[^ ]+/gi,"REDACTED")));
'
