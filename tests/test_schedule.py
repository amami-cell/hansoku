"""施策スケジュール（config/schedule.yaml）の読み込みと正規化。"""
import textwrap

from hansoku.web.export import load_schedule


def _write(tmp_path, body: str):
    path = tmp_path / "schedule.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_ファイルが無ければ空(master, tmp_path):
    assert load_schedule(master, tmp_path / "none.yaml") == []


def test_全店指定は稼働店に展開される(master, tmp_path):
    path = _write(tmp_path, """
        campaigns:
          - id: all
            stores: all
            title: 全店施策
            kind: promo
            start: "2026-09-01"
            end: "2026-09-10"
    """)
    camps = load_schedule(master, path)
    assert len(camps) == 1
    assert camps[0]["scope_all"] is True
    assert set(camps[0]["stores"]) == set(master.active_codes)


def test_実在しない店コードは捨てる(master, tmp_path):
    path = _write(tmp_path, """
        campaigns:
          - id: ghost
            stores: ["0000000"]
            title: 幽霊店
            kind: fair
            start: "2026-09-01"
            end: "2026-09-02"
    """)
    # 対象店が1つも実在しない施策は黙って通さない
    assert load_schedule(master, path) == []


def test_未知の種類はpromoに寄せる(master, tmp_path):
    code = master.active_codes[0]
    path = _write(tmp_path, f"""
        campaigns:
          - id: weird
            stores: ["{code}"]
            title: 種類不明
            kind: nonsense
            start: "2026-09-01"
            end: "2026-09-02"
    """)
    camps = load_schedule(master, path)
    assert camps[0]["kind"] == "promo"


def test_終了日省略は単日になる(master, tmp_path):
    code = master.active_codes[0]
    path = _write(tmp_path, f"""
        campaigns:
          - id: point
            stores: ["{code}"]
            title: 単日
            kind: renewal
            start: "2026-09-01"
    """)
    camps = load_schedule(master, path)
    assert camps[0]["start"] == camps[0]["end"] == "2026-09-01"
