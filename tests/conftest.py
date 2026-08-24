"""テスト共通の準備。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "fw_sheet_sample.json"


@pytest.fixture(scope="session", autouse=True)
def _check_test_database():
    """
    テストDBが指定されているのに繋がらない場合、最初に一度だけ落とす。

    そのまま走らせると、DB を使う全テストが同じ接続エラーを出して
    数十件のエラーに埋もれ、原因が読み取りにくくなる。
    未指定のときは各フィクスチャが個別にスキップするので、ここでは何もしない。
    """
    dsn = os.environ.get("HANSOKU_TEST_DATABASE_URL")
    if not dsn:
        return
    import psycopg

    try:
        psycopg.connect(dsn, connect_timeout=5).close()
    except Exception as exc:
        pytest.exit(
            f"HANSOKU_TEST_DATABASE_URL に接続できません: {exc}\n"
            f"  DSN: {dsn}\n"
            f"  PostgreSQL が起動しているか確認してください。"
            f" DBを使わないテストだけ流すなら、この環境変数を外してください。",
            returncode=1,
        )


@pytest.fixture(scope="session")
def master():
    from hansoku.stores import StoreMaster

    return StoreMaster.load()


@pytest.fixture
def reader():
    from hansoku.ingest.sheets_client import FixtureSheetReader

    return FixtureSheetReader.from_file(FIXTURE)


@pytest.fixture(params=["duckdb", "postgres"])
def warehouse(request):
    """
    Warehouse の実装を切り替えながら同じテストを流す。

    本番は PostgreSQL（Neon）だが、認証情報が無い環境でも検証できるよう
    DuckDB 実装も同じインターフェースで揃えてある。両方に同じテストを当てて、
    片方だけで通る実装にならないようにする。
    """
    if request.param == "duckdb":
        from hansoku.db.duckdb_wh import DuckDBWarehouse

        wh = DuckDBWarehouse()
    else:
        dsn = os.environ.get("HANSOKU_TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("HANSOKU_TEST_DATABASE_URL が未設定のためスキップ")

        from hansoku.db.postgres_wh import PostgresWarehouse
        from hansoku.settings import AppDbSettings

        wh = PostgresWarehouse(AppDbSettings(env="local", dsn=dsn))
        wh.ensure_schema()
        wh.query("DELETE FROM f_actuals")
        wh.conn.commit()

    wh.ensure_schema()
    yield wh
    wh.close()


@pytest.fixture
def loaded(warehouse, reader, master):
    """フィクスチャを取り込み済みの Warehouse。"""
    from hansoku.ingest.fw_sheet import ingest

    ingest(reader, master, warehouse, strict=False)
    return warehouse


@pytest.fixture
def appdb():
    """
    実PostgreSQL に繋がる AppDb。

    HANSOKU_TEST_DATABASE_URL が無い環境ではスキップする（Neon スキーマの
    検証は実DBでしか意味がないため、SQLite等での代用はしない）。
    """
    dsn = os.environ.get("HANSOKU_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("HANSOKU_TEST_DATABASE_URL が未設定のためスキップ")

    from hansoku.db.appdb import AppDb
    from hansoku.settings import AppDbSettings

    db = AppDb(AppDbSettings(env="local", dsn=dsn))
    db.execute(
        """
        DROP TABLE IF EXISTS promo_notes, promo_targets, f_campaign_summary, f_daily,
             m_reviews, m_creatives, m_share_targets, m_goals, m_campaigns, m_timeslots,
             m_access, m_stores CASCADE
        """
    )
    db.ensure_schema()
    yield db
    db.close()
