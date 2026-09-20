"""
集計の入口。

比率系の指標（原価率・客単価）は行ごとの値を平均しても正しくならないため、
必ず分子・分母をそれぞれ合計してから割る。ここを1か所に閉じ込めておく。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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


def actual_cost_by_month(
    warehouse: Warehouse,
    *,
    months: Sequence[str],
    store_codes: Sequence[str] | None = None,
) -> dict[str, dict[str, dict]]:
    """店×月の**実原価**を返す。ABCの部門（理論原価）が無い月でも出せる。

        実原価 = 前月棚卸 + 当月仕入 − 当月棚卸

    棚卸と仕入はインフォマートの月次集計から来ており、フード／ドリンクに
    分かれている。FWのABC部門に依存しないので、部門が紐付いていない店・月でも
    算出できる（1069 ひよこ飯店 / 1137 たいだい の 2026-07 以前がこれに当たる）。

    **理論原価率と同じ列に混ぜてはいけない。** 理論はレシピ通りに作った場合の値、
    こちらは実際に使った金額で、その差が不明ロスそのものだから。混ぜると
    見たかった差が消える。別の指標として持つこと。

    返り値: {store_code: {"YYYY-MM": {"food":…, "drink":…, "total":…, "rate":…}}}
    材料が1つでも欠けた店×月は入れない（0として計算すると、棚卸を取り込めて
    いない月が「原価0円」や「仕入まるごとが原価」に化ける）。
    """
    from .model import (
        METRIC_DRINK_INVENTORY,
        METRIC_DRINK_PURCHASE,
        METRIC_FOOD_INVENTORY,
        METRIC_FOOD_PURCHASE,
    )

    wanted = sorted(set(months))
    if not wanted:
        return {}
    # 前月棚卸が要るので、1ヶ月前から集める。
    need = sorted(set(wanted) | {_prev_month(m) for m in wanted})

    per_month: dict[str, dict[tuple[str, str], float]] = {}
    for month in need:
        start = datetime.strptime(month, "%Y-%m").date()
        end = date(start.year, start.month, 28)   # 月内であればよい（grain=month）
        per_month[month] = totals(
            warehouse,
            date_from=start,
            date_to=end,
            metrics=[
                METRIC_FOOD_INVENTORY, METRIC_DRINK_INVENTORY,
                METRIC_FOOD_PURCHASE, METRIC_DRINK_PURCHASE,
                METRIC_SALES,
            ],
            store_codes=store_codes,
        )

    out: dict[str, dict[str, dict]] = {}
    for month in wanted:
        cur = per_month.get(month, {})
        prev = per_month.get(_prev_month(month), {})
        codes = {code for code, _ in cur} | {code for code, _ in prev}
        for code in codes:
            parts = {}
            for side, inv, pur in (
                ("food", METRIC_FOOD_INVENTORY, METRIC_FOOD_PURCHASE),
                ("drink", METRIC_DRINK_INVENTORY, METRIC_DRINK_PURCHASE),
            ):
                begin = prev.get((code, inv))
                buy = cur.get((code, pur))
                finish = cur.get((code, inv))
                if begin is None or buy is None or finish is None:
                    parts = {}
                    break
                parts[side] = begin + buy - finish
            if not parts:
                continue
            total = parts["food"] + parts["drink"]
            sales = cur.get((code, METRIC_SALES))
            out.setdefault(code, {})[month] = {
                "food": round(parts["food"]),
                "drink": round(parts["drink"]),
                "total": round(total),
                # 売上が無い月は率を出さない。0除算を0%にすると、
                # lower_better の原価率で「達成」に見えてしまう。
                "rate": round(total / sales * 100, 4) if sales else None,
            }
    return out


def _prev_month(month: str) -> str:
    """'2026-01' → '2025-12'。"""
    y, m = (int(x) for x in month.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


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
