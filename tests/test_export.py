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


def test_生成時刻が入る(payload):
    assert payload["generated_at"]


def test_JSONが小さい(payload):
    import json

    # 画面が一瞬で読める大きさに収まっていること（数百KB以内）
    size = len(json.dumps(payload, ensure_ascii=False))
    assert size < 500_000
