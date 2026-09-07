"""
集計の入口。

比率系の指標（原価率・客単価）は行ごとの値を平均しても正しくならないため、
必ず分子・分母をそれぞれ合計してから割る。ここを1か所に閉じ込めておく。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from .db.warehouse import AggregateQuery, Warehouse
from .model import (
    GRAIN_MONTH,
    METRIC_COVERS,
    METRIC_DRINK_THEORY_COST,
    METRIC_FOOD_THEORY_COST,
    METRIC_SALES,
)
from .stores import StoreMaster

# 比率指標 → (分子となる指標群, 分母となる指標)
RATIO_METRICS: dict[str, tuple[tuple[str, ...], str]] = {
    # 理論原価率 = (フード理論原価 + ドリンク理論原価) / 売上
    "cost_rate": ((METRIC_FOOD_THEORY_COST, METRIC_DRINK_THEORY_COST), METRIC_SALES),
    # 客単価 = 売上 / 客数（客数が取り込めるようになったら有効になる）
    "avg_check": ((METRIC_SALES,), METRIC_COVERS),
}

# 分子と分母を同じ取り込み口から取る比率。
#
# 理論原価は店長会シート（fw_sheet）の1枚から来る。同じ表の売上で割らないと
# 意味を成さない。売上は fw_sheet（税抜）と fw_uriage_suii（税込）の両方が
# 書いていて、SOURCE_PRIORITY は税込を採る。そのまま割ると分子だけ税抜になり、
# 原価率が実態より約2.7pt 低く出る（低いほど良い指標なので、全店が実力より
# 優秀に見える方向にずれる）。
#
# どちらの税基準かを当てにいくのではなく、「1つの表の中で割る」ことで揃える。
SAME_SOURCE_RATIOS: dict[str, tuple[str, ...]] = {
    "cost_rate": ("fw_sheet",),
}


@dataclass(frozen=True)
class MetricValue:
    store_code: str
    metric: str
    value: float | None


def totals(
    warehouse: Warehouse,
    *,
    date_from: date,
    date_to: date,
    metrics: Sequence[str],
    store_codes: Sequence[str] | None = None,
    grain: str = GRAIN_MONTH,
    hours: Sequence[int] | None = None,
    sources: Sequence[str] | None = None,
) -> dict[tuple[str, str], float]:
    """(store_code, metric) → 合計値。"""
    query = AggregateQuery(
        date_from=date_from,
        date_to=date_to,
        grain=grain,
        metrics=list(metrics),
        store_codes=list(store_codes) if store_codes else None,
        hours=list(hours) if hours else None,
        sources=list(sources) if sources else None,
        group_by=("store_code", "metric"),
    )
    return {(r["store_code"], r["metric"]): r["value"] for r in warehouse.aggregate(query)}


def ratio(
    warehouse: Warehouse,
    metric: str,
    *,
    date_from: date,
    date_to: date,
    store_codes: Sequence[str] | None = None,
    grain: str = GRAIN_MONTH,
    hours: Sequence[int] | None = None,
) -> list[MetricValue]:
    """
    比率指標を、分子・分母を合計してから割って求める。

    分母が 0 または欠測の店舗は value=None で返す（0除算を握りつぶして
    0% と表示すると、原価率のような lower_better 指標で「達成」に見えてしまう）。
    """
    if metric not in RATIO_METRICS:
        raise ValueError(f"比率指標ではありません: {metric!r}")
    numerators, denominator = RATIO_METRICS[metric]

    sums = totals(
        warehouse,
        date_from=date_from,
        date_to=date_to,
        metrics=[*numerators, denominator],
        store_codes=store_codes,
        grain=grain,
        hours=hours,
        # 分子と分母を1つの表の中で取る比率は、その口に絞る。
        sources=SAME_SOURCE_RATIOS.get(metric),
    )
    codes = sorted({code for code, _ in sums})
    results: list[MetricValue] = []
    for code in codes:
        bottom = sums.get((code, denominator))
        if not bottom:
            results.append(MetricValue(code, metric, None))
            continue
        top = sum(sums.get((code, n), 0.0) for n in numerators)
        results.append(MetricValue(code, metric, top / bottom))
    return results


def by_region(
    warehouse: Warehouse,
    master: StoreMaster,
    *,
    date_from: date,
    date_to: date,
    metric: str,
    grain: str = GRAIN_MONTH,
) -> dict[str, float]:
    """エリア単位（大阪/東京/…）の合計。総合ページのエリア別サマリに使う。"""
    sums = totals(
        warehouse,
        date_from=date_from,
        date_to=date_to,
        metrics=[metric],
        store_codes=master.active_codes,
        grain=grain,
    )
    out: dict[str, float] = {}
    for (code, _), value in sums.items():
        region = master.by_code(code).region or "未分類"
        out[region] = out.get(region, 0.0) + value
    return out


def by_brand(
    warehouse: Warehouse,
    master: StoreMaster,
    *,
    date_from: date,
    date_to: date,
    metric: str,
    grain: str = GRAIN_MONTH,
) -> dict[str, float]:
    """ブランド単位の合計。スケジュール・ダッシュボードの色分け単位に合わせた束ね方。"""
    sums = totals(
        warehouse,
        date_from=date_from,
        date_to=date_to,
        metrics=[metric],
        store_codes=master.active_codes,
        grain=grain,
    )
    out: dict[str, float] = {}
    for (code, _), value in sums.items():
        brand = master.by_code(code).brand
        out[brand] = out.get(brand, 0.0) + value
    return out
