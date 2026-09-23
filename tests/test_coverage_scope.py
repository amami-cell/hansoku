"""
店×月カバレッジの「分母」の決め方。

月別の行と店別の行で分母の決め方がずれていて、2026-09 に
店別は全23店 ✓ なのに月別は `22/22` と出た。1766 は FW未連動なので
「FWから取れない店」として月別の分母からだけ外れ続けていた。
実績は uレジから入っているので、数えないほうが間違い。
"""
from hansoku.ingest.fw_daily import coverage_in_scope


class Test分母に数えるか:
    def test_FWから取れる店は数える(self):
        assert coverage_in_scope(cannot=None, has_any=True)

    def test_FWから取れる店はデータが無くても数える(self):
        # ここを外すと「欠け」が分母ごと消えて、穴が見えなくなる。
        assert coverage_in_scope(cannot=None, has_any=False)

    def test_FW未連動でも実績が入っていれば数える(self):
        # 1766（uレジ）。これが今回のバグ。
        assert coverage_in_scope(cannot="FW未連動（実績はuレジ管理から）", has_any=True)

    def test_FW未連動で実績も無ければ対象外(self):
        # 取り漏れではないので「欠け」にはしない。
        assert not coverage_in_scope(cannot="FW未連動（実績はuレジ管理から）", has_any=False)
