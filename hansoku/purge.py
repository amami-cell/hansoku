"""0 以下の実績を消す。**開店前の月に入ってしまった 0 を片付けるため。**

⚠️ **再取込では消えない。** `replace_actuals` は「渡した行が覆う
(source, grain, date, 店, 指標)」だけを削除する。0 を入れなくなると、その月は
行が0件になり**削除範囲にも入らない**ので、前に入れた 0 が残り続ける。

⚠️ **残ると本当の値に勝ち続ける。** pos_sheet は `kind=確定` かつ
SOURCE_PRIORITY 最優先なので、あとからFWが実数を入れても 0 が採用される。
1766（2026-08-04 オープン）の 2026-04〜07 がこれに当たる。

⚠️ **既定は素振り。** 消すのは `apply=True` を明示したときだけ。
探すときと消すときで**同じ条件**を使う（`build_where`）。別々に書くと、
見たものと消すものがずれる。
"""
from __future__ import annotations

from typing import Any

from .model import GRAIN_MONTH


def build_where(
    *,
    source: str,
    store_codes: list[str] | None = None,
    metrics: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    grain: str = GRAIN_MONTH,
) -> tuple[str, dict[str, Any]]:
    """探す条件と消す条件を**1か所**で作る。

    ⚠️ **source は必ず要る。** 指定を忘れて全ソースの 0 を消すと、
    「本当に0だった月」（休業など）まで巻き添えになる。
    """
    if not source:
        raise ValueError("source は必ず指定してください（全ソースは消させない）")
    where = ["source = :source", "grain = :grain", "value <= 0"]
    params: dict[str, Any] = {"source": source, "grain": grain}
    if store_codes:
        keys = []
        for i, code in enumerate(store_codes):
            keys.append(f":store{i}")
            params[f"store{i}"] = code
        where.append(f"store_code IN ({', '.join(keys)})")
    if metrics:
        keys = []
        for i, metric in enumerate(metrics):
            keys.append(f":metric{i}")
            params[f"metric{i}"] = metric
        where.append(f"metric IN ({', '.join(keys)})")
    if date_from:
        where.append("date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where.append("date <= :date_to")
        params["date_to"] = date_to
    return " AND ".join(where), params


def purge_zeros(warehouse, *, apply: bool = False, **kwargs) -> list[dict[str, Any]]:
    """0以下の行を探し、`apply=True` のときだけ消す。見つけた行を返す。

    戻り値は**消す前に見たもの**。素振りでも本番でも同じ一覧が返るので、
    「何が消えるか」を出してから同じ条件で消せる。
    """
    where, params = build_where(**kwargs)
    table = warehouse.table_name("f_actuals")
    found = warehouse.query(
        f"SELECT store_code, date, metric, value, source, kind FROM {table} "
        f"WHERE {where} ORDER BY store_code, date, metric",
        params,
    )
    if apply and found:
        warehouse.execute(f"DELETE FROM {table} WHERE {where}", params)
    return found
