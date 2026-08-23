"""
設定ローダー。

方針:
  * 認証情報はコードにもリポジトリにも置かない。全て環境変数（= GitHub Secrets）から読む。
  * 何も設定されていなければ ``local`` モードで動く。BigQuery/Neon/R2 のアカウントが
    揃う前でも、DuckDB・ローカルPostgres・ローカルFS で開発とテストが完結する。
  * ``cloud`` モードで必須値が欠けている場合は、黙って local に落ちずに明示的に落とす。
    （静かにフォールバックすると「動いているように見えて本番に書けていない」事故になる。）
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Env = Literal["local", "cloud"]

# リポジトリのルート（このファイルの1つ上）
ROOT = Path(__file__).resolve().parent.parent

# 既存FW共有シートの既定ID（infomart_automation / foodist_journal.py が書き込んでいるもの）
DEFAULT_FW_SPREADSHEET_ID = "18Fq_mpEweHOFTlF4ntmwJzDsJNSt-DOq7wQYy8E0iLc"


class SettingsError(RuntimeError):
    """必須の設定が欠けている。"""


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _require(name: str, value: str, hint: str) -> str:
    if not value:
        raise SettingsError(
            f"環境変数 {name} が未設定です（{hint}）。"
            " GitHub Secrets に登録するか、HANSOKU_ENV=local で実行してください。"
        )
    return value


def _abs(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class WarehouseSettings:
    """
    実績データ（f_actuals）の置き場。

    backend で実装を選ぶ:
      postgres … Neon。現在の本番。複数年分の履歴を保持できる
      duckdb   … ローカル検証用
      bigquery … 時間帯別・商品別実績（年150万行規模）が入ってきたときの移行先。
                 GCPプロジェクトにお支払い情報の登録が要る
                 （サンドボックスは DML 不可・60日でデータ削除のため使えない）
    """

    env: Env
    backend: str
    project: str
    dataset: str
    service_account_json: str
    local_path: Path

    def table(self, name: str) -> str:
        """完全修飾テーブル名を返す。"""
        if self.env == "cloud":
            return f"`{self.project}.{self.dataset}.{name}`"
        return name


@dataclass(frozen=True)
class AppDbSettings:
    """施策・目標・マスタ・集計結果の置き場。cloud=Neon / local=ローカルPostgres。"""

    env: Env
    dsn: str


@dataclass(frozen=True)
class ObjectStoreSettings:
    """制作物PDFの置き場。cloud=Cloudflare R2 / local=ローカルFS。"""

    env: Env
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    public_base_url: str
    local_path: Path

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


@dataclass(frozen=True)
class SourceSettings:
    """取り込み元。既存資産は読み取り専用で参照する。"""

    fw_spreadsheet_id: str
    service_account_json: str


@dataclass(frozen=True)
class Settings:
    env: Env
    warehouse: WarehouseSettings
    appdb: AppDbSettings
    objects: ObjectStoreSettings
    sources: SourceSettings


def load_settings(env: Env | None = None) -> Settings:
    """環境変数から設定を組み立てる。"""
    resolved: Env = env or ("cloud" if _env("HANSOKU_ENV") == "cloud" else "local")
    cloud = resolved == "cloud"

    sa_json = _env("GOOGLE_SERVICE_ACCOUNT_JSON")

    # 実績の置き場。既定は cloud なら Neon、ローカルなら DuckDB。
    backend = _env("WAREHOUSE_BACKEND") or ("postgres" if cloud else "duckdb")
    if backend not in {"postgres", "duckdb", "bigquery"}:
        raise SettingsError(f"WAREHOUSE_BACKEND が不正です: {backend!r}")
    needs_bigquery = cloud and backend == "bigquery"

    warehouse = WarehouseSettings(
        env=resolved,
        backend=backend,
        project=(
            _require("BIGQUERY_PROJECT", _env("BIGQUERY_PROJECT"), "BigQueryのGCPプロジェクトID")
            if needs_bigquery
            else _env("BIGQUERY_PROJECT")
        ),
        dataset=_env("BIGQUERY_DATASET", "hansoku"),
        service_account_json=(
            _require(
                "GOOGLE_SERVICE_ACCOUNT_JSON",
                sa_json,
                "サービスアカウント鍵（JSONの中身、またはJSONファイルのパス）",
            )
            if needs_bigquery
            else sa_json
        ),
        local_path=_abs(_env("LOCAL_WAREHOUSE_PATH", ".local/warehouse.duckdb")),
    )

    appdb = AppDbSettings(
        env=resolved,
        dsn=(
            _require("NEON_DATABASE_URL", _env("NEON_DATABASE_URL"), "Neonの接続文字列")
            if cloud
            else _env(
                "LOCAL_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/hansoku"
            )
        ),
    )

    objects = ObjectStoreSettings(
        env=resolved,
        account_id=(
            _require("R2_ACCOUNT_ID", _env("R2_ACCOUNT_ID"), "CloudflareアカウントID")
            if cloud
            else _env("R2_ACCOUNT_ID")
        ),
        access_key_id=(
            _require("R2_ACCESS_KEY_ID", _env("R2_ACCESS_KEY_ID"), "R2アクセスキーID")
            if cloud
            else _env("R2_ACCESS_KEY_ID")
        ),
        secret_access_key=(
            _require(
                "R2_SECRET_ACCESS_KEY", _env("R2_SECRET_ACCESS_KEY"), "R2シークレットキー"
            )
            if cloud
            else _env("R2_SECRET_ACCESS_KEY")
        ),
        bucket=_env("R2_BUCKET", "hansoku-creatives"),
        public_base_url=_env("R2_PUBLIC_BASE_URL"),
        local_path=_abs(_env("LOCAL_OBJECT_STORE_PATH", ".local/objects")),
    )

    sources = SourceSettings(
        fw_spreadsheet_id=_env("FW_SPREADSHEET_ID", DEFAULT_FW_SPREADSHEET_ID),
        service_account_json=sa_json,
    )

    return Settings(
        env=resolved,
        warehouse=warehouse,
        appdb=appdb,
        objects=objects,
        sources=sources,
    )
