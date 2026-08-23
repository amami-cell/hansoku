"""
App DB（施策・目標・マスタ・集計結果）。

Neon はサーバーレス PostgreSQL なので、本番も検証も同じ psycopg で扱える。
接続先が Neon かローカルPostgres かは接続文字列の違いだけ。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from ..settings import AppDbSettings
from ..stores import Store

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "sql" / "neon"


class AppDb:
    """施策・マスタ側の読み書き口。"""

    def __init__(self, settings: AppDbSettings):
        self._dsn = settings.dsn
        self._conn = None

    # ── 接続 ──────────────────────────────────────────────────────────────
    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            import psycopg

            # Neon は自動スリープするため、接続失敗時は呼び出し側でリトライする前提。
            self._conn = psycopg.connect(self._dsn, autocommit=False)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def __enter__(self) -> "AppDb":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── 汎用 ──────────────────────────────────────────────────────────────
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
        self.conn.commit()

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    # ── スキーマ ──────────────────────────────────────────────────────────
    def ensure_schema(self) -> None:
        """sql/neon 配下のマイグレーションを番号順に流す（全て冪等なDDL）。"""
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            with self.conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
            self.conn.commit()

    # ── 店舗マスタ ────────────────────────────────────────────────────────
    def sync_stores(self, stores: Iterable[Store]) -> int:
        """config/stores.yaml の内容を m_stores へ反映する（追加・更新）。"""
        rows = list(stores)
        if not rows:
            return 0
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO m_stores (store_code, store_name, source_name, infomart_code,
                                      region, brand, brand_name, file_prefix,
                                      is_shared_facility, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (store_code) DO UPDATE SET
                    store_name         = EXCLUDED.store_name,
                    source_name        = EXCLUDED.source_name,
                    infomart_code      = EXCLUDED.infomart_code,
                    region             = EXCLUDED.region,
                    brand              = EXCLUDED.brand,
                    brand_name         = EXCLUDED.brand_name,
                    file_prefix        = EXCLUDED.file_prefix,
                    is_shared_facility = EXCLUDED.is_shared_facility,
                    active             = EXCLUDED.active,
                    updated_at         = now()
                """,
                [
                    (
                        s.store_code,
                        s.store_name,
                        s.source_name,
                        s.infomart_code,
                        s.region,
                        s.brand,
                        s.brand_name,
                        s.file_prefix,
                        s.is_shared_facility,
                        s.active,
                    )
                    for s in rows
                ],
            )
        self.conn.commit()
        return len(rows)

    # ── 権限（§2-2）────────────────────────────────────────────────────────
    def stores_for(self, email: str) -> list[str]:
        """
        ログインユーザーが見られる店舗コードを返す。

        admin は全 active 店。manager は自分の email に紐づく店舗のみ。
        該当が無ければ空リスト（＝何も見せない）。
        """
        rows = self.query(
            "SELECT role, store_code FROM m_access WHERE lower(email) = lower(%s)",
            (email,),
        )
        if not rows:
            return []
        if any(r["role"] == "admin" for r in rows):
            return [
                r["store_code"]
                for r in self.query(
                    "SELECT store_code FROM m_stores WHERE active ORDER BY store_code"
                )
            ]
        return sorted({r["store_code"] for r in rows})

    def grant(self, email: str, store_code: str, role: str) -> None:
        self.execute(
            """
            INSERT INTO m_access (email, store_code, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (email, store_code) DO UPDATE SET role = EXCLUDED.role
            """,
            (email, store_code, role),
        )

    def grant_admin(self, email: str) -> int:
        """admin を全 active 店に対して登録する（初期は天のみ）。"""
        codes = [
            r["store_code"]
            for r in self.query("SELECT store_code FROM m_stores WHERE active")
        ]
        for code in codes:
            self.grant(email, code, "admin")
        return len(codes)
