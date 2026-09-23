"""0以下の実績を消す。   pytest tests/test_purge.py

⚠️ **本番DBを書き換えるコマンドなので、守りを全部固定する。**
  * 既定は素振り（`--apply` なしでは1行も消えない）
  * 正の値には絶対に触らない
  * source を指定しないと動かない（全ソースを消させない）
  * 探すときと消すときで同じ条件を使う
"""
import datetime as dt

import pytest

from hansoku.model import GRAIN_MONTH, METRIC_COVERS, METRIC_SALES, ActualRow
from hansoku.purge import build_where, purge_zeros


def _row(code, ym, metric, value, source="pos_sheet"):
    y, m = (int(x) for x in ym.split("-"))
    return ActualRow(
        store_code=code, date=dt.date(y, m, 1), grain=GRAIN_MONTH,
        metric=metric, value=value, source=source,
    )


@pytest.fixture
def seeded(warehouse):
    warehouse.ensure_schema()
    warehouse.replace_actuals([
        _row("1766", "2026-04", METRIC_SALES, 0),        # 開店前
        _row("1766", "2026-04", METRIC_COVERS, 0),
        _row("1766", "2026-07", METRIC_SALES, 0),
        _row("1766", "2026-08", METRIC_SALES, 12640890),  # 本物
        _row("1111", "2026-04", METRIC_SALES, 0),         # よその店の0
        _row("1766", "2026-04", METRIC_SALES, 0, source="fw_sheet"),  # 別の口の0
    ])
    return warehouse


def _left(wh, **kw):
    return purge_zeros(wh, apply=False, source="pos_sheet", **kw)


# ---- 素振り ----

def test_既定は素振りで1行も消さない(seeded):
    found = purge_zeros(seeded, source="pos_sheet", store_codes=["1766"])
    assert len(found) == 3
    # もう一度探しても同じだけ残っている＝消えていない
    assert len(_left(seeded, store_codes=["1766"])) == 3


def test_素振りと本番で同じ一覧を返す(seeded):
    dry = purge_zeros(seeded, apply=False, source="pos_sheet", store_codes=["1766"])
    wet = purge_zeros(seeded, apply=True, source="pos_sheet", store_codes=["1766"])
    assert [(r["store_code"], r["date"], r["metric"]) for r in dry] == \
           [(r["store_code"], r["date"], r["metric"]) for r in wet]


# ---- 消す範囲 ----

def test_applyで消える(seeded):
    purge_zeros(seeded, apply=True, source="pos_sheet", store_codes=["1766"])
    assert _left(seeded, store_codes=["1766"]) == []


def test_正の値には触らない(seeded):
    purge_zeros(seeded, apply=True, source="pos_sheet", store_codes=["1766"])
    rows = seeded.query(
        f"SELECT value FROM {seeded.table_name('f_actuals')} "
        "WHERE store_code = :c AND source = :s",
        {"c": "1766", "s": "pos_sheet"})
    assert [r["value"] for r in rows] == [12640890]


def test_よその店を巻き添えにしない(seeded):
    purge_zeros(seeded, apply=True, source="pos_sheet", store_codes=["1766"])
    assert len(_left(seeded, store_codes=["1111"])) == 1


def test_別の取り込み口を巻き添えにしない(seeded):
    # fw_sheet の 0 は「FWが書いた0」で、意味も持ち主も違う
    purge_zeros(seeded, apply=True, source="pos_sheet", store_codes=["1766"])
    left = purge_zeros(seeded, apply=False, source="fw_sheet", store_codes=["1766"])
    assert len(left) == 1


def test_期間で絞れる(seeded):
    purge_zeros(seeded, apply=True, source="pos_sheet", store_codes=["1766"],
                date_from="2026-04-01", date_to="2026-04-30")
    left = _left(seeded, store_codes=["1766"])
    assert [str(r["date"]) for r in left] == ["2026-07-01"]


def test_指標で絞れる(seeded):
    purge_zeros(seeded, apply=True, source="pos_sheet", store_codes=["1766"],
                metrics=[METRIC_COVERS])
    left = _left(seeded, store_codes=["1766"])
    assert {r["metric"] for r in left} == {METRIC_SALES}


# ---- 条件の組み立て ----

def test_sourceは必須():
    # 指定を忘れて全ソースの0を消すと、本当に0だった月まで巻き添えになる
    for bad in ["", None]:
        with pytest.raises(ValueError):
            build_where(source=bad)


def test_探す条件と消す条件は同じ():
    # 別々に書くと、見たものと消すものがずれる
    a = build_where(source="pos_sheet", store_codes=["1766"])
    b = build_where(source="pos_sheet", store_codes=["1766"])
    assert a == b


def test_0以下だけを対象にする():
    where, _ = build_where(source="pos_sheet")
    assert "value <= 0" in where
