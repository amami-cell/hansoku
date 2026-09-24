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
HEAD = ["コード", "名称", "標準税率 10%込", "軽減税率 8%込", "税抜",
        "原価", "部門", "グループ", "消費税", "ユーザー用メニューコード", "分析用コード"]
CODE_COL = 10


def _row(code: str, name: str = "からあげ") -> list[str]:
    r = [""] * len(HEAD)
    r[1] = name
    r[CODE_COL] = code
    return r


class Test見出しを見つける:
    def test_分析用コードの列を見出しから探す(self):
        got = analysis_code_summary([HEAD, _row("15")])
        assert got["col"] == CODE_COL
        assert got["header_row"] == 0

    def test_見出しが先頭行とは限らない(self):
        # 実測では1〜2行目に分かれていた。決め打ちにすると全行を本文と誤る。
        got = analysis_code_summary([["分析用コード設定"], HEAD, _row("15")])
        assert got["header_row"] == 1
        assert got["total"] == 1

    def test_列が無ければNone(self):
        assert analysis_code_summary([["コード", "名称"], ["1", "からあげ"]]) is None


class Test入力漏れ:
    def test_空欄は漏れ(self):
        got = analysis_code_summary([HEAD, _row("15", "からあげ"), _row("", "新メニュー")])
        assert got["blank"] == ["新メニュー"]
        assert got["filled"] == 1

    def test_空白だけも漏れ(self):
        # 見た目は入っていても中身が空白なら、FWの集計には効かない。
        got = analysis_code_summary([HEAD, _row("   ", "からあげ")])
        assert got["blank"] == ["からあげ"]

    def test_0は漏れではない(self):
        # 「0」というコードが割り当ててあるなら、それは入力済み。
        # ここを falsy で弾くと、入っているものを漏れと言い出す。
        got = analysis_code_summary([HEAD, _row("0", "お通し")])
        assert got["blank"] == []
        assert got["filled"] == 1

    def test_空行は数えない(self):
        # CSVの末尾に空行が付くことがある。これを漏れに数えると毎回赤くなる。
        got = analysis_code_summary([HEAD, _row("15"), ["", "", "", ""], []])
        assert got["total"] == 1
        assert got["blank"] == []

    def test_列が短い行も漏れとして拾う(self):
        # 末尾の空欄がCSVで落ちることがある。行が短い＝コードが無い。
        got = analysis_code_summary([HEAD, ["1", "からあげ"]])
        assert got["blank"] == ["からあげ"]


class Test使われているコード:
    def test_重複を畳んで並べる(self):
        rows = [HEAD, _row("15"), _row("15"), _row("2"), _row("")]
        got = analysis_code_summary(rows)
        assert got["codes"] == ["15", "2"]


class Test見出しを当て推量しない:
    """部分一致で探すと、タイトル行の「分析用コード設定」を見出しと誤認する。
    そうなると列位置が全部ずれ、**アラートが静かに嘘をつく**。"""

    def test_設定という見出しには当てない(self):
        got = analysis_code_summary([["分析用コード設定"], HEAD, _row("15")])
        assert got["header_row"] == 1
        assert got["col"] == CODE_COL

    def test_それらしい列が無ければ当て推量しない(self):
        # 画面が変わって列名が消えたとき、勝手に0列目を使われるのが最悪。
        assert analysis_code_summary([["分析用コード設定"], ["コード", "名称"]]) is None

    def test_空白や改行が混ざっても見つける(self):
        head = list(HEAD)
        head[CODE_COL] = "分析用\nコード"
        got = analysis_code_summary([head, _row("15")])
        assert got["col"] == CODE_COL
