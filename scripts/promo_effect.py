"""1168 の二重計上を特定：月次売上を口別に見る＋同じクエリを2回。"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings

CODE="1168"
MONTHS=[(f"2026-0{m}",date(2026,m,1),date(2026,m+1,1)) for m in range(3,9)]

def q(wh,a,b,sources=None):
    rows=wh.aggregate(AggregateQuery(date_from=a,date_to=b,grain=GRAIN_MONTH,
        metrics=["sales"],store_codes=[CODE],group_by=("store_code",),sources=sources))
    return sum(r["value"] or 0 for r in rows)

def main():
    s=load_settings()
    with get_warehouse(s) as wh:
        print("## 月次売上：全体（2回）／口別")
        print("| 月 | 全体(1回目) | 全体(2回目) | fw_uriage_suii | fw_sheet |")
        print("|---|--:|--:|--:|--:|")
        for label,a,b in MONTHS:
            t1=q(wh,a,b); t2=q(wh,a,b)
            us=q(wh,a,b,["fw_uriage_suii"]); sh=q(wh,a,b,["fw_sheet"])
            print(f"| {label} | ¥{t1:,.0f} | ¥{t2:,.0f} | ¥{us:,.0f} | ¥{sh:,.0f} |")
    return 0
raise SystemExit(main())
