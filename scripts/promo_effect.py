"""ルクアLargo(1160) 理論値の確認：
 (1) 店全体の理論原価率(cost_rate)の月次推移＝ポーションダウンの全体影響
 (2) 部門(dept)データがあるか。あれば デザート部門の 売上/数量/原価率 月次。
"""
from __future__ import annotations
import calendar, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hansoku.db import AggregateQuery, get_warehouse
from hansoku.analytics import ratio
from hansoku.model import GRAIN_MONTH
from hansoku.settings import load_settings

CODE="1160"
def mr(y,m): return date(y,m,1), date(y,m,calendar.monthrange(y,m)[1])
MONTHS=[("2026-05",*mr(2026,5)),("2026-06",*mr(2026,6)),("2026-07",*mr(2026,7)),
        ("2026-08",*mr(2026,8)),("2025-08",*mr(2025,8))]

def main():
    s=load_settings()
    with get_warehouse(s) as wh:
        print("## 店全体の理論原価率（cost_rate）月次")
        for label,a,b in MONTHS:
            rows=ratio(wh,"cost_rate",date_from=a,date_to=b,store_codes=[CODE],grain=GRAIN_MONTH)
            v=next((r.value for r in rows if r.store_code==CODE), None)
            print(f"- {label}: {v*100:.1f}%" if v is not None else f"- {label}: —（未取得）")
        print()
        print("## 部門(dept_sales) が取れるか：2026-08 の部門一覧")
        a,b=mr(2026,8)
        deptrows=wh.aggregate(AggregateQuery(date_from=a,date_to=b,grain=GRAIN_MONTH,
            metrics=["dept_sales"],store_codes=[CODE],group_by=("product_name","product_category")))
        if not deptrows:
            print("  → 部門データなし（1160はABC部門未取込。全体cost_rateのみ）")
        else:
            for r in sorted(deptrows,key=lambda r:-(r["value"] or 0)):
                nm=r.get("product_name"); rate=r.get("product_category")
                print(f"  {nm}: 売上¥{(r['value'] or 0):,.0f} 原価率{rate}")
        print()
        # デザート部門の月次（あれば）
        print("## デザート部門らしき部門の月次（売上/数量/原価率）")
        for label,a,b in MONTHS:
            ds=wh.aggregate(AggregateQuery(date_from=a,date_to=b,grain=GRAIN_MONTH,
                metrics=["dept_sales"],store_codes=[CODE],group_by=("product_name","product_category")))
            dq=wh.aggregate(AggregateQuery(date_from=a,date_to=b,grain=GRAIN_MONTH,
                metrics=["dept_qty"],store_codes=[CODE],group_by=("product_name",)))
            qmap={r.get("product_name"):(r["value"] or 0) for r in dq}
            for r in ds:
                nm=r.get("product_name") or ""
                if "デザート" in nm or "パフェ" in nm or "スイーツ" in nm:
                    q=qmap.get(nm,0); v=r["value"] or 0; rate=r.get("product_category")
                    print(f"- {label} {nm}: 売上¥{v:,.0f} 数量{q:,.0f} 原価率{rate}")
    return 0
raise SystemExit(main())
