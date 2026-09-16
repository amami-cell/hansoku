"""
インフォマート棚卸（共有シートの「月次集計」タブ）→ f_actuals の集約。

取り込み元:
    既存パイプライン（infomart_automation の spreadsheet.py）が、発注インフォマートの
    「取引先別棚卸高」を取得して書き込んでいるタブ。FWと同じスプレッドシートに
    相乗りしている。

    列: 年月 / 店舗名 / フード / ドリンク / 備品
    ※ FWのタブ（1行1指標）と違い、1行に3指標が横に並ぶ

注意:
    店名が**インフォマート形式**（全角・「（ＨＡＳＳＩＮ）」付き・コード無し）で、
    FWタブの "0001015_すさび湯 歌舞伎町" とは別表記。
    店舗マスタの infomart_name で突き合わせる。

    棚卸高そのものは「その月末に残っている在庫の金額」であり、原価ではない。
    実原価は 前月棚卸 + 当月仕入 - 当月棚卸 で求まる。
    ここでは素の棚卸高を入れ、原価の計算は集計層で行う。
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..model import (
    GRAIN_MONTH,
    METRIC_DRINK_INVENTORY,
    METRIC_FOOD_INVENTORY,
    METRIC_SUPPLY_INVENTORY,
    ActualRow,
)
from ..normalize import normalize_text, parse_amount, parse_year_month
from ..stores import StoreMaster
from .fw_sheet import IngestReport
from .sheets_client import SheetReader

SOURCE = "infomart_sheet"
TAB = "月次集計"

# 列の位置（0始まり） → 指標
COLUMN_TO_METRIC: dict[int, str] = {
    2: METRIC_FOOD_INVENTORY,
    3: METRIC_DRINK_INVENTORY,
    4: METRIC_SUPPLY_INVENTORY,
}

_HEADER_MARKERS = {"年月", "店舗名"}


def build_rows(
    reader: SheetReader,
    master: StoreMaster,
    *,
    year_months: set[str] | None = None,
) -> tuple[list[ActualRow], IngestReport]:
    """「月次集計」タブを読んで ``ActualRow`` の列を組み立てる。"""
    report = IngestReport()
    if TAB not in set(reader.tab_names()):
        report.tabs_missing.append(TAB)
        return [], report
    report.tabs_seen.append(TAB)

    ingested_at = datetime.now(timezone.utc)
    rows: list[ActualRow] = []

    for index, raw in enumerate(reader.values(TAB), start=1):
        if not raw or normalize_text(raw[0]) in _HEADER_MARKERS:
            continue
        report.rows_read += 1

        padded = list(raw) + [""] * (5 - len(raw))
        year_month, store_name = padded[0], padded[1]

        if not normalize_text(year_month) or not normalize_text(store_name):
            report.skipped.append(f"[{TAB}] {index}行目: 年月または店舗名が空")
            continue

        try:
            period = parse_year_month(year_month)
        except ValueError as exc:
            report.skipped.append(f"[{TAB}] {index}行目: {exc}")
            continue

        if year_months is not None and period.strftime("%Y-%m") not in year_months:
            continue

        store = master.find_by_name(store_name)
        if store is None:
            key = normalize_text(store_name)
            report.unknown_stores[key] = report.unknown_stores.get(key, 0) + 1
            continue
        if not store.active:
            # 閉店店。稼働店の集計には載せないが、黙って消さず記録に残す
            # （resolved 件数と出力行数が食い違わないようにするためでもある）。
            report.skipped.append(
                f"[{TAB}] {index}行目: {store.store_code} は閉店（稼働店では無い）"
            )
            continue

        for column, metric in COLUMN_TO_METRIC.items():
            rows.append(
                ActualRow(
                    store_code=store.store_code,
                    date=period,
                    grain=GRAIN_MONTH,
                    metric=metric,
                    value=parse_amount(padded[column]),
                    source=SOURCE,
                    ingested_at=ingested_at,
                )
            )

    report.rows_built = len(rows)
    return rows, report


def ingest(
    reader: SheetReader,
    master: StoreMaster,
    warehouse,
    *,
    year_months: set[str] | None = None,
    strict: bool = True,
) -> IngestReport:
    """「月次集計」タブを読み、f_actuals へ冪等に投入する。"""
    rows, report = build_rows(reader, master, year_months=year_months)

    if strict and not report.ok:
        raise RuntimeError("インフォマート棚卸の取り込みに取りこぼしがあります:\n" + report.summary())

    warehouse.ensure_schema()
    # 取れた店・取れた指標だけを入れ替える。
    # 同上。棚卸が一部の店・一部の指標しか取れなかった月に、既存を巻き添えにしない。
    report.rows_loaded = warehouse.replace_actuals(
        rows, scope_stores=True, scope_metrics=True
    )
    return report
