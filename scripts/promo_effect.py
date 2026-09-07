"""全店の8月「おすすめ部門」数値（売上・数量・原価率）。店売上・FDは出さない。
dept_sales: product_name=部門名, product_category=原価率。dept_qty: 部門名の数量。"""
from __future__ import annotations
import calendar, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings
from hansoku.stores import StoreMaster

A=(date(2026,8,1), date(2026,8,31))
def main():
    s=load_settings(); m=StoreMaster.load(); smap={x.store_code:x for x in m.active}
    with get_warehouse(s) as wh:
        ds=wh.aggregate(AggregateQuery(date_from=A[0],date_to=A[1],grain=GRAIN_MONTH,
            metrics=["dept_sales"],group_by=("store_code","product_name","product_category")))
        dq=wh.aggregate(AggregateQuery(date_from=A[0],date_to=A[1],grain=GRAIN_MONTH,
            metrics=["dept_qty"],group_by=("store_code","product_name")))
    qmap={}
    for r in dq: qmap[(r["store_code"],r.get("product_name"))]=r["value"] or 0
    # 部門データがある店
    have=set(r["store_code"] for r in ds)
    # おすすめ部門を集める
    osu={}  # code -> list of (部門名, 売上, 原価率, 数量)
    for r in ds:
        nm=r.get("product_name") or ""
        if "おすすめ" in nm or "オススメ" in nm:
            try: rate=float(r["product_category"]) if r["product_category"] else None
            except: rate=None
            osu.setdefault(r["store_code"],[]).append(
                (nm, r["value"] or 0, rate, qmap.get((r["store_code"],nm),0)))
    print("## 部門データがある店:", len(have), "/", len(smap))
    print("## 「おすすめ部門」がある店:", len(osu))
    print()
    print("## 8月 おすすめ部門の数値（全店）")
    print("| 店 | 部門 | 売上 | 数量 | 原価率 |")
    print("|---|---|--:|--:|--:|")
    REG=['大阪','東京','京都','兵庫','福岡']
    def key(c): return (REG.index(smap[c].region) if smap[c].region in REG else 9, c)
    for c in sorted(smap, key=key):
        nmn=smap[c].store_name
        if c in osu:
            for dn,v,rate,q in sorted(osu[c],key=lambda x:-x[1]):
                cr=f"{rate:.1f}%" if rate is not None else "—"
                print(f"| {nmn} | {dn} | ¥{v:,.0f} | {q:,.0f} | {cr} |")
        elif c in have:
            print(f"| {nmn} | （おすすめ部門なし） | — | — | — |")
        else:
            print(f"| {nmn} | 部門データなし | — | — | — |")
    return 0
raise SystemExit(main())
