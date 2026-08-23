"""
FW共有シート → f_actuals の取り込み。

Phase 0 の完了基準そのもの: 実績が正しく集約され、何度流しても壊れないこと。
"""
from datetime import date

import pytest

from hansoku.ingest.fw_sheet import SOURCE, TAB_TO_METRIC, build_rows, ingest
from hansoku.model import GRAIN_MONTH


class Test取り込み:
    def test_9つの指標タブを全て取り込む(self, reader, master):
        rows, report = build_rows(reader, master)
        assert len(report.tabs_seen) == 9
        assert report.tabs_missing == []
        assert {r.metric for r in rows} == set(TAB_TO_METRIC.values())

    def test_月次として月初日に載る(self, reader, master):
        rows, _ = build_rows(reader, master)
        assert all(r.grain == GRAIN_MONTH for r in rows)
        assert all(r.date.day == 1 for r in rows)

    def test_集約元が記録される(self, reader, master):
        rows, _ = build_rows(reader, master)
        assert all(r.source == SOURCE for r in rows)

    def test_店名がstore_codeへ寄せられる(self, reader, master):
        rows, _ = build_rows(reader, master)
        codes = {r.store_code for r in rows}
        assert codes <= set(master.active_codes)
        assert "1015" in codes


class Test取りこぼしの可視化:
    """取りこぼしを「0件成功」で通さないこと。"""

    def test_マスタに無い店名は報告される(self, reader, master):
        _, report = build_rows(reader, master)
        assert report.unknown_stores
        assert not report.ok

    def test_壊れた行はスキップして報告される(self, reader, master):
        _, report = build_rows(reader, master)
        assert any("年月" in s for s in report.skipped)

    def test_strictなら取りこぼしで中断する(self, reader, master, warehouse):
        with pytest.raises(RuntimeError, match="取りこぼし"):
            ingest(reader, master, warehouse, strict=True)

    def test_strictで中断したときは書き込まない(self, reader, master, warehouse):
        with pytest.raises(RuntimeError):
            ingest(reader, master, warehouse, strict=True)
        assert warehouse.query("SELECT count(*) c FROM f_actuals")[0]["c"] == 0

    def test_lenientなら取りこぼしを報告しつつ続行する(self, reader, master, warehouse):
        report = ingest(reader, master, warehouse, strict=False)
        assert report.rows_loaded > 0
        assert report.unknown_stores


class Test冪等性:
    def test_何度取り込んでも行数が増えない(self, reader, master, warehouse):
        counts = []
        for _ in range(3):
            ingest(reader, master, warehouse, strict=False)
            counts.append(warehouse.query("SELECT count(*) c FROM f_actuals")[0]["c"])
        assert len(set(counts)) == 1
        assert counts[0] > 0

    def test_年月を絞って取り込める(self, reader, master, warehouse):
        report = ingest(reader, master, warehouse, year_months={"2026-08"}, strict=False)
        dates = warehouse.query("SELECT DISTINCT date FROM f_actuals")
        assert [d["date"] for d in dates] == [date(2026, 8, 1)]
        # 24店 × 9指標 × (中間 + 確定)
        assert report.rows_loaded == 24 * 9 * 2

    def test_特定月の再取り込みは他の月を消さない(self, reader, master, warehouse):
        ingest(reader, master, warehouse, strict=False)
        before = warehouse.query("SELECT count(*) c FROM f_actuals")[0]["c"]
        ingest(reader, master, warehouse, year_months={"2026-08"}, strict=False)
        after = warehouse.query("SELECT count(*) c FROM f_actuals")[0]["c"]
        assert after == before
