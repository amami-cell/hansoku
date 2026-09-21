"""
POS取込シート（POS売上タブ）の取り込み。

ここで守りたいのは2つ。
  * **二重計上しない** … FW連動店はFWからも売上が入る。両方入れたら倍になる
  * **0で埋めない** … 空欄は「未取得」。0を入れると売上0の月として混ざる
"""
import pytest

from hansoku.ingest.pos_sheet import SOURCE, TAB, build_rows
from hansoku.ingest.sheets_client import FixtureSheetReader
from hansoku.model import GRAIN_MONTH, METRIC_COVERS, METRIC_SALES

HEADER = ["年月", "店舗名", "売上", "フード原価", "ドリンク原価", "客数",
          "備考", "取込日時", "POS", "店舗コード", "原価"]


def rd(*rows):
    return FixtureSheetReader({TAB: [HEADER, *rows]})


def row(ym="2026-08", name="ぎふや 福岡天神店", sales="12640890", covers="4338",
        pos="uleji", code="1766", cost=""):
    return [ym, name, sales, "", "", covers, "", "2026-09-21 05:05:04", pos, code, cost]


class Test取り込み:
    def test_FW未連動店の売上と客数が入る(self, master):
        rows, report = build_rows(rd(row()), master)
        got = {(r.metric, r.value) for r in rows}
        assert got == {(METRIC_SALES, 12640890.0), (METRIC_COVERS, 4338.0)}
        assert all(r.store_code == "1766" for r in rows)
        assert all(r.grain == GRAIN_MONTH and r.source == SOURCE for r in rows)

    def test_FW連動店は取り込まない(self, master):
        # 1151 NagaGutsu は pos: fw。FWからも売上が入るので、ここで入れると倍になる。
        rows, _ = build_rows(rd(row(name="NagaGutsu", code="1151", pos="dinii")), master)
        assert rows == []

    def test_よその会社の店は取り込まない(self, master):
        rows, report = build_rows(rd(row(name="喰人梅田東通り店", code="")), master)
        assert rows == []
        assert report.unknown_stores

    def test_空欄は0にしない(self, master):
        # 売上だけ取れて客数が空、ということが起きる。0を入れると客数0の月になる。
        rows, _ = build_rows(rd(row(covers="")), master)
        assert {r.metric for r in rows} == {METRIC_SALES}

    def test_原価はまだ取り込まない(self, master):
        # 新Uレジの材料原価は未登録で0が返る（2026-08 実測）。
        # 取れていない値の経路を先に作っても確かめようがないので入れない。
        rows, _ = build_rows(rd(row(cost="0")), master)
        assert {r.metric for r in rows} == {METRIC_SALES, METRIC_COVERS}

    def test_対象月で絞れる(self, master):
        reader = rd(row(ym="2026-07"), row(ym="2026-08"))
        rows, _ = build_rows(reader, master, year_months={"2026-08"})
        assert {r.date.strftime("%Y-%m") for r in rows} == {"2026-08"}

    def test_店舗コードが空でも店舗名で引く(self, master):
        rows, _ = build_rows(rd(row(code="")), master)
        assert {r.store_code for r in rows} == {"1766"}


class Test形が違うとき:
    def test_タブが無ければ何も作らない(self, master):
        rows, report = build_rows(FixtureSheetReader({}), master)
        assert rows == [] and TAB in report.tabs_missing

    def test_見出しが無ければ位置で当てずっぽうに読まない(self, master):
        # 列は末尾に増える運用。見出しを頼りにしないと、増えた月からずれる。
        reader = FixtureSheetReader({TAB: [["2026-08", "ぎふや 福岡天神店", "123"]]})
        rows, report = build_rows(reader, master)
        assert rows == [] and report.skipped

    def test_列が増えても名前で引ける(self, master):
        extra = FixtureSheetReader({TAB: [HEADER + ["新しい列"],
                                          row() + ["なにか"]]})
        rows, _ = build_rows(extra, master)
        assert {r.metric for r in rows} == {METRIC_SALES, METRIC_COVERS}

    def test_年月が壊れていても他の行は生きる(self, master):
        reader = rd(row(ym="こわれ"), row(ym="2026-08"))
        rows, report = build_rows(reader, master)
        assert {r.date.strftime("%Y-%m") for r in rows} == {"2026-08"}
        assert report.skipped


class Test読む列の範囲:
    """**列の範囲は最後の列で決まる。** 広げ忘れると、その列だけ黙って空になる。

    実際に踏んだ: 既定の A:E のままだったので POS売上タブの `客数`（F列）が
    読めず、売上だけが入った。**エラーにならない**ので気づきにくい。
    """

    def test_既定はE列まで(self):
        # 既存の取り込み（FWタブ・月次集計）はE列で収まる。挙動を変えない。
        from hansoku.ingest.sheets_client import sheet_range

        assert sheet_range("月次集計") == "'月次集計'!A:E"

    def test_広げられる(self):
        from hansoku.ingest.sheets_client import sheet_range

        assert sheet_range("POS売上", "Z") == "'POS売上'!A:Z"

    def test_POS売上はE列より右を使う(self):
        # 客数はF列。ここが E のままだと取れない。
        from hansoku.ingest.pos_sheet import HEADER_TO_METRIC

        assert HEADER_TO_METRIC["客数"]
        assert HEADER.index("客数") > HEADER.index("フード原価")


class Test中断の判断:
    """**マスタに無い店は取りこぼしではない。** このタブにはよその会社の店が
    大量に入るのが正常。他の取り込みと同じ基準で止めると、毎回失敗する。"""

    def test_よその店があっても中断しない(self, master, warehouse):
        from hansoku.ingest.pos_sheet import ingest

        reader = rd(row(), row(name="喰人梅田東通り店", code=""))
        report = ingest(reader, master, warehouse, strict=True)
        assert report.unknown_stores          # 記録はする
        assert report.rows_loaded == 2        # 1766 の売上・客数は入る

    def test_タブが無ければ中断する(self, master, warehouse):
        import pytest as _pytest

        from hansoku.ingest.pos_sheet import ingest

        with _pytest.raises(RuntimeError):
            ingest(FixtureSheetReader({}), master, warehouse, strict=True)


class Test終了コード:
    """**中断の判断と終了コードで同じ基準を使う。** 片方だけ直すと
    「データは入ったのにジョブは赤」になる（実際そうなった）。"""

    def test_よその店だけなら異常ではない(self):
        from hansoku.ingest.fw_sheet import IngestReport
        from hansoku.ingest.pos_sheet import looks_broken

        r = IngestReport(rows_read=15, rows_built=2, rows_loaded=2)
        r.unknown_stores["喰人梅田東通り店"] = 1
        assert not r.ok           # 既存の基準では「取りこぼしあり」
        assert not looks_broken(r)  # この取り込みでは正常

    def test_タブ欠落と読み飛ばしは異常(self):
        from hansoku.ingest.fw_sheet import IngestReport
        from hansoku.ingest.pos_sheet import looks_broken

        assert looks_broken(IngestReport(tabs_missing=["POS売上"]))
        assert looks_broken(IngestReport(skipped=["3行目: 年月が空"]))


class Test取り込み元の優先順位:
    """**FW側はFW未連動店に 0 を書く。** 既定の順位だとその 0 が実数を
    押しのける。実測で 1766 の 2026-08 が 0 のままだった（投入は成功していた）。"""

    def test_pos_sheetがFWより優先される(self):
        from hansoku.db.warehouse import SOURCE_PRIORITY, _SOURCE_PRIORITY_DEFAULT

        # 数字が小さいほど優先
        assert SOURCE_PRIORITY["pos_sheet"] < SOURCE_PRIORITY["fw_sheet"]
        assert SOURCE_PRIORITY["pos_sheet"] < SOURCE_PRIORITY["fw_uriage_suii"]
        assert SOURCE_PRIORITY["pos_sheet"] < _SOURCE_PRIORITY_DEFAULT

    def test_FW連動店にはpos_sheetの行を作らないので影響しない(self, master):
        # 最優先にしても安全なのは、FW連動店に行を作らないから。
        rows, _ = build_rows(rd(row(name="NagaGutsu", code="1151", pos="dinii")), master)
        assert rows == []
