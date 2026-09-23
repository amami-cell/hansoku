"""ルクアLargo（1160）2026 の販促目標(KPI)を promo_targets へ取り込む。

スプレッドシート「日別スケジュールlargo（実績一覧）」の KPI（区分×月）を、
施策ごとに「その販売期間がまたぐ月のKPI合計」を売上目標(metric=sales)にして入れる。
目標の鍵は schedule.yaml の id に開始年を付けた `id@YYYY`（アプリと同じ）。

実行:
  HANSOKU_ENV=cloud NEON_DATABASE_URL=... python scripts/import_largo_targets.py         # 予定を表示（書き込まない）
  HANSOKU_ENV=cloud NEON_DATABASE_URL=... python scripts/import_largo_targets.py --apply  # 実際に書き込む

いつでも冪等（同じ鍵は上書き）。set_by に出所を残す。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# repo ルートを import パスに追加（`python scripts/xxx.py` 実行でも hansoku を読めるように）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 施策(id@開始年) → 売上目標(円)。KPI(区分×月)を販売期間がまたぐ月で合計したもの。
# 月の割り当ては「その月の見出し販促」＝シートの区分×月の1枠に対応（重なる月は開始側の販促に寄せる）。
TARGETS: dict[str, int] = {
    # ── ケーキ ──
    "r1160-cake-2603@2026": 2_450_000,   # 3/19-4/30 ミルクレープ切替  (3月130万+4月115万)
    "r1160-cake-2605@2026": 2_350_000,   # 5/1-6/30 2種新規2種既存    (5月120万+6月115万)
    "r1160-cake-2607@2026": 1_300_000,   # 7/1-8/31 夏ケーキ          (7月75万+8月55万)
    "r1160-cake-2609@2026": 1_950_000,   # 9/1-11/30 秋ケーキ4種       (9月60万+10月70万+11月65万)
    "r1160-cake-2612@2026":   800_000,   # 12/1-12/25 クリスマス       (12月80万)
    "r1160-cake-2701@2026": 1_600_000,   # 12/26-1/31 既存より選定     (2027.1 160万)
    "r1160-cake-2702@2027": 1_700_000,   # 2/1-2/14 バレンタイン⇒いちご (2027.2 170万)
    # ── 季節パフェ ──
    "r1160-parfait-2603@2026": 3_100_000,  # 3/2-5/7 苺パフェ2種       (3月190万+4月120万)
    "r1160-parfait-2605@2026": 1_550_000,  # 5/8-7/12 メロン           (5月75万+6月80万)
    "r1160-parfait-2607@2026": 7_100_000,  # 7/1-9/15 スノー           (7月200万+8月380万+9月130万)
    "r1160-parfait-2609@2026":   700_000,  # 9/16-10/31 いちじく        (10月70万)
    "r1160-parfait-2611@2026":   950_000,  # 11/1-11/30 シャイン→ルレクチェ (11月95万)
    "r1160-parfait-2612@2026":   700_000,  # 12/1-12/25 ノエル          (12月70万)
    "r1160-parfait-2701@2026":   500_000,  # 12/26-1/31 みかん          (2027.1 50万)
    "r1160-parfait-2702@2027": 1_250_000,  # 2/1- 苺                   (2027.2 125万)
    # ── コラボ ──
    "r1160-collab-kaeru@2026":   400_000,  # 6/12-6/25 かえるのピクルス (6月40万)
    "r1160-collab-hakuto26@2026": 360_000, # 7/21-7/26 白桃DAYS        (7月36万)
    # 熊本コラボ・ハロウィンは KPI 未設定 → 目標なし
}

SET_BY = "import:largo2026"


def main() -> int:
    apply = "--apply" in sys.argv
    os.environ.setdefault("HANSOKU_ENV", "cloud")
    from hansoku.db import get_appdb

    db = get_appdb()
    before = db.list_promo_targets("sales")
    print(f"# ルクアLargo 2026 目標取込（{'APPLY' if apply else 'DRY-RUN'}）  対象 {len(TARGETS)} 件")
    changed = 0
    for key, val in TARGETS.items():
        cur = before.get(key)
        mark = "＝" if cur == val else ("←" if cur is None else "↑↓")
        print(f"  {key:34s} {mark} ¥{val:,}" + ("" if cur is None else f"  (現在 ¥{cur:,})"))
        if apply and cur != val:
            db.set_promo_target(key, val, set_by=SET_BY, metric="sales")
            changed += 1
    if apply:
        print(f"\n書き込み {changed} 件（残りは既に同値）。")
    else:
        print("\nDRY-RUN。実際に書き込むには --apply を付けて実行してください。")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
