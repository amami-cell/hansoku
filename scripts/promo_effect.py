"""GOLD京都ポルタ(1168) の部門「おすすめ」＝夏おすすめ の月次推移。
売上・構成比・単価（=売上/数量）を直近数ヶ月で並べる。原価が部門で
取れるかも確認する（取れなければ店単位の原価率を別途出す）。"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings

CODE="1168"
MONTHS=[(date(2026,m,1), date(2026,m+1,1)) for m in range(3,8)]+[(date(2026,8,1),date(2026,9,1))]

def agg(wh, metric, dfrom, dto, groupby):
    return wh.aggregate(AggregateQuery(date_from=dfrom, date_to=dto, grain=GRAIN_MONTH,
        metrics=[metric], store_codes=[CODE], group_by=groupby))

def month_cat(wh, metric):
    """{('YYYY-MM',cat): value}"""
    out={}
    for dfrom,dto in MONTHS:
        for r in agg(wh, metric, dfrom, dto, ("product_category",)):
            out[(dfrom.strftime('%Y-%m'), r.get('product_category') or '(無)')]=r['value'] or 0
    return out

def main():
    s=load_settings()
    with get_warehouse(s) as wh:
        sales=month_cat(wh,"product_sales")
        qty=month_cat(wh,"dept_qty")
        # 店全体の売上（構成比の分母）と原価率（部門で取れるか確認）
        tot={}
        for dfrom,dto in MONTHS:
            rows=agg(wh,"sales",dfrom,dto,("store_code",))
            tot[dfrom.strftime('%Y-%m')]=sum(r['value'] or 0 for r in rows)
    months=[d.strftime('%Y-%m') for d,_ in MONTHS]
    cats=sorted({c for (_,c) in sales})
    print("# GOLD京都ポルタ(1168) 部門一覧（月×部門の売上）")
    print("月 \\ 部門:", "／".join(cats))
    for m in months:
        print(f"- {m}: " + "／".join(f"{c}={sales.get((m,c),0):,.0f}" for c in cats))
    print()
    # 「おすすめ」を含む部門を夏おすすめとして抽出
    osusume=[c for c in cats if "おすすめ" in c or "オススメ" in c or c.startswith("01")]
    print("## 夏おすすめ部門と判定:", osusume)
    print()
    print("## おすすめ部門 月次推移（売上／店売上に占める構成比／単価=売上÷数量）")
    print("| 月 | おすすめ売上 | 店売上 | 構成比 | おすすめ数量 | 単価 |")
    print("|---|--:|--:|--:|--:|--:|")
    for m in months:
        osu=sum(sales.get((m,c),0) for c in osusume)
        q=sum(qty.get((m,c),0) for c in osusume)
        t=tot.get(m,0)
        share=f"{osu/t*100:.1f}%" if t else "—"
        unit=f"¥{osu/q:,.0f}" if q else "—"
        print(f"| {m} | ¥{osu:,.0f} | ¥{t:,.0f} | {share} | {q:,.0f} | {unit} |")
    print()
    # 原価が部門で取れるか（theory_cost を部門で引けるか）確認
    with get_warehouse(load_settings()) as wh:
        cost_rows=agg(wh,"food_theory_cost",date(2026,8,1),date(2026,9,1),("product_category",))
    print("## 部門別の原価(食)を引けるか:", "引ける" if cost_rows else "引けない（原価は店単位のみ）")
    for r in cost_rows[:5]:
        print("  ", r.get('product_category'), r['value'])
    return 0
raise SystemExit(main())
