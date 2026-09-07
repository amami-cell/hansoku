"""GOLD京都ポルタ(1168) 8月半減の確認：日別売上で、月末まで入っているか見る。"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_DAY, GRAIN_MONTH
from hansoku.settings import load_settings

CODE="1168"

def daily(wh, dfrom, dto):
    rows=wh.aggregate(AggregateQuery(date_from=dfrom, date_to=dto, grain=GRAIN_DAY,
        metrics=["sales"], store_codes=[CODE], group_by=("date",)))
    return sorted((r["date"], r["value"] or 0) for r in rows)

def month_sales(wh, dfrom, dto):
    rows=wh.aggregate(AggregateQuery(date_from=dfrom, date_to=dto, grain=GRAIN_MONTH,
        metrics=["sales"], store_codes=[CODE], group_by=("store_code",)))
    return sum(r["value"] or 0 for r in rows)

def main():
    s=load_settings()
    with get_warehouse(s) as wh:
        jul=daily(wh, date(2026,7,1), date(2026,7,31))
        aug=daily(wh, date(2026,8,1), date(2026,8,31))
        # 月次(月別日別売上推移=uriage_suii)側の月合計も比較
        jul_m=month_sales(wh, date(2026,7,1), date(2026,7,31))
        aug_m=month_sales(wh, date(2026,8,1), date(2026,8,31))
    for label, d, m in [("2026-07",jul,jul_m),("2026-08",aug,aug_m)]:
        days=[x for x in d if x[1]]
        total=sum(v for _,v in d)
        print(f"## {label}  日別の入っている日数={len(days)}  日別合計=¥{total:,.0f}  月次側合計=¥{m:,.0f}")
        if days:
            print(f"  最初の日: {days[0][0]}  最後の日: {days[-1][0]}")
        # 全日を出す
        for dt,v in d:
            print(f"   {dt}  ¥{v:,.0f}")
        print()
    return 0
raise SystemExit(main())
