"""
DB接続の抽象化。

呼び出し側は ``get_warehouse()`` / ``get_appdb()`` / ``get_object_store()`` だけを見る。
HANSOKU_ENV=cloud なら BigQuery / Neon / R2、それ以外なら
DuckDB / ローカルPostgres / ローカルFS に繋がる。
認証情報は全て環境変数（GitHub Secrets）経由で、コードには一切持たせない。
"""
from __future__ import annotations

from ..settings import Settings, load_settings
from .appdb import AppDb
from .objectstore import LocalObjectStore, ObjectStore, R2ObjectStore, creative_key
from .warehouse import AggregateQuery, Warehouse

__all__ = [
    "AppDb",
    "AggregateQuery",
    "ObjectStore",
    "Warehouse",
    "creative_key",
    "get_appdb",
    "get_object_store",
    "get_warehouse",
]


def get_warehouse(settings: Settings | None = None) -> Warehouse:
    """WAREHOUSE_BACKEND（既定: cloud なら postgres、ローカルなら duckdb）で実装を選ぶ。"""
    resolved = settings or load_settings()
    backend = resolved.warehouse.backend

    if backend == "postgres":
        from .postgres_wh import PostgresWarehouse

        return PostgresWarehouse(resolved.appdb)
    if backend == "bigquery":
        from .bigquery_wh import BigQueryWarehouse

        return BigQueryWarehouse(resolved.warehouse)
    from .duckdb_wh import DuckDBWarehouse

    return DuckDBWarehouse(resolved.warehouse.local_path)


def get_appdb(settings: Settings | None = None) -> AppDb:
    return AppDb((settings or load_settings()).appdb)


def get_object_store(settings: Settings | None = None) -> ObjectStore:
    resolved = settings or load_settings()
    if resolved.env == "cloud":
        return R2ObjectStore(resolved.objects)
    return LocalObjectStore(resolved.objects.local_path)
