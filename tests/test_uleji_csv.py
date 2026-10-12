"""uレジ商品別ABC CSVの取込（FW未連動店）。"""
from hansoku.ingest.uleji_csv import build_rows, parse_abc_csv
from hansoku.model import (
    METRIC_COVERS,
    METRIC_DEPT_SALES,
    METRIC_PRODUCT_QTY,
    METRIC_PRODUCT_SALES,
    METRIC_SALES,
)

CSV = """順位,ランク,商品名,カテゴリ１,カテゴリ２,販売単価,平均単価,売上数量,売上金額,構成比,累計構成比,商品設定原価,粗利益,粗利率
1,A,生ビール,ドリンク,ビール,¥550,¥550,10,"¥5,500",50%,50%,¥300,¥250,45%
2,A,串かつ盛り,フード,串カツ,¥800,¥800,5,"¥4,000",36%,86%,¥200,¥600,75%
3,B,お冷,サービス,サービス,¥0,¥0,20,¥0,0%,86%,¥0,¥0,0%
4,C,緑茶割,商品カテゴリ無し1,商品カテゴリ無し2,¥50,¥50,3,¥150,1%,100%,¥10,¥40,80%
5,-,飲）レモンサワー,飲み放題,飲）酎ハイ,¥0,¥0,0,¥0,0%,100%,¥52,"¥-52",0%
6,-,ロック,飲み放題,ロック,¥0,¥0,7,¥0,0%,100%,¥0,¥0,0%
"""


def _write(tmp_path):
    p = tmp_path / "abc.csv"
    p.write_text("﻿" + CSV, encoding="utf-8")
    return p


def test_parse_金額と原価合計(tmp_path):
    items = parse_abc_csv(_write(tmp_path))
    beer = next(i for i in items if i["name"] == "生ビール")
    assert beer["sales"] == 5500 and beer["qty"] == 10
    assert beer["cost"] == 3000 and beer["gross"] == 2500   # 単価×数量
    # サービス/商品カテゴリ無し2 は その他 に寄せる
    svc = next(i for i in items if i["name"] == "お冷")
    assert svc["cat2"] == "その他"


def test_build_rows_区分と商品タグ(tmp_path):
    items = parse_abc_csv(_write(tmp_path))
    rows = build_rows("1766", "2026-09", items, sales_total=123456, covers=78)
    # 区分(dept_sales)は売上のあるカテゴリ2だけ・売上順でNN付き
    depts = {r.product_name: int(r.value) for r in rows if r.metric == METRIC_DEPT_SALES}
    assert depts.get("01:ビール") == 5500
    assert depts.get("02:串カツ") == 4000
    # 商品QTYに所属区分(group)が付く
    pq = {r.product_name: r.product_category for r in rows if r.metric == METRIC_PRODUCT_QTY}
    assert pq.get("生ビール") == "01:ビール"
    assert pq.get("串かつ盛り") == "02:串カツ"
    # 商品SALESはランク
    ps = {r.product_name: r.product_category for r in rows if r.metric == METRIC_PRODUCT_SALES}
    assert ps.get("生ビール") == "A"
    # トップライン
    assert [int(r.value) for r in rows if r.metric == METRIC_SALES] == [123456]
    assert [int(r.value) for r in rows if r.metric == METRIC_COVERS] == [78]


def test_売上0だけの区分は見出しを付けない(tmp_path):
    items = parse_abc_csv(_write(tmp_path))
    rows = build_rows("1766", "2026-09", items)
    # 「ロック」は点数はあるが売上0・そのカテゴリ(ロック)は売上0のみ→区分見出し無し(group None)
    pq = {r.product_name: r.product_category for r in rows if r.metric == METRIC_PRODUCT_QTY}
    assert "ロック" in pq and pq["ロック"] is None
    depts = {r.product_name for r in rows if r.metric == METRIC_DEPT_SALES}
    assert not any("ロック" in d for d in depts)   # 売上0の区分は作らない
