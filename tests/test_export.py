"""画面が読む JSON の書き出し。"""
from datetime import date

import pytest

from hansoku.web.export import build


@pytest.fixture
def payload(loaded, master):
    from hansoku.ingest.infomart_sheet import ingest as im_ingest

    # 棚卸も入れておく（原価率とは別系統）
    return build(loaded, master, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))


def test_店舗と月が入る(payload):
    assert payload["stores"]
    assert payload["months"] == sorted(payload["months"])


def test_月次に売上が入る(payload):
    code = payload["stores"][0]["code"]
    some = payload["monthly"][code]
    assert any("sales" in v for v in some.values())


def test_原価率が計算されている(payload):
    # どこかの店に原価率が入っていること（分子分母から組み直す）
    assert any(payload["cost_rate"].values())


def test_原価率は0から1の割合(payload):
    for by_month in payload["cost_rate"].values():
        for rate in by_month.values():
            assert 0 < rate < 1


def test_施策は空でも成立する(payload):
    assert payload["campaigns"] == []


def test_客数がcoversに焼かれる(loaded, master):
    """月別日別売上推移で取り込む客数（集客）が dashboard.json の covers に出る。"""
    from datetime import datetime, timezone

    from hansoku.model import GRAIN_MONTH, KIND_FINAL, METRIC_COVERS, ActualRow

    code = master.active_codes[0]
    loaded.replace_actuals(
        [
            ActualRow(
                store_code=code,
                date=date(2025, 6, 1),
                grain=GRAIN_MONTH,
                metric=METRIC_COVERS,
                value=1234.0,
                kind=KIND_FINAL,
                source="fw_uriage_suii",
                ingested_at=datetime.now(timezone.utc),
            )
        ]
    )
    payload = build(loaded, master, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
    assert "covers" in payload
    assert payload["covers"].get(code, {}).get("2025-06") == 1234


def test_時間帯別がhourlyに焼かれる(loaded, master):
    """FW時間帯別売上で取り込む時間帯×売上・客数が hourly に出る。"""
    from datetime import datetime, timezone

    from hansoku.model import GRAIN_HOUR, KIND_FINAL, METRIC_COVERS, METRIC_SALES, ActualRow

    code = master.active_codes[0]
    now = datetime.now(timezone.utc)
    loaded.replace_actuals(
        [
            ActualRow(store_code=code, date=date(2026, 7, 1), grain=GRAIN_HOUR,
                      metric=METRIC_SALES, value=942288.0, hour=12, kind=KIND_FINAL,
                      source="fw_hourly", ingested_at=now),
            ActualRow(store_code=code, date=date(2026, 7, 1), grain=GRAIN_HOUR,
                      metric=METRIC_COVERS, value=389.0, hour=12, kind=KIND_FINAL,
                      source="fw_hourly", ingested_at=now),
        ]
    )
    payload = build(loaded, master, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
    assert payload["hourly_month"] == "2026-07"
    assert payload["hourly"].get(code, {}).get("12") == {"sales": 942288, "covers": 389}


def test_売れ筋商品がproductsに焼かれる(loaded, master):
    """FW ABC分析で取り込む商品別売上が products に売上順で出る。"""
    from datetime import datetime, timezone

    from hansoku.model import GRAIN_MONTH, KIND_FINAL, METRIC_PRODUCT_SALES, ActualRow

    code = master.active_codes[0]
    now = datetime.now(timezone.utc)
    loaded.replace_actuals(
        [
            ActualRow(store_code=code, date=date(2026, 7, 1), grain=GRAIN_MONTH,
                      metric=METRIC_PRODUCT_SALES, value=396800.0, product_name="おすすめ刺盛",
                      product_category="A", kind=KIND_FINAL, source="fw_abc", ingested_at=now),
            ActualRow(store_code=code, date=date(2026, 7, 1), grain=GRAIN_MONTH,
                      metric=METRIC_PRODUCT_SALES, value=682000.0, product_name="生ビール中",
                      product_category="A", kind=KIND_FINAL, source="fw_abc", ingested_at=now),
        ]
    )
    payload = build(loaded, master, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
    assert payload["products_month"] == "2026-07"
    got = payload["products"].get(code)
    assert got and got[0] == {"name": "生ビール中", "sales": 682000, "rank": "A"}


def test_全店の売れ筋がproducts_groupに焼かれる(loaded, master):
    """ABC分析の全店集計（擬似店舗 _group）が products_group に売上順で出る。"""
    from datetime import datetime, timezone

    from hansoku.model import GRAIN_MONTH, KIND_FINAL, METRIC_PRODUCT_SALES, ActualRow

    now = datetime.now(timezone.utc)
    loaded.replace_actuals(
        [
            ActualRow(store_code="_group", date=date(2026, 7, 1), grain=GRAIN_MONTH,
                      metric=METRIC_PRODUCT_SALES, value=3100000.0, product_name="生ビール",
                      product_category="A", kind=KIND_FINAL, source="fw_abc", ingested_at=now),
            ActualRow(store_code="_group", date=date(2026, 7, 1), grain=GRAIN_MONTH,
                      metric=METRIC_PRODUCT_SALES, value=5200000.0, product_name="名物もつ鍋",
                      product_category="A", kind=KIND_FINAL, source="fw_abc", ingested_at=now),
        ]
    )
    payload = build(loaded, master, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
    group = payload["products_group"]
    assert group[0] == {"name": "名物もつ鍋", "sales": 5200000, "rank": "A"}
    # 全店の擬似店舗は店舗別 products には混ざらない
    assert "_group" not in payload["products"]


def test_生成時刻が入る(payload):
    assert payload["generated_at"]


def test_JSONが小さい(payload):
    import json

    # 画面が一瞬で読める大きさに収まっていること（数百KB以内）
    size = len(json.dumps(payload, ensure_ascii=False))
    assert size < 500_000
