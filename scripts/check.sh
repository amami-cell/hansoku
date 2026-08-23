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
