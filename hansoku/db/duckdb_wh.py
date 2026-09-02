"""
DuckDB 実装（ローカル開発・テスト用）。

BigQuery のアカウントが無くても、同じSQLで f_actuals の取り込みと集計を
検証できるようにするためのもの。本番と同じ ``Warehouse`` を実装する。
"""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Any, Iterable

import duckdb

from ..model import ActualRow
from .warehouse import COLUMNS, Warehouse, render_params

DDL = """
CREATE TABLE IF NOT EXISTS f_actuals (
    store_code       VARCHAR   NOT NULL,
    date             DATE      NOT NULL,
    grain            VARCHAR   NOT NULL,
    hour             INTEGER,
    metric           VARCHAR   NOT NULL,
    value            DOUBLE    NOT NULL,
    product_name     VARCHAR,
    product_category VARCHAR,
    kind             VARCHAR,
    source           VARCHAR   NOT NULL,
    ingested_at      TIMESTAMP NOT NULL
);
"""


class DuckDBWarehouse(Warehouse):
    dialect = "duckdb"

    def __init__(self, path: Path | str | None = None):
        # path 未指定ならインメモリ（テスト用）
        if path is None:
            self._conn = duckdb.connect()
        else:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(target))

    def table_name(self, name: str) -> str:
        return name

    def ensure_schema(self) -> None:
        self._conn.execute(DDL)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rendered = render_params(sql, self.dialect)
        cursor = self._conn.execute(rendered, params or {})
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def replace_actuals(self, rows: Iterable[ActualRow], *, scope_stores: bool = False) -> int:
        materialized = [r.with_ingested_at() if r.ingested_at is None else r for r in rows]
        if not materialized:
            return 0

        # 冪等性: この取り込みが覆う (source, grain, date) を先に消してから入れ直す。
        # scope_stores なら店も範囲に含め、流していない店の実績には触れない。
        if scope_stores:
            scopes = sorted({(r.source, r.grain, r.date, r.store_code) for r in materialized})
            delete_sql = (
                "DELETE FROM f_actuals "
                "WHERE source = ? AND grain = ? AND date = ? AND store_code = ?"
            )
        else:
            scopes = sorted({(r.source, r.grain, r.date) for r in materialized})
            delete_sql = "DELETE FROM f_actuals WHERE source = ? AND grain = ? AND date = ?"

        # 挿入は CSV 経由の一括ロード。1行ずつの INSERT だと 1,000行あたり約2秒かかり、
        # 時間帯別実績（1時間粒度）が入ったときに現実的な時間で終わらなくなる。
        # COPY なら 10万行を約1秒で取り込める。
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            for row in materialized:
                writer.writerow(
                    [
                        "" if (value := getattr(row, column)) is None else value
                        for column in COLUMNS
                    ]
                )
            csv_path = handle.name

        try:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(delete_sql, [list(scope) for scope in scopes])
                # NULLSTR '' で空欄を NULL として読む。NOT NULL の列（store_code / grain /
                # metric / source）は取り込み時点で必ず値が入っているため、
                # 空文字と NULL を取り違える余地はない。
                self._conn.execute(
                    f"COPY f_actuals ({', '.join(COLUMNS)}) FROM ? "
                    f"(FORMAT CSV, HEADER false, NULLSTR '')",
                    [csv_path],
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        finally:
            Path(csv_path).unlink(missing_ok=True)
        return len(materialized)

    def close(self) -> None:
        self._conn.close()
