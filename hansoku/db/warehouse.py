"""
Warehouse（実績データ f_actuals）の共通インターフェースとSQL構築。

同じSQLを PostgreSQL（Neon・本番）/ DuckDB（ローカル検証）/ BigQuery（将来）で
動かすため、方言差はここに閉じ込める。差があるのは3点だけ:

  * 名前付きパラメータの書き方（%(x)s / $x / @x）
  * 配列を使った IN 述語の書き方
  * テーブル名の修飾

重複排除に QUALIFY は使わない。BigQuery と DuckDB は解釈できるが
PostgreSQL は解釈できないため、どこでも通る副問い合わせの形にしている。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from ..model import ADDITIVE_METRICS, GRAINS, METRICS, ActualRow

# f_actuals の列順。INSERT と SELECT で共有する。
COLUMNS: tuple[str, ...] = (
    "store_code",
    "date",
    "grain",
    "hour",
    "metric",
    "value",
    "product_name",
    "product_category",
    "kind",
    "source",
    "ingested_at",
)

_PARAM = re.compile(r":([a-z_][a-z0-9_]*)", re.IGNORECASE)

# 方言ごとの差分: (パラメータの書き方, 配列IN述語のひな型)
DIALECTS: dict[str, tuple[str, str]] = {
    "postgres": ("pyformat", "{column} = ANY(%({param})s)"),
    "duckdb": ("dollar", "{column} IN (SELECT UNNEST(${param}))"),
    "bigquery": ("at", "{column} IN UNNEST(@{param})"),
}


def render_params(sql: str, dialect: str) -> str:
    """``:name`` 形式のパラメータを各方言の記法へ変換する。"""
    try:
        style, _ = DIALECTS[dialect]
    except KeyError:
        raise ValueError(f"未知の方言です: {dialect}") from None
    if style == "pyformat":
        return _PARAM.sub(r"%(\1)s", sql)
    if style == "dollar":
        return _PARAM.sub(r"$\1", sql)
    return _PARAM.sub(r"@\1", sql)


@dataclass(frozen=True)
class AggregateQuery:
    """任意の店舗×期間×指標×時間帯で実績を集計するための問い合わせ。"""

    date_from: date
    date_to: date
    grain: str
    metrics: Sequence[str] | None = None
    store_codes: Sequence[str] | None = None
    hours: Sequence[int] | None = None
    product_categories: Sequence[str] | None = None
    product_names: Sequence[str] | None = None
    # 出力の束ね方。store_code / date / hour / metric から選ぶ。
    group_by: Sequence[str] = ("store_code", "metric")

    ALLOWED_GROUP_BY = (
        "store_code",
        "date",
        "hour",
        "metric",
        "product_name",
        "product_category",
    )

    def validate(self) -> None:
        if self.grain not in GRAINS:
            raise ValueError(f"未知の grain です: {self.grain!r}")
        if self.date_from > self.date_to:
            raise ValueError("date_from が date_to より後になっています")
        for metric in self.metrics or ():
            if metric not in METRICS:
                raise ValueError(f"未知の metric です: {metric!r}")
        for column in self.group_by:
            if column not in self.ALLOWED_GROUP_BY:
                raise ValueError(f"group_by に使えない列です: {column!r}")
        if not self.group_by:
            raise ValueError("group_by が空です")
        # 指標をまたいで束ねると、売上と客数のように意味の違う値が1つに潰れる。
        # group_by に metric を入れるか、指標を1つに絞るかのどちらかを求める。
        if "metric" not in self.group_by and len(self.metrics or ()) != 1:
            raise ValueError(
                "複数の指標を metric で束ねずに集計しようとしています。"
                " group_by に 'metric' を含めるか、metrics を1つに絞ってください。"
            )


def build_aggregate_sql(
    table: str, query: AggregateQuery, dialect: str = "duckdb"
) -> tuple[str, dict[str, Any]]:
    """
    集計SQLと名前付きパラメータを組み立てる。

    加法的な指標（売上・客数など）は SUM、比率系（客単価など）は AVG で束ねる。
    比率を正しく出したい場合は分子・分母の指標から組み直すこと（``analytics`` 参照）。
    """
    query.validate()
    if dialect not in DIALECTS:
        raise ValueError(f"未知の方言です: {dialect}")
    _, array_in = DIALECTS[dialect]

    where = ["grain = :grain", "date BETWEEN :date_from AND :date_to"]
    params: dict[str, Any] = {
        "grain": query.grain,
        "date_from": query.date_from,
        "date_to": query.date_to,
    }

    for name, values, column in (
        ("metrics", query.metrics, "metric"),
        ("store_codes", query.store_codes, "store_code"),
        ("hours", query.hours, "hour"),
        ("product_categories", query.product_categories, "product_category"),
        ("product_names", query.product_names, "product_name"),
    ):
        if values:
            where.append(array_in.format(column=column, param=name))
            params[name] = list(values)

    group_sql = ", ".join(query.group_by)
    additive = ", ".join(f"'{m}'" for m in sorted(ADDITIVE_METRICS))
    sql = f"""
WITH ranked AS (
    SELECT
        {', '.join(COLUMNS)},
        ROW_NUMBER() OVER (
            PARTITION BY store_code, date, grain, hour, metric,
                         product_name, product_category
            -- 同じ実績が「中間」と「確定」の両方で入っていれば確定を採る。
            -- 同じ確定区分なら後から取り込んだ行を採る。
            ORDER BY CASE WHEN kind = '確定' THEN 0 ELSE 1 END, ingested_at DESC
        ) AS row_rank
    FROM {table}
    WHERE {' AND '.join(where)}
)
SELECT
    {group_sql},
    CASE
        WHEN MIN(metric) IN ({additive}) AND MAX(metric) = MIN(metric) THEN SUM(value)
        ELSE AVG(value)
    END AS value,
    COUNT(*) AS row_count
FROM ranked
WHERE row_rank = 1
GROUP BY {group_sql}
ORDER BY {group_sql}
""".strip()
    return sql, params


class Warehouse(ABC):
    """実績データの読み書き口。"""

    dialect: str = "duckdb"

    @abstractmethod
    def ensure_schema(self) -> None:
        """f_actuals を（無ければ）作る。"""

    @abstractmethod
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """SQL を実行して辞書のリストで返す。"""

    @abstractmethod
    def replace_actuals(self, rows: Iterable[ActualRow], *, scope_stores: bool = False) -> int:
        """
        実績を冪等に入れ替える。

        渡された行が覆う (source, grain, date) の組み合わせを先に削除してから
        まとめて挿入する。同じ取り込みを何度流しても件数が増えない。
        戻り値は挿入した行数。

        scope_stores=True なら削除範囲に store_code も加える。1店ずつ・一部店だけ
        流すときに、同じ source と月を共有する他店の実績を巻き添えで消さないため。
        """

    @abstractmethod
    def table_name(self, name: str) -> str:
        """方言に応じた完全修飾テーブル名。"""

    def aggregate(self, query: AggregateQuery) -> list[dict[str, Any]]:
        sql, params = build_aggregate_sql(self.table_name("f_actuals"), query, self.dialect)
        return self.query(sql, params)

    def close(self) -> None:  # pragma: no cover - 実装によっては何もしない
        pass

    def __enter__(self) -> "Warehouse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
