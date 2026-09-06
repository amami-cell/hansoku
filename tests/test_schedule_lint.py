"""台帳（config/schedule.yaml）の検査。

`load_schedule` は実在しない店コードも未知の kind も握りつぶす（画面を止めない
ための設計）。その代わり、書いた内容が意図どおり解釈されたかを lint で確かめる。
ここで固定するのは「黙って消える書き方をちゃんと見つけるか」。
"""
import textwrap

import pytest

from hansoku.schedule_lint import lint_schedule


def _write(tmp_path, body: str):
    p = tmp_path / "schedule.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _msgs(master, path, level="error"):
    return [f.message for f in lint_schedule(master, path) if f.level == level]


class Test黙って消える書き方を見つける:
    def test_実在しない店コードを見つける(self, master, tmp_path):
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-x, stores: ["9999"], title: t, kind: gm, start: "2026-01-01" }
        """)
        msgs = _msgs(master, path)
        assert any("9999" in m for m in msgs)
        assert any("画面に出ません" in m for m in msgs)

    def test_id重複を見つける(self, master, tmp_path):
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-a, stores: "all", title: t, kind: gm, start: "2026-01-01" }
              - { id: 2026-a, stores: "all", title: u, kind: gm, start: "2026-02-01" }
        """)
        assert any("重複" in m for m in _msgs(master, path))

    def test_未知のkindを見つける(self, master, tmp_path):
        """dev に寄せられるので、書き手は間違いに気づけない。"""
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-a, stores: "all", title: t, kind: bounenkaii, start: "2026-01-01" }
        """)
        assert any("bounenkaii" in m for m in _msgs(master, path))

    def test_終わりが始まりより前(self, master, tmp_path):
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-a, stores: "all", title: t, kind: gm, start: "2026-05-01", end: "2026-04-01" }
        """)
        assert any("より前" in m for m in _msgs(master, path))

    def test_日付の形が違う(self, master, tmp_path):
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-a, stores: "all", title: t, kind: gm, start: "2026/05/01" }
        """)
        assert any("YYYY-MM-DD" in m for m in _msgs(master, path))

    def test_年の無いidは警告どまり(self, master, tmp_path):
        """来年の同じ施策が今年の目標・メモを上書きするが、いま壊れてはいない。"""
        path = _write(tmp_path, """
            campaigns:
              - { id: r1006-osusume, stores: "all", title: t, kind: gm, start: "2026-01-01" }
        """)
        assert _msgs(master, path, "error") == []
        assert any("年が入っていません" in m for m in _msgs(master, path, "warn"))


class Test正しい台帳は素通しする:
    def test_問題なし(self, master, tmp_path):
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-1006-osusume, stores: ["1006"], title: 秋おすすめ, kind: osusume,
                  start: "2026-09-15", end: "2026-11-30", items: ["秋"] }
        """)
        assert lint_schedule(master, path) == []

    def test_本番の台帳にエラーが無い(self, master):
        assert _msgs(master, None) == []
