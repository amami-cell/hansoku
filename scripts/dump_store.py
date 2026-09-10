#!/usr/bin/env python3
"""1店ぶんの「画面に出せる材料」を棚卸しする。

年間→月→部門→商品ドリルを作る前に、その店で実際に何月ぶん・どの粒度の
データが warehouse にあるかを確かめる。読み取りだけ。STORE 環境変数で店コード
（既定 1160=ルクアLargo）。
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hansoku.db import get_warehouse
from hansoku.db.warehouse import AggregateQuery
from hansoku.model import (
    GRAIN_MONTH,
    METRIC_COVERS,
    METRIC_DEPT_QTY,
    METRIC_DEPT_SALES,
    METRIC_PRODUCT_SALES,
    METRIC_SALES,
    METRIC_SALES_BUDGET,
)
from hansoku.settings import load_settings

CODE = os.environ.get("STORE", "1160")
FROM = date(2023, 8, 1)
TO = date(2026, 12, 31)


def months_for(wh, metric, group=("date",)):
    rows = wh.aggregate(
        AggregateQuery(
            date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
            metrics=[metric], store_codes=[CODE], group_by=("store_code", "date"),
        )
    )
    return sorted({r["date"].strftime("%Y-%m") for r in rows})


def main() -> int:
    settings = load_settings()
    with get_warehouse(settings) as wh:
        print(f"== 店 {CODE} / 窓 {FROM}〜{TO} ==")
        for label, metric in [
            ("売上(monthly)", METRIC_SALES),
            ("売上予算(budget)", METRIC_SALES_BUDGET),
            ("客数(covers)", METRIC_COVERS),
            ("部門売上(dept_sales)", METRIC_DEPT_SALES),
            ("部門数量(dept_qty)", METRIC_DEPT_QTY),
            ("商品売上(product_sales)", METRIC_PRODUCT_SALES),
        ]:
            ms = months_for(wh, metric)
            print(f"\n{label}: {len(ms)}ヶ月")
            print("  " + (", ".join(ms) if ms else "（なし）"))

        # 一番新しい部門月の中身（部門名一覧）
        drows = wh.aggregate(
            AggregateQuery(
                date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
                metrics=[METRIC_DEPT_SALES], store_codes=[CODE],
                group_by=("store_code", "date", "product_name", "product_category"),
            )
        )
        by_m: dict[str, list] = {}
        for r in drows:
            m = r["date"].strftime("%Y-%m")
            by_m.setdefault(m, []).append(
                (r["product_name"], round(r["value"]), r["product_category"])
            )
        if by_m:
            latest = max(by_m)
            print(f"\n== 部門売上の最新月 {latest} の部門一覧（{len(by_m[latest])}件）==")
            for name, val, cat in sorted(by_m[latest], key=lambda x: -x[1]):
                print(f"  {name}: {val:,}円 (原価率{cat})")

        # 一番新しい商品月の中身（上位20）
        prows = wh.aggregate(
            AggregateQuery(
                date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
                metrics=[METRIC_PRODUCT_SALES], store_codes=[CODE],
                group_by=("store_code", "date", "product_name", "product_category"),
            )
        )
        pby_m: dict[str, list] = {}
        for r in prows:
            m = r["date"].strftime("%Y-%m")
            pby_m.setdefault(m, []).append(
                (r["product_name"], round(r["value"]), r["product_category"])
            )
        if pby_m:
            latest = max(pby_m)
            items = sorted(pby_m[latest], key=lambda x: -x[1])[:20]
            print(f"\n== 商品売上の最新月 {latest} の上位{len(items)} ==")
            for name, val, cat in items:
                print(f"  [{cat}] {name}: {val:,}円")

        # MONTHS 環境変数で指定した月の商品上位を出す（例: 前年の秋の商品を洗い出す）。
        want = [m.strip() for m in os.environ.get("MONTHS", "").split(",") if m.strip()]
        for m in want:
            rows = sorted(pby_m.get(m, []), key=lambda x: -x[1])[:40]
            print(f"\n== {m} の商品上位{len(rows)} ==")
            if not rows:
                print("  （この月の商品データなし）")
            for name, val, cat in rows:
                print(f"  [{cat}] {name}: {val:,}円")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
