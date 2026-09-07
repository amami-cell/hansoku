"""GOLD京都ポルタ(1168) 部門「おすすめ」＝夏おすすめ の月次推移。
dept_sales: product_name=部門名, product_category=原価率(数値文字列)。
dept_qty:   product_name=部門名 の数量。→ 売上・構成比・単価・原価率を月次で。"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings

CODE="1168"
MONTHS=[(date(2026,m,1),date(2026,m+1,1)) for m in range(3,8)]+[(date(2026,8,1),date(2026,9,1))]

def main():
    s=load_settings()
    dept={}   # (m, 部門名) -> {sales, rate}
    qty={}    # (m, 部門名) -> qty
    tot={}    # m -> 店売上
    with get_warehouse(s) as wh:
        for dfrom,dto in MONTHS:
            m=dfrom.strftime('%Y-%m')
            for r in wh.aggregate(AggregateQuery(date_from=dfrom,date_to=dto,grain=GRAIN_MONTH,
                    metrics=["dept_sales"],store_codes=[CODE],
                    group_by=("product_name","product_category"))):
                nm=r.get("product_name") or "(無)"
                try: rate=float(r["product_category"]) if r["product_category"] else None
                except: rate=None
                e=dept.setdefault((m,nm),{"sales":0.0,"rate":rate})
                e["sales"]+=r["value"] or 0
                if rate is not None: e["rate"]=rate
            for r in wh.aggregate(AggregateQuery(date_from=dfrom,date_to=dto,grain=GRAIN_MONTH,
                    metrics=["dept_qty"],store_codes=[CODE],group_by=("product_name",))):
                nm=r.get("product_name") or "(無)"
                qty[(m,nm)]=qty.get((m,nm),0)+(r["value"] or 0)
            trows=wh.aggregate(AggregateQuery(date_from=dfrom,date_to=dto,grain=GRAIN_MONTH,
                    metrics=["sales"],store_codes=[CODE],group_by=("store_code",)))
            tot[m]=sum(r["value"] or 0 for r in trows)
    months=[d.strftime('%Y-%m') for d,_ in MONTHS]
    names=sorted({nm for (_,nm) in dept})
    print("# 1168 部門名一覧（dept_sales の product_name）")
    print("／".join(names))
    print()
    print("# 部門別 月次売上（全部門）")
    for m in months:
        line=[f"{nm}={dept[(m,nm)]['sales']:,.0f}" for nm in names if (m,nm) in dept and dept[(m,nm)]['sales']]
        print(f"- {m}（店売上¥{tot[m]:,.0f}）: " + " / ".join(line))
    print()
    osu=[nm for nm in names if "おすすめ" in nm or "オススメ" in nm]
    print("## 「おすすめ」部門:", osu)
    for nm in osu:
        print(f"\n## {nm} 月次推移（売上／店売上比＝構成比／数量／単価／原価率）")
        print("| 月 | 売上 | 構成比 | 数量 | 単価 | 原価率 |")
        print("|---|--:|--:|--:|--:|--:|")
        for m in months:
            e=dept.get((m,nm)); q=qty.get((m,nm),0)
            if not e: 
                print(f"| {m} | ¥0 | — | 0 | — | — |"); continue
            sales=e['sales']; rate=e['rate']
            share=f"{sales/tot[m]*100:.2f}%" if tot[m] else "—"
            unit=f"¥{sales/q:,.0f}" if q else "—"
            crv=f"{rate:.1f}%" if rate is not None else "—"
            print(f"| {m} | ¥{sales:,.0f} | {share} | {q:,.0f} | {unit} | {crv} |")
    return 0
raise SystemExit(main())
