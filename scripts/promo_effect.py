"""8月の販促の結果を、FWの商品ABCの「差分」で測る。

台帳に対象商品を手入力しなくても、FWのデータだけで販促を切り出せる：
  夏のおすすめ = 直近の春に無かった商品（＝夏に新しく出た品）
  月替わりランチ = 前月に無かった商品（＝今月ぶん）
どちらも「その期間に新しく現れた商品名」を抜き出し、その売上を積む。

使い方（本番）:
  HANSOKU_ENV=cloud NEON_DATABASE_URL=... python scripts/promo_effect.py
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

TARGET = (date(2026, 8, 1), date(2026, 8, 31))          # 測る月
SPRING = (date(2026, 3, 1), date(2026, 5, 31))          # 「直近の春」基準
PREV_MONTH = (date(2026, 7, 1), date(2026, 7, 31))      # 前月（月替わり判定）
TOPN = 8


def products(warehouse, dfrom, dto):
    """{store_code: {product_name: sales}} を返す。"""
    rows = warehouse.aggregate(AggregateQuery(
        date_from=dfrom, date_to=dto, grain=GRAIN_MONTH,
        metrics=["product_sales"], group_by=("store_code", "product_name"),
    ))
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        name = r.get("product_name")
        if not name:
            continue
        out.setdefault(r["store_code"], {})[name] = out.get(r["store_code"], {}).get(name, 0) + (r["value"] or 0)
    return out


def main() -> int:
    settings = load_settings()
    m = StoreMaster.load()
    smap = {s.store_code: s for s in m.active}
    with get_warehouse(settings) as warehouse:
        aug = products(warehouse, *TARGET)
        spring = products(warehouse, *SPRING)
        prev = products(warehouse, *PREV_MONTH)

    # まず商品データの月別カバレッジ（春の基準が取れているか確認用）
    print("## 商品データのカバレッジ")
    print(f"- 8月に商品データがある店: {sum(1 for c in aug if aug[c])} / {len(smap)}")
    print(f"- 春(3〜5月)に商品データがある店: {sum(1 for c in spring if spring[c])}")
    print(f"- 7月に商品データがある店: {sum(1 for c in prev if prev[c])}")
    print()

    print("## 夏に新しく出た商品（＝夏おすすめ候補）とその8月売上")
    print("店 / 新商品売上合計 / 8月売上に占める新商品比 / 上位品")
    for code in sorted(aug, key=lambda c: (smap[c].region if c in smap else "", c)):
        name = smap[code].store_name if code in smap else code
        a = aug.get(code, {})
        base = set(spring.get(code, {}))
        if not a:
            continue
        if not base:
            print(f"- {name}: 春の基準データなし（判定不可）")
            continue
        new = {p: v for p, v in a.items() if p not in base}
        newsum = sum(new.values())
        total = sum(a.values())
        share = (newsum / total * 100) if total else 0
        top = "、".join(f"{p}({v:,.0f})" for p, v in sorted(new.items(), key=lambda x: -x[1])[:TOPN])
        print(f"- {name}: ¥{newsum:,.0f} / 全体の{share:.0f}% / {top or '新商品なし'}")
    print()

    print("## 8月に新しく出た商品（前月比・月替わりランチ等の切り出し）")
    for code in sorted(aug, key=lambda c: (smap[c].region if c in smap else "", c)):
        name = smap[code].store_name if code in smap else code
        a = aug.get(code, {})
        base = set(prev.get(code, {}))
        if not a or not base:
            continue
        new = {p: v for p, v in a.items() if p not in base}
        if not new:
            continue
        top = "、".join(f"{p}({v:,.0f})" for p, v in sorted(new.items(), key=lambda x: -x[1])[:TOPN])
        print(f"- {name}: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
