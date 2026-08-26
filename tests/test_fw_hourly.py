"""時間帯別・月次グリッドの行パース（視覚行→指標）を、実画面のダンプで固定する。

ブラウザに依存しないよう _visual_rows を差し替え、パース部分だけを検証する。
実際の FW ダンプ（すさび湯 2026-07 の時間帯別、八銭の月別）をそのまま使う。
"""
from hansoku.ingest import fw_daily
from hansoku.ingest.fw_daily import (
    _ABC_QTY,
    _ABC_SALES,
    _HOUR_COVERS,
    _HOUR_SALES,
    _INT_COVERS,
    _INT_SALES,
    _extract_hour_grid,
    _extract_month_grid,
    _extract_product_grid,
)


class _FakeSession:
    pass


def test_時間帯グリッドから時間帯別の売上と客数を拾う(monkeypatch):
    # FW「時間帯別売上」の実ダンプ（先頭セル=時間帯の裸数字、％・見出し混在）
    rows = [
        ["TOP", "販売管理", "勤怠管理", "損益管理"],
        ["大衆寿司酒場すさび湯", "期間", "2026/07/01", "～", "2026/07/31"],
        ["曜日選択", "0", "1", "2"],
        ["時間帯", "組数", "客数", "売上", "構成比", "組単価", "客単価", "坪売上",
         "回転率", "人時売上", "人時生産性"],
        ["10", "5", "13", "37,185", "0.17%", "7,437", "2,860", "422", "0.30%", "562", "341"],
        ["11", "154", "340", "813,518", "3.89%", "5,282", "2,392", "9,244", "7.94%", "5,170", "2,992"],
        ["12", "187", "389", "942,288", "4.51%", "5,038", "2,422", "10,707", "9.09%", "4,535", "2,743"],
    ]
    monkeypatch.setattr(fw_daily, "_visual_rows", lambda _s: rows)
    grid = _extract_hour_grid(_FakeSession())

    assert [b["hour"] for b in grid] == [10, 11, 12]
    # 客数=ints[1], 売上=ints[2]（ラベル除去後の並び）
    b10 = grid[0]
    assert b10["ints"][_HOUR_COVERS] == 13
    assert b10["ints"][_HOUR_SALES] == 37185
    b12 = grid[2]
    assert b12["ints"][_HOUR_COVERS] == 389
    assert b12["ints"][_HOUR_SALES] == 942288


def test_月次グリッドから売上と客数を拾う(monkeypatch):
    # FW「月別日別売上推移」の実ダンプ（八銭 2025-09 の1行ぶん）。％を挟む15列。
    row = [
        "2025年09月",
        "24,200,000", "23,209,138", "95.90%", "-990,862", "25,561,425", "90.79%",
        "-2,352,287", "12,224", "95.79%", "13,425", "91.05%", "-1,201",
        "1,898", "1,904", "-6",
    ]
    monkeypatch.setattr(fw_daily, "_visual_rows", lambda _s: [row])
    grid = _extract_month_grid(_FakeSession())

    assert len(grid) == 1
    m = grid[0]
    assert m["period"] == "2025-09"
    assert m["ints"][_INT_SALES] == 23209138      # 実績
    assert m["ints"][_INT_COVERS] == 12224         # 客数


def test_ABC分析から商品名と売上とランクを拾う(monkeypatch):
    # FW「ABC分析」の想定行（商品CD 商品名 販売単価 原価 原価率 販売数量 売上金額 …ランク）
    # 実グリッドの表記に合わせる: 原価は "180.00"（小数）・原価率/構成比は "%" つき。
    # このため整数のみ拾うと商品名の後ろは [単価, 数量, 売上, 原価金額, 粗利] の並びになる。
    rows = [
        ["商品CD", "商品名", "販売単価", "原価", "原価率", "販売数量", "売上金額",
         "原価金額", "粗利金額", "売上構成比", "累計構成比", "粗利貢献率", "ランク"],
        ["1001", "生ビール中", "550", "180.00", "32.7%", "1,240", "682,000",
         "223,200", "458,800", "5.20%", "5.20%", "6.10%", "A"],
        ["2050", "本日のおすすめ刺盛", "1,280", "520.00", "40.6%", "310", "396,800",
         "161,200", "235,600", "3.02%", "8.22%", "3.10%", "A"],
        ["合計", "", "", "", "", "", "13,120,000"],
    ]
    monkeypatch.setattr(fw_daily, "_visual_rows", lambda _s: rows)
    grid = _extract_product_grid(_FakeSession())

    assert [p["name"] for p in grid] == ["生ビール中", "本日のおすすめ刺盛"]
    p0 = grid[0]
    assert p0["ints"][_ABC_QTY] == 1240       # 販売数量
    assert p0["ints"][_ABC_SALES] == 682000   # 売上金額
    assert p0["rank"] == "A"
