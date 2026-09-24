"""
分析用コードの入力漏れ判定。

店長会資料のアラカルトは引き算で出る。

    アラカルト客数 = 総客数 －宴会 －ランチ －食べ飲み －ツアー －単品飲み放題 －テイクアウト

なので**コードを付け忘れたメニューは消えずにアラカルトに紛れ込む**。
どこも空欄にならないため資料は完成して見え、間違いに気づけない。
ここの判定が「何を漏れとみなすか」を決める。
"""
from hansoku.ingest.fw_daily import analysis_code_summary

# 実測の見出し（マスタ管理→販売マスタ→分析用コード設定 の CSV出力）
HEAD = ["店舗コード", "店舗名", "メニューコード", "名称", "標準税率10%込",
        "軽減税率8%込", "税抜", "原価", "部門コード", "部門名称",
        "グループコード", "グループ名称", "消費税", "ユーザーコード", "分析用コード"]
CODE_COL, NAME_COL, STORE_COL = 14, 3, 0


def _row(code: str, menu: str = "からあげ", store: str = "0001006") -> list[str]:
    r = [""] * len(HEAD)
    r[STORE_COL] = store
    r[1] = "大衆寿司酒場すさび湯"
    r[NAME_COL] = menu
    r[CODE_COL] = code
    return r


class Test見出しから列を引く:
    def test_分析用コードの列(self):
        got = analysis_code_summary([HEAD, _row("15")])
        assert got["col"] == CODE_COL and got["header_row"] == 0

    def test_メニュー名は名称の列から取る(self):
        # ⚠️ 1列目と決め打ちしていたら、そこは店舗名だった。
        #    漏れの一覧に店名が78個並ぶという形で実際に出た。
        got = analysis_code_summary([HEAD, _row("", "自家製ポテサラ")])
        assert got["blank"] == [{"store_code": "1006",
                                 "store_name": "大衆寿司酒場すさび湯",
                                 "menu": "自家製ポテサラ"}]

    def test_設定という見出しには当てない(self):
        # 部分一致だとタイトル行「分析用コード設定」に当たり、全部ずれる。
        got = analysis_code_summary([["分析用コード設定"], HEAD, _row("15")])
        assert got["header_row"] == 1 and got["col"] == CODE_COL

    def test_それらしい列が無ければ当て推量しない(self):
        assert analysis_code_summary([["分析用コード設定"], ["コード", "名称"]]) is None

    def test_空白や改行が混ざっても見つける(self):
        head = list(HEAD)
        head[CODE_COL] = "分析用\nコード"
        assert analysis_code_summary([head, _row("15")])["col"] == CODE_COL


class Test入力漏れ:
    def test_空欄は漏れ(self):
        got = analysis_code_summary([HEAD, _row("15", "からあげ"), _row("", "新メニュー")])
        assert [b["menu"] for b in got["blank"]] == ["新メニュー"]
        assert got["filled"] == 1

    def test_空白だけも漏れ(self):
        got = analysis_code_summary([HEAD, _row("   ", "からあげ")])
        assert [b["menu"] for b in got["blank"]] == ["からあげ"]

    def test_0は漏れではない(self):
        # 「0」が割り当ててあるなら入力済み。falsy で弾くと入っているものを
        # 漏れと言い出す。
        got = analysis_code_summary([HEAD, _row("0", "お通し")])
        assert got["blank"] == [] and got["filled"] == 1

    def test_空行は数えない(self):
        # CSVの末尾に空行が付くことがある。数えると毎回赤くなる。
        got = analysis_code_summary([HEAD, _row("15"), ["", "", ""], []])
        assert got["total"] == 1 and got["blank"] == []

    def test_列が短い行も漏れとして拾う(self):
        got = analysis_code_summary([HEAD, ["0001006", "すさび湯", "x", "からあげ"]])
        assert [b["menu"] for b in got["blank"]] == ["からあげ"]


class Test店ごとにまとめる:
    def test_全店CSVでも店ごとに数えられる(self):
        # 『全店』で落とせたときに、どの店が何件かをそのまま出せること。
        rows = [HEAD,
                _row("", "A", "0001006"), _row("", "B", "0001006"),
                _row("", "C", "0001766"), _row("15", "D", "0001766")]
        got = analysis_code_summary(rows)
        assert got["by_store"] == {"1006": 2, "1766": 1}

    def test_店舗コードの0埋めは外す(self):
        # 店舗マスタは "1006"、FWは "0001006"。揃えないと突き合わない。
        got = analysis_code_summary([HEAD, _row("", "A", "0001766")])
        assert got["blank"][0]["store_code"] == "1766"


class Test使われているコード:
    def test_重複を畳んで数の順に並べる(self):
        rows = [HEAD, _row("15"), _row("15"), _row("2"), _row("")]
        assert analysis_code_summary(rows)["codes"] == ["2", "15"]
