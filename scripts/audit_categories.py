#!/usr/bin/env python3
"""店の品目区分ルールの精度監査。

store_categories.yaml のルールで、その店の商品がどの区分に落ちるかを実データで
確かめ、特に「その他」に落ちている商品（＝ルール未整備）を売上順に出す。
ここへキーワードを足せば構成比の精度が上がる。読み取りのみ。STORE 環境変数で店。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hansoku.db import get_warehouse
from hansoku.db.warehouse import AggregateQuery
from hansoku.model import GRAIN_MONTH, METRIC_PRODUCT_SALES
from hansoku.settings import load_settings
from hansoku.web.export import classify_category, load_store_categories

CODE = os.environ.get("STORE", "1160")
FROM = date(2024, 1, 1)
TO = date(2026, 12, 31)


def main() -> int:
    rules = load_store_categories().get(CODE)
    if not rules:
        print(f"店 {CODE} の区分ルールが store_categories.yaml にありません。")
        return 0
    settings = load_settings()
    with get_warehouse(settings) as wh:
        rows = wh.aggregate(
            AggregateQuery(
                date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
                metrics=[METRIC_PRODUCT_SALES], store_codes=[CODE],
                group_by=("store_code", "product_name"),
            )
        )
    other_name = rules.get("other", "その他")
    by_cat_sales: dict[str, float] = defaultdict(float)
    other_products: dict[str, float] = defaultdict(float)
    # 飲み物と分かる語を含むのに、ドリンク/アルコール以外に分類されている＝誤分類の疑い。
    drink_hints = ["ドリンク", "ラテ", "コーヒー", "珈琲", "カフェ", "ティー", "紅茶", "ソーダ",
                   "ジュース", "スムージー", "フロート", "レモネード", "モカ", "コーラ",
                   "エスプレッソ", "カプチーノ", "アイスチョコ"]
    suspicious: dict[str, tuple[str, float]] = {}
    total = 0.0
    for r in rows:
        name = r["product_name"]
        val = r["value"]
        total += val
        cat = classify_category(name, rules)
        by_cat_sales[cat] += val
        if cat == other_name:
            other_products[name] += val
        if cat not in ("ドリンク", "アルコール") and any(h in (name or "") for h in drink_hints):
            suspicious[name] = (cat, suspicious.get(name, (cat, 0))[1] + val)

    print(f"== 店 {CODE} 品目区分の精度監査（{FROM}〜{TO} 累計） ==")
    print(f"総売上（商品計）: {round(total):,}円")
    print("\n-- 区分別 売上シェア --")
    for cat, s in sorted(by_cat_sales.items(), key=lambda x: -x[1]):
        print(f"  {cat:8}: {round(s):>12,}円  ({s/ (total or 1) *100:5.1f}%)")

    print(f"\n-- 飲み物語を含むのに非ドリンク区分＝誤分類の疑い {len(suspicious)}件 売上順 --")
    if suspicious:
        for name, (cat, s) in sorted(suspicious.items(), key=lambda x: -x[1][1]):
            print(f"  [{cat:6}] {round(s):>11,}円  {name}")
    else:
        print("  なし（飲み物の誤分類は検出されず）")

    osum = sum(other_products.values())
    print(f"\n-- 「{other_name}」に落ちている商品 {len(other_products)}件（{osum/ (total or 1)*100:.1f}%）売上順 --")
    for name, s in sorted(other_products.items(), key=lambda x: -x[1])[:60]:
        print(f"  {round(s):>11,}円  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
