#!/usr/bin/env bash
# テストを流し、通ったときだけ終了コード0を返す。
#
# `pytest | tail` のようにパイプで繋ぐと、終了コードは最後のコマンドのものになり、
# テストが落ちていても成功として扱われる。実際にそれで、失敗したまま push した。
# 以後はこのスクリプトを通す。
set -euo pipefail

cd "$(dirname "$0")/.."

# ローカルPostgres が落ちていれば起こす（DBを使うテストがあるため）
if [ -n "${HANSOKU_TEST_DATABASE_URL:-}" ] && command -v pg_isready >/dev/null 2>&1; then
    if ! pg_isready -h 127.0.0.1 -p 5433 >/dev/null 2>&1; then
        echo "ローカルPostgres を起動します..."
        su postgres -c \
          "PATH=/usr/lib/postgresql/16/bin:\$PATH pg_ctl -D /tmp/pgdata -o '-p 5433 -k /tmp' -l /tmp/pg.log start" \
          >/dev/null 2>&1 || true
        for _ in 1 2 3 4 5; do
            pg_isready -h 127.0.0.1 -p 5433 >/dev/null 2>&1 && break
            sleep 2
        done
    fi
fi

python3 -m pytest tests/ -q

# 台帳（唯一の人力入力）の書き方。間違えると黙って消えるので、ここで止める。
python3 -m hansoku.cli schedule-lint

# 画面側の計算（前年比・効果判定・目標達成率）。ブラウザ無しで動かす。
node --check web/app.js
node tests/web/app_test.mjs

# 入口（合言葉ログイン）。売上の実データが載る画面なので、開けっ放しに
# なっていないことを推測ではなくテストで押さえる。
node tests/web/worker_test.mjs

# URL と画面の対応（実ブラウザ）。戻るボタン・リンク共有・「うちの店」。
# chromium が無い環境では中で自動的に飛ばす。
python3 tests/web/route_check.py
