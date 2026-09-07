"""
FW（Foodist Journal / HASSIN）共有シート → f_actuals の集約。

取り込み元:
    既存パイプライン（infomart_automation の foodist_journal.py）が、FW の
    「損益管理 → 実績管理業務 → 店長会資料DL」で落とした Excel を解析して
    書き込んでいる Google スプレッドシート。指標ごとにタブが分かれ、
    各行は  A:年月(YYYY-MM) / B:店舗名 / C:金額 / D:種別(中間|確定) / E:取込日時。

方針:
    * 既存パイプラインには一切触らず、書き込みもしない。出来上がった結果を読むだけ。
    * 店名は store_code へ寄せる。解決できない店名は黙って捨てず、結果に載せて報告する。
    * 現時点で取れるのは月次のみなので grain='month'、date はその月の1日。
      時間帯別売上・ABC分析が取れるようになったら、同じ f_actuals に
      grain='hour' / product_* 付きで足すだけで済む。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..model import (
    GRAIN_MONTH,
    KIND_FINAL,
    KIND_INTERIM,
    METRIC_DRINK_BUDGET_COST,
    METRIC_DRINK_PURCHASE,
    METRIC_DRINK_SALES,
    METRIC_DRINK_THEORY_COST,
    METRIC_FOOD_BUDGET_COST,
    METRIC_FOOD_PURCHASE,
    METRIC_FOOD_SALES,
    METRIC_FOOD_THEORY_COST,
    METRIC_SALES,
    ActualRow,
)
from ..normalize import normalize_text, parse_amount, parse_year_month
from ..stores import StoreMaster
from .sheets_client import SheetReader

SOURCE = "fw_sheet"

# 共有シートのタブ名 → このシステムの指標。
# 新しい指標タブが増えたら、ここに1行足すだけで取り込み対象に入る。
TAB_TO_METRIC: dict[str, str] = {
    "売上": METRIC_SALES,
    "F売上": METRIC_FOOD_SALES,
    "D売上": METRIC_DRINK_SALES,
    "F食材費仕入": METRIC_FOOD_PURCHASE,
    "D飲料費仕入": METRIC_DRINK_PURCHASE,
    "フード理論原価": METRIC_FOOD_THEORY_COST,
    "ドリンク理論原価": METRIC_DRINK_THEORY_COST,
    "F予算": METRIC_FOOD_BUDGET_COST,
    "D予算": METRIC_DRINK_BUDGET_COST,
}

VALID_KINDS = {KIND_INTERIM, KIND_FINAL}

# ヘッダー行が入っていた場合に読み飛ばすための目印
_HEADER_MARKERS = {"年月", "店舗名", "金額"}


@dataclass
class IngestReport:
    """取り込み結果。件数だけでなく、取りこぼしを必ず可視化する。"""

    rows_read: int = 0
    rows_built: int = 0
    rows_loaded: int = 0
    tabs_seen: list[str] = field(default_factory=list)
    tabs_missing: list[str] = field(default_factory=list)
    unknown_stores: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """取りこぼしゼロで取り込めたか。"""
        return not self.unknown_stores and not self.tabs_missing

    def summary(self) -> str:
        lines = [
            f"読み取り {self.rows_read} 行 / 変換 {self.rows_built} 行 / 投入 {self.rows_loaded} 行",
            f"タブ: 取得 {len(self.tabs_seen)} / 欠落 {len(self.tabs_missing)}",
        ]
        if self.tabs_missing:
            lines.append("  欠落タブ: " + ", ".join(self.tabs_missing))
        if self.unknown_stores:
            detail = ", ".join(f"{n}({c}行)" for n, c in sorted(self.unknown_stores.items()))
            lines.append(f"  ⚠ 店舗マスタに無い店名: {detail}")
        if self.skipped:
            lines.append(f"  スキップ {len(self.skipped)} 行（先頭: {self.skipped[0]}）")
        return "\n".join(lines)


def _is_header(row: list) -> bool:
    return bool(row) and normalize_text(row[0]) in _HEADER_MARKERS


def build_rows(
    reader: SheetReader,
    master: StoreMaster,
    *,
    year_months: set[str] | None = None,
    report: IngestReport | None = None,
) -> tuple[list[ActualRow], IngestReport]:
    """
    共有シートを読んで ``ActualRow`` の列を組み立てる。

    year_months を渡すと、その年月（'2026-08' 形式）だけに絞る。
    """
    result = report or IngestReport()
    available = set(reader.tab_names())
    ingested_at = datetime.now(timezone.utc)
    rows: list[ActualRow] = []

    for tab, metric in TAB_TO_METRIC.items():
        if tab not in available:
            result.tabs_missing.append(tab)
            continue
        result.tabs_seen.append(tab)

        for index, raw in enumerate(reader.values(tab), start=1):
            if not raw or _is_header(raw):
                continue
            result.rows_read += 1

            # A:年月 B:店舗名 C:金額 D:種別 E:取込日時
            padded = list(raw) + [""] * (5 - len(raw))
            year_month, store_name, amount, kind = padded[0], padded[1], padded[2], padded[3]

            if not normalize_text(year_month) or not normalize_text(store_name):
                result.skipped.append(f"[{tab}] {index}行目: 年月または店舗名が空")
                continue

            try:
                period = parse_year_month(year_month)
            except ValueError as exc:
                result.skipped.append(f"[{tab}] {index}行目: {exc}")
                continue

            if year_months is not None and period.strftime("%Y-%m") not in year_months:
                continue

            store = master.find_by_name(store_name)
            if store is None:
                key = normalize_text(store_name)
                result.unknown_stores[key] = result.unknown_stores.get(key, 0) + 1
                continue
            if not store.active:
                continue

            kind_value = normalize_text(kind)
            rows.append(
                ActualRow(
                    store_code=store.store_code,
                    date=period,
                    grain=GRAIN_MONTH,
                    metric=metric,
                    value=parse_amount(amount),
                    kind=kind_value if kind_value in VALID_KINDS else None,
                    source=SOURCE,
                    ingested_at=ingested_at,
                )
            )

    result.rows_built = len(rows)
    return rows, result


def ingest(
    reader: SheetReader,
    master: StoreMaster,
    warehouse,
    *,
    year_months: set[str] | None = None,
    strict: bool = True,
) -> IngestReport:
    """
    共有シートを読み、f_actuals へ冪等に投入する。

    strict=True のとき、店舗マスタに無い店名やタブの欠落があれば例外にする。
    取りこぼしを「0件成功」として通してしまうと、画面には出ないのに
    正常終了した扱いになり気づけないため。
    """
    rows, report = build_rows(reader, master, year_months=year_months)

    if strict and not report.ok:
        raise RuntimeError("FW共有シートの取り込みに取りこぼしがあります:\n" + report.summary())

    warehouse.ensure_schema()
    # 取れた店・取れた指標だけを入れ替える。
    # 毎日走る主経路。--month 指定が無いと全履歴が削除対象になる。ある月に売上タブ
    # だけ更新され、他タブがその月ぶん空だと、同じ (source, grain, date) を共有する
    # 理論原価・仕入・F/D売上・予算が全店ぶん消える。原価率の分子はここから来る。
    report.rows_loaded = warehouse.replace_actuals(
        rows, scope_stores=True, scope_metrics=True
    )
    return report
