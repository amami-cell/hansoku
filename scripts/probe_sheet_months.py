#!/usr/bin/env python3
"""FW共有シートの損益タブが、どの年月まで遡って入っているかを調べる。読み取りのみ。

原価率（＝損益）が出ない月は、こちら側の取り込み漏れではなく「共有シートにその月が
無い」ことが多い。バックフィルの手配（上流の店長会資料DLをどの月ぶん流すか）を
正確にするため、タブごとに存在する年月と、指定チェック月の有無を出す。

環境変数 CHECK_MONTHS（カンマ区切り YYYY-MM、既定 2025-09,2025-10,2025-11,2025-12）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hansoku.ingest.fw_sheet import TAB_TO_METRIC
from hansoku.ingest.sheets_client import GoogleSheetReader
from hansoku.normalize import normalize_text, parse_year_month
from hansoku.settings import load_settings

# 損益（原価率）に効くタブだけを見る。売上は分母、理論原価は分子。
PNL_TABS = ["売上", "F売上", "D売上", "フード理論原価", "ドリンク理論原価"]
_HEADER = {"年月", "店舗名", "金額"}


def main() -> None:
    settings = load_settings()
    if not settings.sources.service_account_json:
        print("GOOGLE_SERVICE_ACCOUNT_JSON が未設定です。", file=sys.stderr)
        sys.exit(2)
    check = [m.strip() for m in os.environ.get(
        "CHECK_MONTHS", "2025-09,2025-10,2025-11,2025-12").split(",") if m.strip()]

    reader = GoogleSheetReader(settings.sources.fw_spreadsheet_id, settings.sources.service_account_json)
    tabs = set(reader.tab_names())

    print("# FW共有シート 損益タブの年月カバレッジ（読み取りのみ）")
    print("")
    print(f"チェック対象月: {', '.join(check)}")
    print("")

    for tab in PNL_TABS:
        if tab not in tabs:
            print(f"## {tab} … タブが存在しません（{TAB_TO_METRIC.get(tab, '—')}）")
            print("")
            continue
        months: dict[str, int] = {}
        stores_by_month: dict[str, set] = {}
        for raw in reader.values(tab):
            if not raw or normalize_text(raw[0]) in _HEADER:
                continue
            padded = list(raw) + [""] * 3
            ym_raw, store = padded[0], padded[1]
            if not normalize_text(ym_raw) or not normalize_text(store):
                continue
            try:
                d = parse_year_month(ym_raw)
            except ValueError:
                continue
            ym = d.strftime("%Y-%m")
            months[ym] = months.get(ym, 0) + 1
            stores_by_month.setdefault(ym, set()).add(normalize_text(store))
        keys = sorted(months)
        span = f"{keys[0]} 〜 {keys[-1]}（{len(keys)}ヶ月）" if keys else "（データなし）"
        print(f"## {tab} → {TAB_TO_METRIC.get(tab, '—')}")
        print(f"- 年月レンジ: {span}")
        # チェック月の有無（店数つき）
        for m in check:
            n = len(stores_by_month.get(m, set()))
            print(f"  - {m}: {'有り' if m in months else '無し'}（{n}店）")
        print("")

    print("## まとめ")
    print("- 「無し」の月は共有シートに損益が出ていない＝上流（店長会資料DL）のバックフィルが必要。")
    print("- 埋めたら ingest.yml を回し、python scripts/audit_cost.py で取込済みに変わることを確認。")


if __name__ == "__main__":
    main()
