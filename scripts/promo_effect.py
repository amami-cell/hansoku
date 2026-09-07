"""ルクアLargo(1160) パフェ・スノー一覧（商品別）。
月次データは月初日付なので、範囲は必ず「月末まで」で取る（翌月を巻き込まない）。"""
from __future__ import annotations
import calendar, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings

CODE="1160"
def mrange(y,m):
    return date(y,m,1), date(y,m,calendar.monthrange(y,m)[1])
PERIODS={"2026-06":mrange(2026,6),"2026-07":mrange(2026,7),
         "2026-08":mrange(2026,8),"2025-08":mrange(2025,8)}
KEYS=("パフェ","スノー")

def prods(wh,a,b):
    d={}
    for r in wh.aggregate(AggregateQuery(date_from=a,date_to=b,grain=GRAIN_MONTH,
            metrics=["product_sales"],store_codes=[CODE],group_by=("product_name",))):
        n=r.get("product_name") or ""
        d[n]=d.get(n,0)+(r["value"] or 0)
    return d

def main():
    s=load_settings()
    data={}
    with get_warehouse(s) as wh:
        for label,(a,b) in PERIODS.items():
            data[label]=prods(wh,a,b)
    names=set()
    for label in PERIODS:
        for n in data[label]:
            if any(k in n for k in KEYS): names.add(n)
    g=lambda label,n: data[label].get(n,0)
    print("# ルクアLargo パフェ・スノー 一覧（範囲=月末まで・商品別）")
    print("| 商品 | 2026/6月 | 2026/7月 | 2026/8月 | 2025/8月 | 前年比(8月) |")
    print("|---|--:|--:|--:|--:|--:|")
    t26=t25=0
    for n in sorted(names,key=lambda n:-g("2026-08",n)):
        a8=g("2026-08",n); p8=g("2025-08",n); t26+=a8; t25+=p8
        yoy=f"{(a8/p8-1)*100:+.0f}%" if (a8 and p8) else ("新" if a8 and not p8 else ("終売" if p8 and not a8 else "—"))
        print(f"| {n} | {g('2026-06',n):,.0f} | {g('2026-07',n):,.0f} | {a8:,.0f} | {p8:,.0f} | {yoy} |")
    ty=f"{(t26/t25-1)*100:+.1f}%" if t25 else "—"
    print(f"| **合計** |  |  | **{t26:,.0f}** | **{t25:,.0f}** | **{ty}** |")
    return 0
raise SystemExit(main())
