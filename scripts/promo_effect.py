"""2店を深掘り：ルクアLargoのパフェ昨対比／泡くらいの8月構成。"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings

def prods(wh, code, dfrom, dto):
    rows = wh.aggregate(AggregateQuery(
        date_from=dfrom, date_to=dto, grain=GRAIN_MONTH,
        metrics=["product_sales"], store_codes=[code],
        group_by=("product_name",)))
    d={}
    for r in rows:
        n=r.get("product_name")
        if n: d[n]=d.get(n,0)+(r["value"] or 0)
    return d

def show(title, d, n=25):
    print(f"### {title}  （品数{len(d)}／合計¥{sum(d.values()):,.0f}）")
    for name,v in sorted(d.items(), key=lambda x:-x[1])[:n]:
        print(f"  {v:>12,.0f}  {name}")
    print()

def main():
    s=load_settings()
    with get_warehouse(s) as wh:
        largo26=prods(wh,"1160",date(2026,8,1),date(2026,8,31))
        largo25=prods(wh,"1160",date(2025,8,1),date(2025,8,31))
        awa26=prods(wh,"1115",date(2026,8,1),date(2026,8,31))
    print("# ルクアLargo（1160）")
    show("2026-08 全商品", largo26)
    show("2025-08 全商品", largo25)
    # パフェ/スノー/かき氷/デザート系を拾う
    def dessert(d):
        keys=("パフェ","スノー","かき氷","ケーキ","タルト","プリン","ジェラート","クリームソーダ","チーズケーキ","シャルロット","アラスカ","ムース")
        return {k:v for k,v in d.items() if any(x in k for x in keys)}
    d26,d25=dessert(largo26),dessert(largo25)
    print(f"## パフェ・デザート系 昨対比：2025 ¥{sum(d25.values()):,.0f} → 2026 ¥{sum(d26.values()):,.0f}")
    print("### 2026 デザート系"); [print(f"  {v:>12,.0f}  {k}") for k,v in sorted(d26.items(),key=lambda x:-x[1])]
    print("### 2025 デザート系"); [print(f"  {v:>12,.0f}  {k}") for k,v in sorted(d25.items(),key=lambda x:-x[1])]
    print()
    print("# 泡くらい（1115）")
    show("2026-08 全商品（上位30）", awa26, 30)
    tabe={k:v for k,v in awa26.items() if ("放題" in k)}
    print(f"## 食べ/飲み放題だけの合計：¥{sum(tabe.values()):,.0f}（8月売上に占める割合の分子）")
    for k,v in sorted(tabe.items(),key=lambda x:-x[1]): print(f"  {v:>12,.0f}  {k}")
    return 0
raise SystemExit(main())
