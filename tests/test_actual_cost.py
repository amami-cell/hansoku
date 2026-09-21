"""
実原価（前月棚卸 + 当月仕入 − 当月棚卸）の算出。

FWのABC部門が無い店・月でも原価を出せるようにするための指標。
理論原価とは別物で、その差が不明ロスなので、混ぜないことが要点。
"""
from hansoku.analytics import actual_cost_by_month


class FakeWarehouse:
    """(store, metric) → 値 を月ごとに持つだけの倉庫。"""

    def __init__(self, by_month: dict):
        self.by_month = by_month

    def aggregate(self, query):
        month = query.date_from.strftime("%Y-%m")
        out = []
        for (store, metric), value in self.by_month.get(month, {}).items():
            if metric not in query.metrics:
                continue
            if query.store_codes and store not in query.store_codes:
                continue
            out.append({"store_code": store, "metric": metric, "value": value})
        return out


def wh(**months):
    # m2026_08 → 2026-08（キーワード引数は数字で始められないので m を付けている）
    return FakeWarehouse({m[1:].replace("_", "-"): v for m, v in months.items()})


class Test実原価が計算できる:
    def test_前月棚卸_当月仕入_当月棚卸から求める(self):
        w = wh(
            m2026_07={("1069", "food_inventory"): 100, ("1069", "drink_inventory"): 50},
            m2026_08={
                ("1069", "food_purchase"): 1000, ("1069", "drink_purchase"): 300,
                ("1069", "food_inventory"): 200, ("1069", "drink_inventory"): 80,
                ("1069", "sales"): 5000,
            },
        )
        got = actual_cost_by_month(w, months=["2026-08"])["1069"]["2026-08"]
        assert got["food"] == 900        # 100 + 1000 - 200
        assert got["drink"] == 270       # 50 + 300 - 80
        assert got["total"] == 1170
        # cost_rate と同じ分数で持つ（画面が同じ pct() で出せるように）
        assert got["rate"] == 0.234      # 1170 / 5000

    def test_年をまたぐ(self):
        w = wh(
            m2025_12={("1137", "food_inventory"): 10, ("1137", "drink_inventory"): 5},
            m2026_01={
                ("1137", "food_purchase"): 100, ("1137", "drink_purchase"): 50,
                ("1137", "food_inventory"): 20, ("1137", "drink_inventory"): 5,
                ("1137", "sales"): 1000,
            },
        )
        got = actual_cost_by_month(w, months=["2026-01"])["1137"]["2026-01"]
        assert got["food"] == 90 and got["drink"] == 50


class Test材料が欠けたら出さない:
    def test_前月棚卸が無い月は出さない(self):
        w = wh(m2026_08={
            ("1069", "food_purchase"): 1000, ("1069", "drink_purchase"): 300,
            ("1069", "food_inventory"): 200, ("1069", "drink_inventory"): 80,
            ("1069", "sales"): 5000,
        })
        # 0として計算すると「棚卸ぶんだけ原価が多い」月に化ける
        assert actual_cost_by_month(w, months=["2026-08"]) == {}

    def test_仕入が無い月は出さない(self):
        w = wh(
            m2026_07={("1069", "food_inventory"): 100, ("1069", "drink_inventory"): 50},
            m2026_08={("1069", "food_inventory"): 200, ("1069", "drink_inventory"): 80,
                      ("1069", "sales"): 5000},
        )
        assert actual_cost_by_month(w, months=["2026-08"]) == {}

    def test_ドリンクだけ欠けても店ごと出さない(self):
        w = wh(
            m2026_07={("1069", "food_inventory"): 100, ("1069", "drink_inventory"): 50},
            m2026_08={("1069", "food_purchase"): 1000,
                      ("1069", "food_inventory"): 200, ("1069", "drink_inventory"): 80,
                      ("1069", "sales"): 5000},
        )
        # フードだけ出すと「原価が異様に低い店」に見える
        assert actual_cost_by_month(w, months=["2026-08"]) == {}


class Test売上が無い月は率を出さない:
    def test_金額は出すが率はNone(self):
        w = wh(
            m2026_07={("1069", "food_inventory"): 100, ("1069", "drink_inventory"): 50},
            m2026_08={("1069", "food_purchase"): 1000, ("1069", "drink_purchase"): 300,
                      ("1069", "food_inventory"): 200, ("1069", "drink_inventory"): 80},
        )
        got = actual_cost_by_month(w, months=["2026-08"])["1069"]["2026-08"]
        # 0除算を0%にすると lower_better の原価率で「達成」に見えてしまう
        assert got["rate"] is None and got["total"] == 1170


class Test月の並び:
    """cli の actual-cost が使う月リスト。年またぎで止まらないこと。"""

    def test_年をまたいで続く(self):
        from hansoku.cli import _month_range

        assert _month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]

    def test_同じ月なら1件(self):
        from hansoku.cli import _month_range

        assert _month_range("2026-08", "2026-08") == ["2026-08"]

    def test_逆順なら空(self):
        from hansoku.cli import _month_range

        assert _month_range("2026-08", "2026-07") == []


class Test信用できない値に印をつける:
    """材料が揃っていても、値そのものが嘘の月がある。消さずに印を付ける。

    実測（2026-02〜08、24店）で出た2種類:
      1151 NagaGutsu 2026-04 … 342.9%（翌月 18.0%）＝棚卸の取り違え
      1743 すさび湯 新宿東口 2026-05 … 0.6%（開店直後の在庫積み）
    どちらも「材料は揃っている」ので欠測ガードでは止まらない。
    """

    def test_率が高すぎる月に印がつく(self):
        w = wh(
            m2026_03={("1151", "food_inventory"): 100, ("1151", "drink_inventory"): 0},
            m2026_04={
                ("1151", "food_purchase"): 5000, ("1151", "drink_purchase"): 0,
                ("1151", "food_inventory"): 10, ("1151", "drink_inventory"): 0,
                ("1151", "sales"): 1000,
            },
        )
        got = actual_cost_by_month(w, months=["2026-04"])["1151"]["2026-04"]
        assert got["rate"] > 1.0
        assert got["suspect"] and "高すぎる" in got["suspect"]

    def test_率が低すぎる月に印がつく(self):
        w = wh(
            m2026_04={("1743", "food_inventory"): 0, ("1743", "drink_inventory"): 0},
            m2026_05={
                ("1743", "food_purchase"): 1000, ("1743", "drink_purchase"): 0,
                ("1743", "food_inventory"): 990, ("1743", "drink_inventory"): 0,
                ("1743", "sales"): 1000,
            },
        )
        got = actual_cost_by_month(w, months=["2026-05"])["1743"]["2026-05"]
        assert got["rate"] < 0.10
        assert got["suspect"] and "低すぎる" in got["suspect"]

    def test_ふつうの月には印がつかない(self):
        w = wh(
            m2026_07={("1069", "food_inventory"): 100, ("1069", "drink_inventory"): 50},
            m2026_08={
                ("1069", "food_purchase"): 1000, ("1069", "drink_purchase"): 300,
                ("1069", "food_inventory"): 200, ("1069", "drink_inventory"): 80,
                ("1069", "sales"): 5000,
            },
        )
        got = actual_cost_by_month(w, months=["2026-08"])["1069"]["2026-08"]
        assert got["rate"] == 0.234
        assert got["suspect"] is None

    def test_売上が無い月は率が無いので印もつかない(self):
        w = wh(
            m2026_07={("1069", "food_inventory"): 100, ("1069", "drink_inventory"): 50},
            m2026_08={
                ("1069", "food_purchase"): 1000, ("1069", "drink_purchase"): 300,
                ("1069", "food_inventory"): 200, ("1069", "drink_inventory"): 80,
            },
        )
        got = actual_cost_by_month(w, months=["2026-08"])["1069"]["2026-08"]
        assert got["rate"] is None and got["suspect"] is None
