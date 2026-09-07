"""ルクアLargo(1160) パフェスノー一覧：商品別に月次売上・前年8月・ABCランク。"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings

CODE="1160"
PERIODS={
 "2026-06":(date(2026,6,1),date(2026,7,1)),
 "2026-07":(date(2026,7,1),date(2026,8,1)),
 "2026-08":(date(2026,8,1),date(2026,9,1)),
 "2025-08":(date(2025,8,1),date(2025,9,1)),
}
KEYS=("パフェ","スノー")

def prods(wh, dfrom, dto):
    rows=wh.aggregate(AggregateQuery(date_from=dfrom,date_to=dto,grain=GRAIN_MONTH,
        metrics=["product_sales"],store_codes=[CODE],
        group_by=("product_name","product_category")))
    d={}
    for r in rows:
        n=r.get("product_name") or ""
        d[n]={"sales":(r["value"] or 0)+d.get(n,{}).get("sales",0),"rank":r.get("product_category")}
    return d

def main():
    s=load_settings()
    data={}
    with get_warehouse(s) as wh:
        for label,(a,b) in PERIODS.items():
            data[label]=prods(wh,a,b)
    # パフェ/スノー商品を集める（全期間の和集合）
    names=set()
    for label in PERIODS:
        for n in data[label]:
            if any(k in n for k in KEYS): names.add(n)
    print("# ルクアLargo パフェ・スノー 一覧（商品別）")
    print("| 商品 | 6月 | 7月 | 8月 | 前年8月 | 前年比 | ABC(8月) |")
    print("|---|--:|--:|--:|--:|--:|:--:|")
    def g(label,n): return data[label].get(n,{}).get("sales",0)
    rows=sorted(names,key=lambda n:-g("2026-08",n))
    t26=t25=0
    for n in rows:
        a8=g("2026-08",n); p8=g("2025-08",n)
        t26+=a8; t25+=p8
        yoy=f"{(a8/p8-1)*100:+.0f}%" if (a8 and p8) else ("新" if a8 and not p8 else ("終売" if p8 and not a8 else "—"))
        rank=data["2026-08"].get(n,{}).get("rank") or "—"
        print(f"| {n} | {g('2026-06',n):,.0f} | {g('2026-07',n):,.0f} | {a8:,.0f} | {p8:,.0f} | {yoy} | {rank} |")
    ty=f"{(t26/t25-1)*100:+.1f}%" if t25 else "—"
    print(f"| **合計** |  |  | **{t26:,.0f}** | **{t25:,.0f}** | **{ty}** |  |")
    return 0
raise SystemExit(main())
