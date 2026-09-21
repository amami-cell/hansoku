"""
POS取込シートの「POS売上」タブ → f_actuals。

取り込み元:
    pos-sync（別リポジトリ）が新Uレジ／ダイニーの管理画面から取得して書き込む
    タブ。**FW共有シートとは別のスプレッドシート**なので、`POS_SPREADSHEET_ID`
    で場所を指定する。

    列: 年月 / 店舗名 / 売上 / フード原価 / ドリンク原価 / 客数 / 備考 /
        取込日時 / POS / 店舗コード / 原価

なぜ要るか:
    FWに連動していない店は、FW共有シートにもインフォマートにも載らない。
    24店のうち **1766 ぎふや福岡天神** がこれに当たり、売上すら 0 のまま
    画面に出ていた（2026-09-21 実測）。この店の数字はPOSから直接取るしかない。

**FW連動店は取り込まない。**
    シートにはダイニー店（1111 / 1151 / 1163）の行も入るが、この3店は
    FWからも売上が入っている。両方入れると**二重計上**になる。
    店舗マスタの `pos` が `fw` の店は、行があっても無視する。

列は**名前で引く**。位置で引かない:
    書き込み側は列を末尾に足していく運用（`原価` が後から増えた）。
    位置で決め打ちすると、列が増えた月から静かにずれる。
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..model import GRAIN_MONTH, KIND_FINAL, METRIC_COVERS, METRIC_SALES, ActualRow
from ..normalize import normalize_text, parse_amount, parse_year_month
from ..stores import StoreMaster
from .fw_sheet import IngestReport
from .sheets_client import SheetReader

SOURCE = "pos_sheet"
TAB = "POS売上"


def looks_broken(report: IngestReport) -> bool:
    """**この取り込みにとっての異常**を判定する。

    `IngestReport.ok` は使えない。あちらは `unknown_stores` が空であることを
    求めるが、このタブにはよその会社の店が入るのが正常だから
    （実測で15行中12行が他社）。タブが無い・行を読み飛ばした、を異常とする。

    中断の判断と終了コードで**同じ関数を使う**こと。片方だけ直すと、
    「データは入ったのにジョブは赤」になる（実際そうなった）。
    """
    return bool(report.tabs_missing or report.skipped)

# 見出し名 → 指標。**原価はまだ入れない。**
# 新Uレジの材料原価は「原価・販管費登録」への手入力が元で、実測（2026-08）では
# 未登録のため 0 だった。取れていない値の経路を先に作っても確かめようがない。
# 登録が始まってから、実データを見て足すこと。
HEADER_TO_METRIC: dict[str, str] = {
    "売上": METRIC_SALES,
    "客数": METRIC_COVERS,
}

_HEADER_MARKERS = {"年月", "店舗名"}


def build_rows(
    reader: SheetReader,
    master: StoreMaster,
    *,
    year_months: set[str] | None = None,
) -> tuple[list[ActualRow], IngestReport]:
    """「POS売上」タブを読んで ``ActualRow`` の列を組み立てる。"""
    report = IngestReport()
    if TAB not in set(reader.tab_names()):
        report.tabs_missing.append(TAB)
        return [], report
    report.tabs_seen.append(TAB)

    ingested_at = datetime.now(timezone.utc)
    rows: list[ActualRow] = []
    header: list[str] | None = None

    for index, raw in enumerate(reader.values(TAB), start=1):
        if not raw:
            continue
        cells = [normalize_text(c) for c in raw]
        if header is None:
            if cells and cells[0] in _HEADER_MARKERS:
                header = cells
                continue
            # 見出しが無いシートは形が違う。位置で当てずっぽうに読まない。
            report.skipped.append(f"[{TAB}] 見出し行（年月/店舗名…）が見つからない")
            return [], report

        report.rows_read += 1
        at = {name: (cells[i] if i < len(cells) else "") for i, name in enumerate(header)}

        year_month, store_name = at.get("年月", ""), at.get("店舗名", "")
        if not year_month or not store_name:
            report.skipped.append(f"[{TAB}] {index}行目: 年月または店舗名が空")
            continue
        try:
            period = parse_year_month(year_month)
        except ValueError as exc:
            report.skipped.append(f"[{TAB}] {index}行目: {exc}")
            continue
        if year_months is not None and period.strftime("%Y-%m") not in year_months:
            continue

        store = _find_store(master, at.get("店舗コード", ""), store_name)
        if store is None:
            # よその会社の店（ダイニーのアカウントは90店舗ぶん見えている）。
            # 書き込み側でも除いているが、ここでも弾く。
            key = normalize_text(store_name)
            report.unknown_stores[key] = report.unknown_stores.get(key, 0) + 1
            continue
        if not store.active:
            continue
        if (store.pos or "fw") == "fw":
            # FWから同じ売上が入る。両方入れると二重計上になる。
            continue

        for name, metric in HEADER_TO_METRIC.items():
            text = at.get(name, "")
            if not text:
                continue      # 空欄は「未取得」。0 として入れない
            rows.append(
                ActualRow(
                    store_code=store.store_code,
                    date=period,
                    grain=GRAIN_MONTH,
                    metric=metric,
                    value=parse_amount(text),
                    # **確定として入れる。** 集計の採用順は
                    #   ① 確定かどうか → ② SOURCE_PRIORITY → ③ 取込日時
                    # で、kind が先に効く。付け忘れると、FWが書いた「確定の 0」に
                    # 無条件で負けて、優先順位を上げても出番が来ない（実際そうなった）。
                    # 締まった月のPOSの数字なので確定で正しい。
                    kind=KIND_FINAL,
                    source=SOURCE,
                    ingested_at=ingested_at,
                )
            )

    report.rows_built = len(rows)
    return rows, report


def _find_store(master: StoreMaster, code: str, name: str):
    """店舗コード優先で引く。無ければ店舗名で引く。

    コードは書き込み側（pos-sync）が店舗マスタを見て入れている。
    名前だけだと表記ゆれに弱いので、入っていればそちらを信じる。"""
    code = normalize_text(code)
    if code:
        for store in master.all:
            if store.store_code == code:
                return store
    return master.find_by_name(name)


def ingest(
    reader: SheetReader,
    master: StoreMaster,
    warehouse,
    *,
    year_months: set[str] | None = None,
    strict: bool = True,
) -> IngestReport:
    """「POS売上」タブを読み、f_actuals へ冪等に投入する。"""
    rows, report = build_rows(reader, master, year_months=year_months)

    # **マスタに無い店は「取りこぼし」ではない。** このタブにはよその会社の店が
    # 大量に入る（ダイニーのアカウントは90店舗ぶん見えている）ので、
    # unknown_stores があることは正常。他の取り込みと違って中断の理由にしない。
    # タブが無い・行を読み飛ばした、のほうが異常なのでそちらで判断する。
    if strict and looks_broken(report):
        raise RuntimeError("POS売上の取り込みに取りこぼしがあります:\n" + report.summary())
    if report.unknown_stores:
        print(f"[{TAB}] マスタに無い店 {len(report.unknown_stores)}件は飛ばしました"
              "（よその会社の店。ダイニーは90店舗ぶん見えています）")

    warehouse.ensure_schema()
    # 取れた店・取れた指標だけを入れ替える。FW連動店の売上を巻き添えにしない。
    report.rows_loaded = warehouse.replace_actuals(
        rows, scope_stores=True, scope_metrics=True
    )
    return report
