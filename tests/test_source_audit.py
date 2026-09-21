"""
source-audit の食い違い判定。

**本当の異常が埋もれないこと**が目的。実測（2026-08）では全24店で
fw_sheet（税抜）と fw_uriage_suii（税込）が約10%ずれ、毎回「食い違い23件」で
赤くなっていた。そのせいで 1766 の異常が23件の中に紛れ、発見が遅れた。
"""
from hansoku.ingest.fw_daily import KNOWN_GAPS, classify_gap


class Test既知の差:
    def test_税抜と税込は既知として数だけ数える(self):
        # 実測値そのまま（1006 の 2026-08）
        kind, gap = classify_gap({"fw_sheet": 20922282, "fw_uriage_suii": 23014088})
        assert kind == "known"
        assert 9.9 < gap < 10.1

    def test_帯を外れたら既知にしない(self):
        # 「いつもの差」で片付けると、税率では説明がつかないずれを見逃す。
        kind, _ = classify_gap({"fw_sheet": 100, "fw_uriage_suii": 200})
        assert kind == "check"

    def test_既知なのは税抜と税込の組だけ(self):
        assert list(KNOWN_GAPS) == [frozenset({"fw_sheet", "fw_uriage_suii"})]

    def test_別の組は同じ差でも要確認(self):
        # 10%ずれていても、組が違えば説明がつかない。
        kind, _ = classify_gap({"fw_sheet": 100, "pos_sheet": 110})
        assert kind == "check"


class Test片方だけ0:
    """**以前はここを黙って飛ばしていた。** `if lo and ...` で 0 を弾いていたため、
    「取れていない口がある」といういちばん見たい形をこぼしていた。
    実際 1766 の fw_sheet=0 / pos_sheet=実数 が一度も報告されなかった。

    ただし 0 が混ざっていること自体は異常ではない。**見たいのは「画面が 0 になるか」**。"""

    def test_採用が0なら必ず出す(self):
        # 実際に起きた事故。kind が第1キーで、FWの「確定の 0」が実数を押しのけた。
        kind, _ = classify_gap({"fw_sheet": 0, "pos_sheet": 12640890}, adopted=0)
        assert kind == "zero"

    def test_採用が実数なら画面は正しいので赤くしない(self):
        # 1766 は FW未連動。fw_sheet が 0 を書くのは仕様どおりで、
        # pos_sheet の実数を採る。毎回赤くすると既知の差を外した意味が無くなる。
        kind, _ = classify_gap(
            {"fw_sheet": 0, "pos_sheet": 12640890}, adopted=12640890
        )
        assert kind == "zero_ok"

    def test_採用値が分からなければ鳴らす側に倒す(self):
        # 見逃すと画面が黙って 0 になる。分からないなら出す。
        kind, _ = classify_gap({"fw_sheet": 0, "pos_sheet": 12640890})
        assert kind == "zero"

    def test_税抜税込の組でも0なら既知にしない(self):
        # 既知の組だからといって、0 を見逃してよい理由にはならない。
        kind, _ = classify_gap({"fw_sheet": 0, "fw_uriage_suii": 23014088}, adopted=0)
        assert kind == "zero"

    def test_両方0は静かにする(self):
        # 書かれていないだけ。鳴らしても打ち手が無い。
        assert classify_gap({"a": 0, "b": 0})[0] == "skip"

    def test_採用が実数でも差の判定より0を先に見る(self):
        # 0 と実数の「差」を % で語っても意味が無い。帯の判定へ落とさない。
        kind, gap = classify_gap(
            {"fw_sheet": 0, "fw_uriage_suii": 23014088}, adopted=23014088
        )
        assert kind == "zero_ok"
        assert gap == 100.0


class Test鳴らさない場合:
    def test_口が1つなら食い違いようがない(self):
        assert classify_gap({"fw_sheet": 100})[0] == "skip"

    def test_差が小さければ流す(self):
        assert classify_gap({"a": 1000, "b": 1005}, gap_pct=1.0)[0] == "skip"

    def test_しきい値は指定できる(self):
        vals = {"a": 1000, "b": 1005}
        assert classify_gap(vals, gap_pct=1.0)[0] == "skip"
        assert classify_gap(vals, gap_pct=0.1)[0] == "check"
