"""制作物ギャラリー（config/creatives.yaml）の読み込みと正規化。"""
import textwrap

from hansoku.web.export import load_creatives


def _write(tmp_path, body: str):
    path = tmp_path / "creatives.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_ファイルが無ければ空(master, tmp_path):
    assert load_creatives(master, [], tmp_path / "none.yaml") == []


def test_file無しは出さない(master, tmp_path):
    path = _write(tmp_path, """
        creatives:
          - id: nofile
            title: PDF未登録
          - id: notitle
            file: creatives/2026/x/a.pdf
    """)
    # file が無い / title が無い項目はギャラリーに出さない
    assert load_creatives(master, [], path) == []


def test_全店ものはscope_all(master, tmp_path):
    path = _write(tmp_path, """
        creatives:
          - id: grand
            title: グランドメニュー
            file: creatives/2026/misc/grand.pdf
    """)
    got = load_creatives(master, [], path)
    assert len(got) == 1
    assert got[0]["scope_all"] is True
    assert set(got[0]["stores"]) == set(master.active_codes)
    assert got[0]["url"] == "/creatives/2026/misc/grand.pdf"


def test_店舗を明示できる(master, tmp_path):
    code = master.active_codes[0]
    path = _write(tmp_path, f"""
        creatives:
          - id: one
            title: 一店だけ
            stores: ["{code}"]
            file: creatives/2026/misc/one.pdf
    """)
    got = load_creatives(master, [], path)
    assert got[0]["stores"] == [code]
    assert got[0]["scope_all"] is False


def test_施策に紐づけると対象店と種類を継承(master, tmp_path):
    code = master.active_codes[0]
    campaigns = [
        {"id": "summer", "title": "夏おすすめ", "stores": [code],
         "scope_all": False, "kind": "osusume"},
    ]
    path = _write(tmp_path, """
        creatives:
          - id: flyer
            title: 夏チラシ
            campaign: summer
            file: creatives/2026/summer/flyer.pdf
    """)
    got = load_creatives(master, campaigns, path)
    assert got[0]["stores"] == [code]
    assert got[0]["kind"] == "osusume"
    assert got[0]["campaign_id"] == "summer"
    assert got[0]["campaign_title"] == "夏おすすめ"


def test_未知の種類はdevに寄せる(master, tmp_path):
    path = _write(tmp_path, """
        creatives:
          - id: weird
            title: 種類不明
            kind: nonsense
            file: creatives/2026/misc/w.pdf
    """)
    assert load_creatives(master, [], path)[0]["kind"] == "dev"


def test_掲出日の新しい順に並ぶ(master, tmp_path):
    path = _write(tmp_path, """
        creatives:
          - id: old
            title: 古い
            date: "2026-01-01"
            file: creatives/2026/misc/old.pdf
          - id: new
            title: 新しい
            date: "2026-08-01"
            file: creatives/2026/misc/new.pdf
    """)
    got = load_creatives(master, [], path)
    assert [c["id"] for c in got] == ["new", "old"]
