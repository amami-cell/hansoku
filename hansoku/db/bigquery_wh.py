"""
BigQuery 実装（本番）。

  * f_actuals は date パーティション。取り込みは「この取り込みが覆う
    (source, grain, date) を DELETE してから INSERT」で冪等にする。
    日次追記が中心なので UPDATE の苦手さは問題にならない。
  * 認証はサービスアカウント。GitHub Secrets から JSON の中身、
    またはファイルパスのどちらでも渡せる。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..model import ActualRow
from ..settings import WarehouseSettings
from .warehouse import COLUMNS, Warehouse, render_params

DDL_PATH = Path(__file__).resolve().parent.parent.parent / "sql" / "bigquery" / "001_f_actuals.sql"


def _credentials(raw: str):
    """サービスアカウント鍵を JSON文字列 / ファイルパスのどちらでも受ける。"""
    from google.oauth2 import service_account

    text = raw.strip()
    if text.startswith("{"):
        return service_account.Credentials.from_service_account_info(json.loads(text))
    return service_account.Credentials.from_service_account_file(text)


class BigQueryWarehouse(Warehouse):
    dialect = "bigquery"

    def __init__(self, settings: WarehouseSettings):
        from google.cloud import bigquery

        self._bq = bigquery
        self._settings = settings
        self._client = bigquery.Client(
            project=settings.project,
            credentials=_credentials(settings.service_account_json),
        )

    def table_name(self, name: str) -> str:
        return f"`{self._settings.project}.{self._settings.dataset}.{name}`"

    # ── スキーマ ──────────────────────────────────────────────────────────
    def ensure_schema(self) -> None:
        ddl = DDL_PATH.read_text(encoding="utf-8")
        ddl = ddl.replace("{project}", self._settings.project).replace(
            "{dataset}", self._settings.dataset
        )
        for chunk in ddl.split(";"):
            # 先頭のコメント行を落としてから中身の有無を見る。
            # チャンク全体が "--" で始まるかで判定すると、ヘッダーコメントに
            # 続く CREATE SCHEMA まで一緒に読み飛ばしてしまう。
            statement = "\n".join(
                line for line in chunk.splitlines() if not line.strip().startswith("--")
            ).strip()
            if statement:
                self._client.query(statement).result()

    # ── 問い合わせ ────────────────────────────────────────────────────────
    def _parameter(self, name: str, value: Any):
        bq = self._bq
        if isinstance(value, list):
            element = self._scalar_type(value[0]) if value else "STRING"
            return bq.ArrayQueryParameter(name, element, value)
        return bq.ScalarQueryParameter(name, self._scalar_type(value), value)

    @staticmethod
    def _scalar_type(value: Any) -> str:
        import datetime as _dt

        if isinstance(value, bool):
            return "BOOL"
        if isinstance(value, int):
            return "INT64"
        if isinstance(value, float):
            return "FLOAT64"
        if isinstance(value, _dt.datetime):
            return "TIMESTAMP"
        if isinstance(value, _dt.date):
            return "DATE"
        return "STRING"

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rendered = render_params(sql, self.dialect)
        config = self._bq.QueryJobConfig(
            query_parameters=[self._parameter(k, v) for k, v in (params or {}).items()]
        )
        return [dict(row) for row in self._client.query(rendered, job_config=config).result()]

    # ── 取り込み ──────────────────────────────────────────────────────────
    def replace_actuals(self, rows: Iterable[ActualRow], *, scope_stores: bool = False) -> int:
        materialized = [r.with_ingested_at() if r.ingested_at is None else r for r in rows]
        if not materialized:
            return 0

        table = self.table_name("f_actuals")

        # 1) この取り込みが覆う (source, grain, date) を消す（パーティション単位の入れ替え）。
        # scope_stores なら店も範囲に含め、流していない店の実績には触れない。
        scopes: dict[tuple[str, str, str | None], set] = {}
        for row in materialized:
            key = (row.source, row.grain, row.store_code if scope_stores else None)
            scopes.setdefault(key, set()).add(row.date)
        for (source, grain, store_code), dates in scopes.items():
            store_sql = " AND store_code = :store_code" if store_code is not None else ""
            params = {"source": source, "grain": grain, "dates": sorted(dates)}
            if store_code is not None:
                params["store_code"] = store_code
            self.query(
                f"""
                DELETE FROM {table}
                WHERE source = :source AND grain = :grain AND date IN UNNEST(:dates){store_sql}
                """,
                params,
            )

        # 2) 入れ直す。
        #    ストリーミング挿入（insert_rows_json）はバッファ上の行を直後に DELETE できず、
        #    冪等な再取り込みと相性が悪いため、バッチロードを使う。
        payload = [
            {
                column: (
                    value.isoformat() if hasattr(value, "isoformat") else value
                )
                for column, value in ((c, getattr(row, c)) for c in COLUMNS)
            }
            for row in materialized
        ]
        job = self._client.load_table_from_json(
            payload,
            f"{self._settings.project}.{self._settings.dataset}.f_actuals",
            job_config=self._bq.LoadJobConfig(
                write_disposition=self._bq.WriteDisposition.WRITE_APPEND,
                schema_update_options=[],
            ),
        )
        job.result()
        if job.errors:
            raise RuntimeError(f"BigQuery への読み込みに失敗しました: {job.errors}")
        return len(materialized)
