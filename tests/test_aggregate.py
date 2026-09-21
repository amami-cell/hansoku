"""
集計クエリ。Phase 0 の完了基準「任意の店舗×期間×指標×時間帯で集計できる」の検証。
"""
from datetime import date

import pytest

from hansoku import analytics
from hansoku.db.warehouse import AggregateQuery
from hansoku.model import GRAIN_HOUR, GRAIN_MONTH, ActualRow


AUG = (date(2026, 8, 1), date(2026, 8, 31))


class Test任意の軸で集計できる:
    def test_店舗で絞れる(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(*AUG, GRAIN_MONTH, metrics=["sales"], store_codes=["1015"])
        )
        assert [r["store_code"] for r in rows] == ["1015"]

    def test_複数店舗で絞れる(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(*AUG, GRAIN_MONTH, metrics=["sales"], store_codes=["1015", "1006"])
        )
        assert sorted(r["store_code"] for r in rows) == ["1006", "1015"]

    def test_期間で絞れる(self, loaded):
        aug = loaded.aggregate(AggregateQuery(*AUG, GRAIN_MONTH, metrics=["sales"]))
        year = loaded.aggregate(
            AggregateQuery(date(2026, 1, 1), date(2026, 12, 31), GRAIN_MONTH, metrics=["sales"])
        )
        assert sum(r["value"] for r in year) > sum(r["value"] for r in aug)

    def test_指標で絞れる(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(*AUG, GRAIN_MONTH, metrics=["sales", "food_sales"], store_codes=["1015"])
        )
        assert sorted(r["metric"] for r in rows) == ["food_sales", "sales"]

    def test_日付で束ねられる(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(
                date(2026, 1, 1),
                date(2026, 12, 31),
                GRAIN_MONTH,
                metrics=["sales"],
                store_codes=["1015"],
                group_by=("date",),
            )
        )
        # 2026-09 は、表記揺れした店名（「すさび湯 歌舞伎町」）の行がフィクスチャに
        # 1件だけ入っており、それが store_code=1015 に解決されて載っている。
        assert [r["date"] for r in rows] == [
            date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1),
        ]

    def test_前年同月と比較できる(self, loaded):
        def total(year):
            rows = loaded.aggregate(
                AggregateQuery(
                    date(year, 8, 1), date(year, 8, 31), GRAIN_MONTH,
                    metrics=["sales"], store_codes=["1015"],
                )
            )
            return rows[0]["value"]

        assert total(2025) > 0 and total(2026) > 0


class Test時間帯で集計できる:
    """月次しか取り込めていない現時点でも、時間粒度の集計経路が通ることを確かめる。"""

    @pytest.fixture
    def hourly(self, warehouse):
        warehouse.replace_actuals(
            [
                ActualRow(
                    store_code="1015",
                    date=date(2026, 8, 1),
                    grain=GRAIN_HOUR,
                    hour=hour,
                    metric="sales",
                    value=float(hour * 1000),
                    source="test",
                )
                for hour in range(24)
            ]
        )
        return warehouse

    def test_時間で絞れる(self, hourly):
        rows = hourly.aggregate(
            AggregateQuery(
                date(2026, 8, 1), date(2026, 8, 1), GRAIN_HOUR,
                metrics=["sales"], hours=[18, 19, 20],
            )
        )
        assert rows[0]["value"] == (18 + 19 + 20) * 1000

    def test_時ごとに束ねられる(self, hourly):
        rows = hourly.aggregate(
            AggregateQuery(
                date(2026, 8, 1), date(2026, 8, 1), GRAIN_HOUR,
                metrics=["sales"], group_by=("hour",),
            )
        )
        assert len(rows) == 24

    def test_粒度が混ざらない(self, hourly, reader, master):
        """月次と時間別が同じ日に載っていても、合計が混ざらないこと。"""
        from hansoku.ingest.fw_sheet import ingest

        ingest(reader, master, hourly, year_months={"2026-08"}, strict=False)
        hour_total = hourly.aggregate(
            AggregateQuery(
                date(2026, 8, 1), date(2026, 8, 1), GRAIN_HOUR,
                metrics=["sales"], store_codes=["1015"],
            )
        )[0]["value"]
        assert hour_total == sum(h * 1000 for h in range(24))


class Test比率指標:
    def test_原価率は分子分母を合計してから割る(self, loaded):
        rows = analytics.ratio(loaded, "cost_rate", date_from=AUG[0], date_to=AUG[1],
                               store_codes=["1015"])
        sums = analytics.totals(loaded, date_from=AUG[0], date_to=AUG[1],
                                metrics=["food_theory_cost", "drink_theory_cost", "sales"],
                                store_codes=["1015"])
        expected = (
            sums[("1015", "food_theory_cost")] + sums[("1015", "drink_theory_cost")]
        ) / sums[("1015", "sales")]
        assert rows[0].value == pytest.approx(expected)

    def test_分母が無いときは0ではなくNoneを返す(self, loaded):
        """0除算を0%として出すと、原価率のような lower_better 指標で達成に見えてしまう。"""
        rows = analytics.ratio(loaded, "avg_check", date_from=AUG[0], date_to=AUG[1],
                               store_codes=["1015"])
        assert rows[0].value is None

    def test_比率でない指標を渡すとエラー(self, loaded):
        with pytest.raises(ValueError, match="比率指標ではありません"):
            analytics.ratio(loaded, "sales", date_from=AUG[0], date_to=AUG[1])


class Testブランド集計:
    def test_ブランド単位で束ねられる(self, loaded, master):
        brands = analytics.by_brand(loaded, master, date_from=AUG[0], date_to=AUG[1],
                                    metric="sales")
        assert brands["SUSABIYU"] > 0
        assert len(brands) == len({s.brand for s in master.active})


class Test入力の検証:
    def test_未知の粒度は弾く(self, loaded):
        with pytest.raises(ValueError, match="grain"):
            loaded.aggregate(AggregateQuery(*AUG, "week"))

    def test_期間が逆なら弾く(self, loaded):
        with pytest.raises(ValueError, match="date_from"):
            loaded.aggregate(AggregateQuery(AUG[1], AUG[0], GRAIN_MONTH))

    def test_未知の指標は弾く(self, loaded):
        with pytest.raises(ValueError, match="metric"):
            loaded.aggregate(AggregateQuery(*AUG, GRAIN_MONTH, metrics=["nope"]))

    def test_group_byに使えない列は弾く(self, loaded):
        with pytest.raises(ValueError, match="group_by"):
            loaded.aggregate(AggregateQuery(*AUG, GRAIN_MONTH, group_by=("source",)))


class Test表記揺れした店名の取り込み:
    def test_全角半角が違っても正しい店舗に載る(self, loaded):
        """フィクスチャの「すさび湯 歌舞伎町」(半角スペース) が 1015 に解決されること。"""
        rows = loaded.aggregate(
            AggregateQuery(
                date(2026, 9, 1), date(2026, 9, 30), GRAIN_MONTH,
                metrics=["sales"], store_codes=["1015"],
            )
        )
        assert rows[0]["value"] == 1234567.0


class Test指標の混在を防ぐ:
    def test_metricで束ねずに複数指標を集計しようとすると弾く(self, loaded):
        """売上と客数が1つの値に潰れると、無意味な数字が画面に出てしまう。"""
        with pytest.raises(ValueError, match="metric"):
            loaded.aggregate(
                AggregateQuery(*AUG, GRAIN_MONTH, metrics=["sales", "covers"],
                               group_by=("store_code",))
            )

    def test_指標を1つに絞れば束ねずに集計できる(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(*AUG, GRAIN_MONTH, metrics=["sales"], group_by=("store_code",))
        )
        # 稼働店ぶん（1115 閉店で 24→23）。
        assert len(rows) == 23

    def test_metricを含めれば複数指標でも集計できる(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(*AUG, GRAIN_MONTH, metrics=["sales", "food_sales"],
                           store_codes=["1015"], group_by=("store_code", "metric"))
        )
        assert len(rows) == 2

    def test_指標を絞らない全件集計もmetricで束ねれば通る(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(*AUG, GRAIN_MONTH, store_codes=["1015"], group_by=("metric",))
        )
        assert len(rows) == 9


class TestCLIの束ね方:
    def test_日付で束ねると月別の推移が出る(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(
                date(2026, 1, 1), date(2026, 12, 31), GRAIN_MONTH,
                metrics=["sales"], store_codes=["1015"], group_by=("date",),
            )
        )
        assert len(rows) >= 3
        # 日付順に並んでいること（推移として読めるため）
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)

    def test_店舗と日付の両方で束ねられる(self, loaded):
        rows = loaded.aggregate(
            AggregateQuery(
                date(2026, 6, 1), date(2026, 8, 31), GRAIN_MONTH,
                metrics=["sales"], store_codes=["1015", "1006"],
                group_by=("store_code", "date"),
            )
        )
        assert {r["store_code"] for r in rows} == {"1006", "1015"}
        assert len(rows) == 6  # 2店 × 3ヶ月


class Test取り込み口の優先順位:
    """同じ (店, 月, 指標) を複数の取り込み口が書いたとき、どちらを採るかを固定する。

    月次の売上は fw_sheet（店長会シート・毎日）と fw_uriage_suii（月別日別売上推移）の
    両方が書く。実測では 2026年の全店・全月で fw_uriage_suii = fw_sheet × 1.10
    ちょうど（税込と税抜）。以前は ingested_at 任せだったため、流した順で画面の
    数字が変わり、2025年は税込・2026年は税抜という混在が起きていた。
    """

    def _rows(self, sheet_at, suii_at):
        from datetime import datetime

        common = dict(
            store_code="1006",
            date=date(2026, 8, 1),
            grain=GRAIN_MONTH,
            metric="sales",
            kind="確定",
        )
        return [
            ActualRow(
                **common, value=20_922_282.0, source="fw_sheet",
                ingested_at=datetime(2026, 9, 1, sheet_at),
            ),
            ActualRow(
                **common, value=23_014_088.0, source="fw_uriage_suii",
                ingested_at=datetime(2026, 9, 1, suii_at),
            ),
        ]

    @pytest.mark.parametrize("sheet_at,suii_at", [(1, 2), (2, 1)])
    def test_税込側_fw_uriage_suii_を採る(self, warehouse, sheet_at, suii_at):
        """取り込んだ順に関係なく、常に同じ側が採られる。"""
        warehouse.ensure_schema()
        warehouse.replace_actuals(self._rows(sheet_at, suii_at))
        rows = warehouse.aggregate(
            AggregateQuery(
                date(2026, 8, 1), date(2026, 8, 31), GRAIN_MONTH,
                metrics=["sales"], store_codes=["1006"], group_by=("store_code",),
            )
        )
        assert [r["value"] for r in rows] == [23_014_088.0]


class Test比率は同じ取り込み口の中で割る:
    """理論原価は店長会シート（fw_sheet）の1枚から来る。同じ表の売上で割らないと
    意味を成さない。売上は fw_sheet（税抜）と fw_uriage_suii（税込）の両方が
    書いていて、既定では税込が採られる。そのまま割ると分子だけ税抜になり、
    原価率が実態より低く出る（低いほど良い指標なので危ない方向にずれる）。"""

    def _seed(self, warehouse):
        from datetime import datetime

        common = dict(store_code="1006", date=date(2026, 8, 1), grain=GRAIN_MONTH, kind="確定")
        at = datetime(2026, 9, 1, 3)
        rows = [
            # 店長会シート（税抜）: 売上2000万・理論原価600万 → 30.0%
            ActualRow(**common, metric="sales", value=20_000_000.0, source="fw_sheet", ingested_at=at),
            ActualRow(**common, metric="food_theory_cost", value=4_000_000.0, source="fw_sheet", ingested_at=at),
            ActualRow(**common, metric="drink_theory_cost", value=2_000_000.0, source="fw_sheet", ingested_at=at),
            # 売上推移（税込）: 2200万。SOURCE_PRIORITY はこちらを採る
            ActualRow(**common, metric="sales", value=22_000_000.0, source="fw_uriage_suii", ingested_at=at),
        ]
        warehouse.ensure_schema()
        warehouse.replace_actuals(rows)

    def test_原価率は税抜どうしで割る(self, warehouse):
        self._seed(warehouse)
        [v] = analytics.ratio(
            warehouse, "cost_rate",
            date_from=date(2026, 8, 1), date_to=date(2026, 8, 31), store_codes=["1006"],
        )
        # 600万 / 2000万 = 30.0%。税込2200万で割ると 27.3% になってしまう。
        assert round(v.value * 100, 1) == 30.0

    def test_売上そのものは税込が採られる(self, warehouse):
        """原価率の分母を絞っても、売上指標そのものの優先順位は変わらない。"""
        self._seed(warehouse)
        rows = warehouse.aggregate(
            AggregateQuery(
                date(2026, 8, 1), date(2026, 8, 31), GRAIN_MONTH,
                metrics=["sales"], store_codes=["1006"], group_by=("store_code",),
            )
        )
        assert [r["value"] for r in rows] == [22_000_000.0]
