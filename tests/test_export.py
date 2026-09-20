"""画面が読む JSON の書き出し。"""
from datetime import date

import pytest

from hansoku.web.export import build, classify_category, _nest_zero_subs


# 0円サブ（選択メニュー内訳）を親メイン商品の下に畳む変換。zero_groups のある店だけ効く。
ZERO_RULES = {
    "other": "その他",
    "zero_groups": {
        "テイクアウトジェラート": "TOジェラート（風味・全TO共通）",
        "ジェラートサンデーダブル": "サンデーダブル",
    },
    "categories": [
        {"name": "パフェ", "keywords": ["サンデー"]},
        {"name": "ジェラート", "keywords": ["ジェラート", "TOジェラート"]},
    ],
}


def test_サブが実在の親に畳まれ売上だけロールアップされる():
    """親名が実在商品なら、その下に内訳をぶら下げ、売上のあるサブは親の売上に足す
    （点数は足さない＝二重計上回避）。サブはトップ階層から外れる。"""
    items = [
        {"name": "サンデーダブル", "sales": 100000, "rank": "A", "qty": 200},
        {"name": "サンデーダブル ベリー", "sales": 3000, "rank": None, "qty": 40,
         "group": "06:ジェラートサンデーダブル"},
        {"name": "サンデーダブル 抹茶", "sales": 0, "rank": None, "qty": 60,
         "group": "06:ジェラートサンデーダブル"},
    ]
    tops = _nest_zero_subs(items, ZERO_RULES)
    assert [t["name"] for t in tops] == ["サンデーダブル"]   # サブはトップから消える
    parent = tops[0]
    assert parent["sales"] == 103000          # 売上のあるサブ(3000)だけ足す
    assert parent["qty"] == 200               # 点数は足さない（本体のまま）
    assert [s["name"] for s in parent["subs"]] == ["サンデーダブル ベリー", "サンデーダブル 抹茶"]
    assert parent["subs"][0] == {"name": "サンデーダブル ベリー", "qty": 40, "sales": 3000}


def test_親が実在しなければ表示専用の親ノードを作る():
    """親名が実在しなければ synthetic な親を新設し、売上はサブ売上の合計になる。
    親名で品目区分に分類できるよう name/rank を持つ。"""
    items = [
        {"name": "TOダブル ピスタチオ", "sales": 0, "rank": None, "qty": 120,
         "group": "20:テイクアウトジェラート"},
        {"name": "TOダブル いちご", "sales": 5000, "rank": None, "qty": 30,
         "group": "20:テイクアウトジェラート"},
    ]
    tops = _nest_zero_subs(items, ZERO_RULES)
    assert len(tops) == 1
    parent = tops[0]
    assert parent["name"] == "TOジェラート（風味・全TO共通）"
    assert parent["synthetic"] is True
    assert parent["qty"] is None and parent["rank"] is None
    assert parent["sales"] == 5000            # サブ売上の合計
    assert len(parent["subs"]) == 2
    # 親名から品目区分（ジェラート）に分類できる。
    assert classify_category(parent["name"], ZERO_RULES) == "ジェラート"


def test_未マップの見出しでも売上0のみその他の内訳に集約される():
    """zero_groups に無い見出しでも、売上0の真の0円選択だけを「その他の内訳」にまとめる。"""
    items = [
        {"name": "P・コーラ", "sales": 0, "rank": None, "qty": 15, "group": "11:ソフトドリンク"},
        {"name": "P・ジンジャー", "sales": 0, "rank": None, "qty": 7, "group": "11:ソフトドリンク"},
    ]
    tops = _nest_zero_subs(items, ZERO_RULES)
    assert len(tops) == 1
    assert tops[0]["name"] == "その他の内訳"
    assert tops[0]["synthetic"] is True
    assert len(tops[0]["subs"]) == 2


def test_未マップ見出しでも売上のある実売れ商品はトップに残る():
    """13:紅茶=レモン / 14:アルコール=大人のレモンティー のような、FW見出しを持つが
    売上のある実商品は畳まず、トップ階層に残す（品目区分で正しく分類するため）。"""
    items = [
        {"name": "大人のレモンティー", "sales": 12000, "rank": None, "qty": 16, "group": "14:アルコール"},
        {"name": "レモン", "sales": 300, "rank": None, "qty": 10, "group": "13:紅茶"},
        {"name": "P・コーラ", "sales": 0, "rank": None, "qty": 15, "group": "11:ソフトドリンク"},
    ]
    tops = _nest_zero_subs(items, ZERO_RULES)
    names = [t["name"] for t in tops]
    assert "大人のレモンティー" in names        # 売上あり→トップに残る
    assert "レモン" in names
    assert "その他の内訳" in names              # 売上0の P・コーラ だけ集約
    other = next(t for t in tops if t["name"] == "その他の内訳")
    assert [s["name"] for s in other["subs"]] == ["P・コーラ"]


def test_商品名サブ指定は未マップ見出しのその他内訳より優先される():
    """P・… のように未マップ見出し(11:ソフトドリンク)＋売上0でも、sub_products_contains に
    親指定があれば「その他の内訳」ではなくその親（例 Pドリンク）へ畳む。"""
    rules = {
        "other": "その他",
        "zero_groups": {"テイクアウトジェラート": "TOジェラート（風味・全TO共通）"},
        "sub_products_contains": {"P・": "Pドリンク"},
        "sub_qty_rollup": ["Pドリンク"],
        "categories": [{"name": "ドリンク", "keywords": ["ドリンク"]}],
    }
    items = [
        {"name": "P・アップルソーダ", "sales": 0, "rank": None, "qty": 10, "group": "11:ソフトドリンク"},
        {"name": "P・アイスコーヒー", "sales": 0, "rank": None, "qty": 16, "group": "11:ソフトドリンク"},
        {"name": "P・パインソーダ", "sales": 0, "rank": None, "qty": 13, "group": "11:ソフトドリンク"},
    ]
    tops = _nest_zero_subs(items, rules)
    assert [t["name"] for t in tops] == ["Pドリンク"]      # その他の内訳ではなくPドリンクへ
    p = tops[0]
    assert p["synthetic"] is True
    assert p["sales"] == 0
    assert p["qty"] == 39                                   # 合計出数 = 10+16+13（sub_qty_rollup）
    assert [s["name"] for s in p["subs"]] == [
        "P・アップルソーダ", "P・アイスコーヒー", "P・パインソーダ"]
    assert classify_category(p["name"], rules) == "ドリンク"  # ドリンク部門に入る


def test_サブを接頭辞正規化で合算する():
    """sub_merge_prefixes に載る親は "TOシングル X" と "X" を1つに合算（数量・売上とも）。"""
    rules = {
        "other": "その他",
        "zero_groups": {
            "テイクアウトジェラート": "TOジェラート（風味・全TO共通）",
            "TOシングル": "TOジェラート（風味・全TO共通）",
        },
        "sub_merge_prefixes": {"TOジェラート（風味・全TO共通）": ["TOシングル "]},
        "categories": [{"name": "ジェラート", "keywords": ["TOジェラート"]}],
    }
    items = [
        {"name": "ピスタチオ", "sales": 14200, "qty": 284, "group": "20:テイクアウトジェラート"},
        {"name": "TOシングル ピスタチオ", "sales": 2550, "qty": 51, "group": "22:TOシングル"},
        {"name": "クッキー＆バニラ", "sales": 0, "qty": 237, "group": "20:テイクアウトジェラート"},
        {"name": "TOシングル クッキー＆バニラ", "sales": 0, "qty": 78, "group": "22:TOシングル"},
    ]
    parent = _nest_zero_subs(items, rules)[0]
    subs = {s["name"]: s for s in parent["subs"]}
    assert set(subs) == {"ピスタチオ", "クッキー＆バニラ"}       # TOシングルは前置きを剥がして合算
    assert subs["ピスタチオ"]["qty"] == 335                      # 284+51
    assert subs["ピスタチオ"]["sales"] == 16750                  # 14200+2550
    assert subs["クッキー＆バニラ"]["qty"] == 315                # 237+78


def test_ICE_HOTはその他へ_ペアリングは指定親へ():
    """商品名サブ指定は見出しマップより優先。ICE/HOT→その他の内訳、ペアリングリキュール→
    指定の親（数量ロールアップ無しなら親の点数はNone）。"""
    rules = {
        "other": "その他",
        "zero_groups": {"400ドリンク": "【セット400円紅茶】"},
        "sub_products": {
            "ICE": "その他の内訳", "HOT": "その他の内訳",
            "ペアリングリキュール無し": "ペアリング", "ペアリングリキュール有り": "ペアリング",
        },
        "categories": [{"name": "パフェ", "keywords": ["ペアリング"]},
                       {"name": "紅茶", "keywords": ["紅茶"]}],
    }
    items = [
        {"name": "ICE", "sales": 0, "qty": 279, "group": "32:400ドリンク"},
        {"name": "HOT", "sales": 1, "qty": 146, "group": "32:400ドリンク"},
        {"name": "ペアリングリキュール無し", "sales": 0, "qty": 1506, "group": "32:400ドリンク"},
    ]
    tops = {t["name"]: t for t in _nest_zero_subs(items, rules)}
    assert "その他の内訳" in tops and "ペアリング" in tops
    assert {s["name"] for s in tops["その他の内訳"]["subs"]} == {"ICE", "HOT"}
    assert classify_category("その他の内訳", rules) == "その他"
    assert tops["ペアリング"]["qty"] is None                     # 数量ロールアップ指定なし＝内訳キーのみ
    assert classify_category("ペアリング", rules) == "パフェ"


def test_zero_groupsが無い店は素通し():
    """zero_groups の無い店は変換しない（内訳もトップにそのまま並ぶ・後方互換）。"""
    items = [
        {"name": "サンデーダブル", "sales": 100000, "rank": "A", "qty": 200},
        {"name": "サンデーダブル ベリー", "sales": 0, "rank": None, "qty": 40,
         "group": "06:ジェラートサンデーダブル"},
    ]
    no_zero = {"other": "その他", "categories": []}
    assert _nest_zero_subs(items, no_zero) is items       # 変更なし（同一リスト）
    assert _nest_zero_subs(items, None) is items


def test_サブのcost粗利も内訳に引き継がれる():
    """サブに原価/粗利があれば内訳（subs）にも引き継ぐ（後方互換：無ければキー無し）。"""
    items = [
        {"name": "サンデーダブル", "sales": 100000, "rank": "A", "qty": 200},
        {"name": "サンデーダブル 有料トッピング", "sales": 8000, "rank": None, "qty": 20,
         "group": "06:ジェラートサンデーダブル", "cost": 3000, "gross": 5000},
    ]
    parent = _nest_zero_subs(items, ZERO_RULES)[0]
    sub = parent["subs"][0]
    assert sub == {"name": "サンデーダブル 有料トッピング", "qty": 20, "sales": 8000,
                   "cost": 3000, "gross": 5000}


# FW見出しを持たず売上つきでトップに出る内訳を、商品名でメインに畳む（sub_products）。
SUBP_RULES = {
    "other": "その他",
    "sub_products": {
        "ピスタチオ": "TOジェラート（風味・全TO共通）",
        "イチゴ増し": "LARGOケーキプレート",
    },
    "sub_products_contains": {
        "SN)": "2000ミニパフェ＆スコーンタルトセット",
    },
    "categories": [
        {"name": "パフェ", "keywords": ["パフェ", "フレジェ"]},
        {"name": "ジェラート", "keywords": ["TOジェラート"]},
        {"name": "ケーキ", "keywords": ["プレート"]},
    ],
}


def test_商品名完全一致でメインに畳み売上だけ足す():
    """FW見出しの無い内訳（増し・単品風味）を商品名の完全一致でメインに畳む。
    売上は親に+計上、点数は加算しない。サブはトップから消える。"""
    items = [
        {"name": "LARGOケーキプレート", "sales": 176400, "rank": "A", "qty": 100},
        {"name": "イチゴ増し", "sales": 211500, "rank": "A", "qty": 300},
    ]
    tops = _nest_zero_subs(items, SUBP_RULES)
    assert [t["name"] for t in tops] == ["LARGOケーキプレート"]
    parent = tops[0]
    assert parent["sales"] == 176400 + 211500   # 売上は合算
    assert parent["qty"] == 100                 # 点数は本体のまま
    assert parent["subs"] == [{"name": "イチゴ増し", "qty": 300, "sales": 211500}]


def test_完全一致は短い風味名でも別商品を巻き込まない():
    """"ピスタチオ" 完全一致は畳むが、"苺とピスタチオのフレジェ"（パフェ）は巻き込まない。"""
    items = [
        {"name": "ピスタチオ", "sales": 331250, "rank": "A", "qty": 400},
        {"name": "苺とピスタチオのフレジェ", "sales": 1097050, "rank": "A", "qty": 500},
    ]
    tops = _nest_zero_subs(items, SUBP_RULES)
    names = [t["name"] for t in tops]
    assert "苺とピスタチオのフレジェ" in names          # パフェはトップに残る
    assert "ピスタチオ" not in names                    # 単品風味は畳まれる
    parent = next(t for t in tops if t["name"] == "TOジェラート（風味・全TO共通）")
    assert parent["synthetic"] is True
    assert parent["sales"] == 331250


def test_部分一致は接頭辞でセットのサブに畳む():
    """"SN)" 接頭辞の紅茶選択を 2000セットのサブに畳む（部分一致）。"""
    items = [
        {"name": "SN)ルイボス", "sales": 900, "rank": None, "qty": 3},
        {"name": "SN)アサイベリー", "sales": 300, "rank": None, "qty": 1},
    ]
    tops = _nest_zero_subs(items, SUBP_RULES)
    assert len(tops) == 1
    assert tops[0]["name"] == "2000ミニパフェ＆スコーンタルトセット"
    assert tops[0]["sales"] == 1200
    assert len(tops[0]["subs"]) == 2


def test_sub_productsだけの店でも畳む():
    """zero_groups が無くても sub_products / sub_products_contains があれば効く。"""
    only_subp = {"other": "その他", "sub_products": {"イチゴ増し": "LARGOケーキプレート"},
                 "categories": []}
    items = [
        {"name": "LARGOケーキプレート", "sales": 100, "rank": "A", "qty": 10},
        {"name": "イチゴ増し", "sales": 50, "rank": None, "qty": 5},
    ]
    tops = _nest_zero_subs(items, only_subp)
    assert [t["name"] for t in tops] == ["LARGOケーキプレート"]
    assert tops[0]["sales"] == 150


def test_classify_category_group_overrides_name():
    """内訳（0円の選択商品）は FW区分見出し（groups）を商品名より優先して束ねる。"""
    rules = {
        "other": "その他",
        "groups": {"テイクアウトジェラート": "ジェラート", "TOシングル": "ジェラート"},
        "categories": [
            {"name": "パフェ", "keywords": ["サンデー", "パフェ"]},
            {"name": "ジェラート", "keywords": ["ジェラート"]},
        ],
    }
    # 素の風味名は商品名では当てられない → 所属グループで ジェラートへ。
    assert classify_category("ベリーマニア", rules, "20:テイクアウトジェラート") == "ジェラート"
    assert classify_category("TOシングル クッキー＆バニラ", rules, "22:TOシングル") == "ジェラート"
    # groups に載らない見出しは従来どおり商品名で判定（サンデー→パフェ）。
    assert classify_category("サンデーダブル ベリーマニア", rules, "06:ジェラートサンデーダブル") == "パフェ"
    # グループ無し（全商品由来の売れ筋）は商品名で判定。
    assert classify_category("丸ごと白桃のパフェ", rules, None) == "パフェ"


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
    # 売上は税込で入る→税抜へ割り戻す（÷1.10）。客数は人なのでそのまま。
    assert payload["hourly"].get(code, {}).get("12") == {"sales": round(942288 / 1.10), "covers": 389}


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
    # ABC商品売上は税込→税抜へ割り戻す（÷1.10）。
    assert got and got[0] == {"name": "生ビール中", "sales": round(682000 / 1.10), "rank": "A"}


def test_商品の原価粗利がproductsに付く(loaded, master):
    """FW ABC の原価金額・粗利金額が products の各商品に cost/gross（税抜・rounded int）で付く。"""
    from datetime import datetime, timezone

    from hansoku.model import (
        GRAIN_MONTH,
        KIND_FINAL,
        METRIC_PRODUCT_COST,
        METRIC_PRODUCT_GROSS,
        METRIC_PRODUCT_SALES,
        ActualRow,
    )

    code = master.active_codes[0]
    now = datetime.now(timezone.utc)

    def row(metric, value):
        return ActualRow(store_code=code, date=date(2026, 7, 1), grain=GRAIN_MONTH,
                         metric=metric, value=value, product_name="生ビール中",
                         product_category="A", kind=KIND_FINAL, source="fw_abc", ingested_at=now)

    loaded.replace_actuals([
        row(METRIC_PRODUCT_SALES, 682000.0),
        row(METRIC_PRODUCT_COST, 110000.0),
        row(METRIC_PRODUCT_GROSS, 572000.0),
    ])
    payload = build(loaded, master, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
    got = payload["products"].get(code)
    # 売上・原価・粗利いずれも税込→税抜へ割り戻す（÷1.10）。売上＝原価＋粗利 が保たれる。
    assert got and got[0] == {
        "name": "生ビール中",
        "sales": round(682000 / 1.10),
        "rank": "A",
        "cost": round(110000 / 1.10),
        "gross": round(572000 / 1.10),
    }


def test_原価粗利未取込なら商品にcostキーが無い(loaded, master):
    """後方互換：原価/粗利を取り込んでいない店では cost/gross キーは付かない。"""
    from datetime import datetime, timezone

    from hansoku.model import GRAIN_MONTH, KIND_FINAL, METRIC_PRODUCT_SALES, ActualRow

    code = master.active_codes[0]
    now = datetime.now(timezone.utc)
    loaded.replace_actuals([
        ActualRow(store_code=code, date=date(2026, 7, 1), grain=GRAIN_MONTH,
                  metric=METRIC_PRODUCT_SALES, value=396800.0, product_name="おすすめ刺盛",
                  product_category="A", kind=KIND_FINAL, source="fw_abc", ingested_at=now),
    ])
    payload = build(loaded, master, date_from=date(2025, 1, 1), date_to=date(2026, 12, 31))
    got = payload["products"].get(code)
    assert got and "cost" not in got[0] and "gross" not in got[0]


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
    # ABC商品売上は税込→税抜へ割り戻す（÷1.10）。
    assert group[0] == {"name": "名物もつ鍋", "sales": round(5200000 / 1.10), "rank": "A"}
    # 全店の擬似店舗は店舗別 products には混ざらない
    assert "_group" not in payload["products"]


def test_生成時刻が入る(payload):
    assert payload["generated_at"]


def test_JSONが小さい(payload):
    import json

    # 画面が一瞬で読める大きさに収まっていること（数百KB以内）
    size = len(json.dumps(payload, ensure_ascii=False))
    assert size < 500_000


def test_実績の無い店も一覧に載る(payload, master):
    """FW未連動の店（1766 ぎふや福岡天神・pos: uleji）を落とすと、その店に書いた
    施策が画面から丸ごと消え、書き手は入力ミスと区別できない。
    集計側は hasData で弾いているので、混ざっても数字は動かない。"""
    codes = {s["code"] for s in payload["stores"]}
    assert codes == set(master.active_codes)
    # 実績があるかを明示的に持つ（画面が「未取込」と書き分けられるように）。
    # 値は monthly に居るかどうかと必ず一致する。
    for s in payload["stores"]:
        assert s["has_actuals"] == (s["code"] in payload["monthly"])
        assert s["pos"]


def test_通称と読みがなが画面に渡る(payload, master):
    """正式名だけだと「梅田」で探せない（1006 は大衆寿司酒場すさび湯）。
    画面のさがす欄は通称・読みがなを当て判定に混ぜるので、書き出しに要る。
    表示名（name）は正式名のまま変えない。"""
    by_code = {s["code"]: s for s in payload["stores"]}
    for s in master.active:
        row = by_code[s.store_code]
        assert row["aliases"] == list(s.aliases)
        assert row["yomi"] == list(s.yomi)
        assert row["name"] == s.store_name
    # マスタに通称が入っている店が実際にある（空配列だけ通って気づかない、を防ぐ）
    assert any(row["aliases"] for row in payload["stores"])
    assert any(row["yomi"] for row in payload["stores"])
