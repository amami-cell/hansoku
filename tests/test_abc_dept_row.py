"""ABCの視覚行から『部門行』を見分ける判定のテスト。

ここを間違えると、合計行しか出ていないグリッドを『部門が取れた』と見なして
部門0件のまま通過してしまう（実際 1111/1151/1168 で再試行のログすら出さずに
0件になっていた）。取込とprobeで同じ判定を使うので、ここで固めておく。
"""

from hansoku.ingest.fw_daily import _abc_countable_dept, _abc_dept_match


def test_普通の部門行は数えられる():
    assert _abc_countable_dept(["01ナン・アヒージョ", "30.10%", "1,234", "567,890"])


def test_合計行は部門として数えない():
    # FWの部門グリッドは末尾に合計行を持つ。これを1件と数えると
    # 「部門が取れた」と誤判定して、実際は0件のまま抜けてしまう。
    cells = ["合計", "25.55%", "24,834", "10,066,542"]
    assert _abc_dept_match(cells) is not None  # 形としては部門行に見える
    assert not _abc_countable_dept(cells)      # が、数えてはいけない


def test_見出し行は数えない():
    assert not _abc_countable_dept(["部門", "0.00%", "0", "0"])


def test_数量も売上も0なら数えない():
    assert not _abc_countable_dept(["ランチ", "25.00%", "0", "0"])


def test_全商品グリッドの行は部門ではない():
    # "商品CD | 商品名 | 単価 | 原価 | 原価率% | 数量" の並び。数字の形は似ているが、
    # 部門名にあたる部分に ' | ' が入るので弾ける。
    cells = ["009010088900011321", "韓国冷麺", "935", "226.00", "24.17%", "112"]
    assert _abc_dept_match(cells) is None
    assert not _abc_countable_dept(cells)


def test_行が短くても落ちない():
    assert not _abc_countable_dept([])
    assert not _abc_countable_dept(["部門名"])
