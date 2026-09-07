"""FWの商品「部門（カテゴリ）」構成を確認する。

「春に無い＝新商品」では、夏おすすめとグランド改定・通常新メニューが混ざる。
正しく夏おすすめを切り出すには部門で見る必要がある。まず各店の8月の
部門別売上と、部門ごとの商品例を出して、"おすすめ/季節" 区分の有無を確かめる。
"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings
from hansoku.stores import StoreMaster

AUG = (date(2026, 8, 1), date(2026, 8, 31))


def main() -> int:
    settings = load_settings()
    m = StoreMaster.load()
    smap = {s.store_code: s for s in m.active}
    with get_warehouse(settings) as warehouse:
        # 部門別売上
        dept = warehouse.aggregate(AggregateQuery(
            date_from=AUG[0], date_to=AUG[1], grain=GRAIN_MONTH,
            metrics=["product_sales"], group_by=("store_code", "product_category"),
        ))
        # 商品×部門（部門にどんな品が入るか例示用）
        prod = warehouse.aggregate(AggregateQuery(
            date_from=AUG[0], date_to=AUG[1], grain=GRAIN_MONTH,
            metrics=["product_sales"], group_by=("store_code", "product_category", "product_name"),
        ))

    bycat = {}
    for r in dept:
        bycat.setdefault(r["store_code"], []).append((r.get("product_category") or "(無)", r["value"] or 0))
    examples = {}
    for r in prod:
        key = (r["store_code"], r.get("product_category") or "(無)")
        examples.setdefault(key, []).append((r.get("product_name") or "", r["value"] or 0))

    print("## 各店の商品部門（カテゴリ）構成・2026-08")
    print("※ ここに『おすすめ/季節/フェア』等の区分があれば、夏おすすめを正確に切り出せる")
    print()
    for code in sorted(bycat, key=lambda c: (smap[c].region if c in smap else "", c)):
        name = smap[code].store_name if code in smap else code
        cats = sorted(bycat[code], key=lambda x: -x[1])
        print(f"### {name}（{code}）  部門数={len(cats)}")
        for cat, val in cats:
            ex = examples.get((code, cat), [])
            ex_top = "、".join(p for p, _ in sorted(ex, key=lambda x: -x[1])[:3])
            print(f"  - {cat}: ¥{val:,.0f}  例) {ex_top}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
