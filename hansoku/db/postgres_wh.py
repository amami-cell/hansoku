"""
PostgreSQL（Neon）版の Warehouse。

当面の本番実装。BigQuery のサンドボックス制限（DML不可・60日でデータ削除）を
避けつつ、前年同期比に必要な複数年分の履歴を保持できる。

挿入は COPY を使う。1行ずつの INSERT では、時間帯別実績（1時間粒度）が
入ってきたときに現実的な時間で終わらなくなるため。
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Iterable

from ..model import ActualRow
from ..settings import AppDbSettings
from .warehouse import COLUMNS, Warehouse, render_params

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "sql" / "neon"


class PostgresWarehouse(Warehouse):
    dialect = "postgres"

    def __init__(self, settings: AppDbSettings):
        self._dsn = settings.dsn
        self._conn = None

    # ── 接続 ──────────────────────────────────────────────────────────────
    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            import psycopg

            self._conn = psycopg.connect(self._dsn, autocommit=False)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def table_name(self, name: str) -> str:
        return name

    # ── スキーマ ──────────────────────────────────────────────────────────
    def ensure_schema(self) -> None:
        path = MIGRATIONS_DIR / "002_f_actuals.sql"
        with self.conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
        self.conn.commit()

    # ── 問い合わせ ────────────────────────────────────────────────────────
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rendered = render_params(sql, self.dialect)
        with self.conn.cursor() as cur:
            cur.execute(rendered, params or {})
            if cur.description is None:
                return []
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    # ── 取り込み ──────────────────────────────────────────────────────────
    def replace_actuals(self, rows: Iterable[ActualRow]) -> int:
        materialized = [r.with_ingested_at() if r.ingested_at is None else r for r in rows]
        if not materialized:
            return 0

        # 冪等性: この取り込みが覆う (source, grain, date) を先に消してから入れ直す。
        scopes = sorted({(r.source, r.grain, r.date) for r in materialized})

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row in materialized:
            writer.writerow(
                ["" if (value := getattr(row, column)) is None else value for column in COLUMNS]
            )
        buffer.seek(0)

        with self.conn.cursor() as cur:
            cur.executemany(
                "DELETE FROM f_actuals WHERE source = %s AND grain = %s AND date = %s",
                [list(scope) for scope in scopes],
            )
            # NULL は空欄で表す。NOT NULL の列（store_code / grain / metric / source）は
            # 取り込み時点で必ず値が入っているため、空文字と NULL を取り違える余地はない。
            with cur.copy(
                f"COPY f_actuals ({', '.join(COLUMNS)}) "
                f"FROM STDIN WITH (FORMAT csv, NULL '')"
            ) as copy:
                copy.write(buffer.getvalue())
        self.conn.commit()
        return len(materialized)
