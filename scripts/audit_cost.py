#!/usr/bin/env python3
"""損益（原価率）の取込状況を全店で棚卸しする。読み取りのみ。

原価率は「フード/ドリンク理論原価 ÷ F/D売上」（いずれも店長会資料DL＝損益管理シート
由来）で計算する。分子（理論原価）か分母（売上）が欠けている店・月は原価率が出せず、
画面では「―」になる。この監査は、どの店のどの月が取り込めていないかを洗い出し、
「―」で放置しないための一覧を出す。

環境変数:
    AUDIT_FROM / AUDIT_TO … 監査する月範囲（YYYY-MM）。既定は直近12ヶ月。
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hansoku.analytics import ratio
from hansoku.db import get_warehouse
from hansoku.db.warehouse import AggregateQuery
from hansoku.model import (
    GRAIN_MONTH,
    METRIC_DRINK_SALES,
    METRIC_DRINK_THEORY_COST,
    METRIC_FOOD_SALES,
    METRIC_FOOD_THEORY_COST,
    METRIC_PRODUCT_COST,
    METRIC_PRODUCT_SALES,
)
from hansoku.settings import load_settings
from hansoku.stores import StoreMaster


def _month_iter(a: str, b: str) -> list[str]:
    ys, ms = int(a[:4]), int(a[5:7])
    ye, me = int(b[:4]), int(b[5:7])
    out = []
    y, m = ys, ms
    while (y, m) <= (ye, me):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _default_window() -> tuple[str, str]:
    # 「今」の直近“完全月”を終端に、そこから12ヶ月さかのぼる。
    today = date.today()
    y, m = today.year, today.month
    m -= 1  # 当月は未確定なので1つ手前
    if m < 1:
        m = 12
        y -= 1
    end = f"{y:04d}-{m:02d}"
    sy, sm = y, m - 11
    while sm < 1:
        sm += 12
        sy -= 1
    return f"{sy:04d}-{sm:02d}", end


def main() -> None:
    settings = load_settings()
    master = StoreMaster.load()
    a = os.environ.get("AUDIT_FROM") or _default_window()[0]
    b = os.environ.get("AUDIT_TO") or _default_window()[1]
    months = _month_iter(a, b)
    m_from = date(int(a[:4]), int(a[5:7]), 1)
    m_to = date(int(b[:4]), int(b[5:7]), 28)
    codes = master.active_codes

    print(f"# 損益（原価率）取込監査　{a} 〜 {b}（{len(months)}ヶ月）")
    print("")
    print("原価率＝(フード理論原価＋ドリンク理論原価) ÷ (F売上＋D売上)。")
    print("分子・分母のどちらかが欠ける店・月は原価率が出せず、画面では「―」。")
    print("")

    with get_warehouse(settings) as wh:
        # 1) 各指標の (店, 月) 在り高。取込元（店長会シート）に何が入ってきているか。
        present: dict[tuple[str, str, str], bool] = {}
        for metric in (
            METRIC_FOOD_THEORY_COST,
            METRIC_DRINK_THEORY_COST,
            METRIC_FOOD_SALES,
            METRIC_DRINK_SALES,
        ):
            rows = wh.aggregate(
                AggregateQuery(
                    date_from=m_from, date_to=m_to, grain=GRAIN_MONTH,
                    metrics=[metric], store_codes=codes,
                    group_by=("store_code", "date"),
                )
            )
            for r in rows:
                if r.get("value"):
                    ym = r["date"].strftime("%Y-%m")
                    present[(r["store_code"], ym, metric)] = True

        # 2) 実際に原価率が出せる (店, 月)。画面と同じ判定：
        #    理論原価（共有シート）で出せる月＋ABC原価金額で補完できる月。
        cr_months: dict[str, set[str]] = {c: set() for c in codes}
        theory_months: dict[str, set[str]] = {c: set() for c in codes}
        abc_months: dict[str, set[str]] = {c: set() for c in codes}
        for ym in months:
            ms = date(int(ym[:4]), int(ym[5:7]), 1)
            me = date(int(ym[:4]), int(ym[5:7]), 28)
            for mv in ratio(wh, "cost_rate", date_from=ms, date_to=me, store_codes=codes):
                if mv.value:
                    cr_months.setdefault(mv.store_code, set()).add(ym)
                    theory_months.setdefault(mv.store_code, set()).add(ym)
        # ABC 原価金額での補完（理論が無い店・月を埋める。画面の cost_rate と同じ）。
        abc_cost: dict[tuple[str, str], float] = {}
        abc_sales: dict[tuple[str, str], float] = {}
        for metric, acc in ((METRIC_PRODUCT_COST, abc_cost), (METRIC_PRODUCT_SALES, abc_sales)):
            for r in wh.aggregate(
                AggregateQuery(
                    date_from=m_from, date_to=m_to, grain=GRAIN_MONTH,
                    metrics=[metric], store_codes=codes,
                    group_by=("store_code", "date"),
                )
            ):
                if r.get("value"):
                    acc[(r["store_code"], r["date"].strftime("%Y-%m"))] = r["value"]
        for (c_code, ym), cost in abc_cost.items():
            sales = abc_sales.get((c_code, ym))
            if cost and sales and ym in months and ym not in cr_months.get(c_code, set()):
                rate = cost / sales
                if 0 < rate <= 1:
                    cr_months.setdefault(c_code, set()).add(ym)
                    abc_months.setdefault(c_code, set()).add(ym)

    # 3) 店ごとに分類して出力。
    done, partial, none_ = [], [], []
    for s in master.active:
        got = sorted(cr_months.get(s.store_code, set()))
        n = len(got)
        if n == 0:
            none_.append(s)
        elif n >= len(months):
            done.append((s, got))
        else:
            partial.append((s, got))

    def _theory_note(code: str) -> str:
        # 原価率が出ない店で、理論原価と売上のどちらが欠けているかを示す。
        ft = any(present.get((code, ym, METRIC_FOOD_THEORY_COST)) for ym in months)
        dt = any(present.get((code, ym, METRIC_DRINK_THEORY_COST)) for ym in months)
        fs = any(present.get((code, ym, METRIC_FOOD_SALES)) for ym in months)
        ds = any(present.get((code, ym, METRIC_DRINK_SALES)) for ym in months)
        miss = []
        if not (ft or dt):
            miss.append("理論原価なし")
        if not (fs or ds):
            miss.append("F/D売上なし")
        return "／".join(miss) if miss else "分子・分母は在るが月がずれている可能性"

    print(f"## ✗ 原価率が1ヶ月も出せない店（損益 未取込）… {len(none_)}店")
    if none_:
        for s in none_:
            print(f"- {s.store_code} {s.store_name}（{s.region or '—'}）… {_theory_note(s.store_code)}")
    else:
        print("- なし")
    print("")

    def _src(code: str) -> str:
        t, ab = len(theory_months.get(code, set())), len(abc_months.get(code, set()))
        return f"（内訳 理論{t}／ABC補完{ab}）"

    print(f"## △ 一部の月しか出せない店（取りこぼし）… {len(partial)}店")
    if partial:
        for s, got in partial:
            missing = [ym for ym in months if ym not in got]
            print(f"- {s.store_code} {s.store_name}（{s.region or '—'}）… "
                  f"取込 {len(got)}/{len(months)}ヶ月{_src(s.store_code)}・欠け {len(missing)}ヶ月: {', '.join(missing)}")
    else:
        print("- なし")
    print("")

    print(f"## ✓ 全月そろっている店… {len(done)}店")
    for s, got in done:
        print(f"- {s.store_code} {s.store_name}（{s.region or '—'}）{_src(s.store_code)}")
    print("")

    tot_theory = sum(len(v) for v in theory_months.values())
    tot_abc = sum(len(v) for v in abc_months.values())
    print("## まとめ")
    print(f"- 稼働 {len(master.active)}店中： 未取込 {len(none_)}／一部 {len(partial)}／全月 {len(done)}")
    print(f"- 原価率が出せる(店×月)の内訳： 理論原価 {tot_theory} ／ ABC補完 {tot_abc}")
    print(f"- 対象期間: {a} 〜 {b}")


if __name__ == "__main__":
    main()
