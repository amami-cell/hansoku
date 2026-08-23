"""
インフォマート棚卸（月次集計タブ）の取り込み。

FWのタブと形が違う点が要:
  * 1行に3指標が横に並ぶ（フード / ドリンク / 備品）
  * 店名がインフォマート形式（全角・（ＨＡＳＳＩＮ）付き・コード無し）
  * 金額に ¥ とカンマが入る
  * ヘッダー行がある
"""
import pytest

from hansoku.ingest.infomart_sheet import SOURCE, build_rows, ingest
from hansoku.model import (
    GRAIN_MONTH,
    METRIC_DRINK_INVENTORY,
    METRIC_FOOD_INVENTORY,
    METRIC_SUPPLY_INVENTORY,
)


class Test取り込み:
    def test_3つの棚卸指標が入る(self, reader, master):
        rows, _ = build_rows(reader, master)
        assert {r.metric for r in rows} == {
            METRIC_FOOD_INVENTORY,
            METRIC_DRINK_INVENTORY,
            METRIC_SUPPLY_INVENTORY,
        }

    def test_1行から3指標が作られる(self, reader, master):
        """FWのタブと違い、1行に3つの金額が横に並ぶ。"""
        rows, report = build_rows(reader, master)
        resolved = (
            report.rows_read
            - len(report.skipped)
            - sum(report.unknown_stores.values())
        )
        assert len(rows) == resolved * 3

    def test_インフォマート形式の店名がFWコードに解決される(self, reader, master):
        """「パフェ＆ジェラート ＬＡＲＧＯ ルクア店」→ FWコード 1160 に着地すること。"""
        rows, _ = build_rows(reader, master)
        assert "1160" in {r.store_code for r in rows}
        assert all(r.store_code in set(master.active_codes) for r in rows)

    def test_円記号とカンマが数値になる(self, reader, master):
        rows, _ = build_rows(reader, master)
        assert all(isinstance(r.value, float) for r in rows)
        assert any(r.value > 0 for r in rows)

    def test_ヘッダー行は読み飛ばす(self, reader, master):
        rows, _ = build_rows(reader, master)
        assert all(r.date.day == 1 for r in rows)

    def test_月次として月初日に載る(self, reader, master):
        rows, _ = build_rows(reader, master)
        assert all(r.grain == GRAIN_MONTH for r in rows)

    def test_集約元が記録される(self, reader, master):
        rows, _ = build_rows(reader, master)
        assert all(r.source == SOURCE for r in rows)


class Test取りこぼしの可視化:
    def test_マスタに無い店名は報告される(self, reader, master):
        _, report = build_rows(reader, master)
        assert report.unknown_stores
        assert not report.ok

    def test_strictなら中断する(self, reader, master, warehouse):
        with pytest.raises(RuntimeError, match="取りこぼし"):
            ingest(reader, master, warehouse, strict=True)


class TestFWと共存できる:
    def test_FWの取り込みを消さない(self, reader, master, warehouse):
        """source が違えば、片方の取り込みがもう片方を消さないこと。"""
        from hansoku.ingest.fw_sheet import ingest as fw_ingest

        fw_ingest(reader, master, warehouse, strict=False)
        fw_count = warehouse.query("SELECT count(*) c FROM f_actuals")[0]["c"]

        ingest(reader, master, warehouse, strict=False)
        total = warehouse.query("SELECT count(*) c FROM f_actuals")[0]["c"]
        assert total > fw_count

        remaining = warehouse.query(
            "SELECT count(*) c FROM f_actuals WHERE source = 'fw_sheet'"
        )[0]["c"]
        assert remaining == fw_count

    def test_両方を何度流しても増えない(self, reader, master, warehouse):
        from hansoku.ingest.fw_sheet import ingest as fw_ingest

        counts = []
        for _ in range(3):
            fw_ingest(reader, master, warehouse, strict=False)
            ingest(reader, master, warehouse, strict=False)
            counts.append(warehouse.query("SELECT count(*) c FROM f_actuals")[0]["c"])
        assert len(set(counts)) == 1
