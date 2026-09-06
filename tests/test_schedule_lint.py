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

    def test_台帳に目標を書いたらエラー(self, master, tmp_path):
        """目標はアプリ（Neon）に一本化した。2箇所あると書き手が迷う。"""
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-a, stores: "all", title: t, kind: gm, start: "2026-01-01",
                  target: 5000000 }
        """)
        assert any("target は台帳に書きません" in m for m in _msgs(master, path))

    def test_年の無いidはもう警告しない(self, master, tmp_path):
        """鍵が id@開始年 になったので、id を年込みに改名する必要は無い。"""
        path = _write(tmp_path, """
            campaigns:
              - { id: r1006-osusume, stores: "all", title: t, kind: gm, start: "2026-01-01" }
        """)
        assert _msgs(master, path, "error") == []
        assert not any("年が入っていません" in m for m in _msgs(master, path, "warn"))


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


class Test効果の測り方:
    def test_bucketもitemsも無ければ警告(self, master, tmp_path):
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-a, stores: "all", title: 秋おすすめ, kind: osusume, start: "2026-09-15" }
        """)
        assert any("効果を出せません" in m for m in _msgs(master, path, "warn"))

    def test_itemsがあれば警告しない(self, master, tmp_path):
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-a, stores: "all", title: 秋おすすめ, kind: osusume,
                  start: "2026-09-15", items: ["秋の味覚"] }
        """)
        assert not any("効果を出せません" in m for m in _msgs(master, path, "warn"))

    def test_GM改定と忘年会は対象外(self, master, tmp_path):
        """GM改定は店全体が範囲、忘年会は kind から部門を推定できる。"""
        path = _write(tmp_path, """
            campaigns:
              - { id: 2026-a, stores: "all", title: GM改定, kind: gm, start: "2026-10-01" }
              - { id: 2026-b, stores: "all", title: 忘年会, kind: bounenkai, start: "2026-10-01" }
        """)
        assert not any("効果を出せません" in m for m in _msgs(master, path, "warn"))
