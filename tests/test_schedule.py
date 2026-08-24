"""施策スケジュール（config/schedule.yaml）の読み込みと正規化。"""
import textwrap

from hansoku.web.export import _parse_target, load_schedule


def test_目標値の正規化():
    assert _parse_target("5,000,000") == 5_000_000
    assert _parse_target("1600000円") == 1_600_000
    assert _parse_target(3000000) == 3_000_000
    assert _parse_target(None) is None
    assert _parse_target("") is None
    assert _parse_target("未定") is None


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
            kind: bounenkai
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
            kind: osusume
            start: "2026-09-01"
            end: "2026-09-02"
    """)
    # 対象店が1つも実在しない施策は黙って通さない
    assert load_schedule(master, path) == []


def test_未知の種類はその他開発に寄せる(master, tmp_path):
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
    assert camps[0]["kind"] == "dev"


def test_店名でも指定できる(master, tmp_path):
    # store_code ではなく店名で書いても find_by_name で解決される
    name = master.active[0].store_name
    code = master.active[0].store_code
    path = _write(tmp_path, f"""
        campaigns:
          - id: byname
            stores: ["{name}"]
            title: 店名指定
            kind: osusume
            start: "2026-09-01"
            end: "2026-09-30"
    """)
    camps = load_schedule(master, path)
    assert camps[0]["stores"] == [code]


def test_目標はyamlからも渡せる(master, tmp_path):
    code = master.active_codes[0]
    path = _write(tmp_path, f"""
        campaigns:
          - id: withgoal
            stores: ["{code}"]
            title: 目標つき
            kind: osusume
            start: "2026-09-01"
            end: "2026-09-30"
            target: "5,000,000"
          - id: nogoal
            stores: ["{code}"]
            title: 目標なし
            kind: osusume
            start: "2026-09-01"
            end: "2026-09-30"
    """)
    camps = {c["id"]: c for c in load_schedule(master, path)}
    assert camps["withgoal"]["target"] == 5_000_000
    assert camps["nogoal"]["target"] is None


def test_終了日省略は単日になる(master, tmp_path):
    code = master.active_codes[0]
    path = _write(tmp_path, f"""
        campaigns:
          - id: point
            stores: ["{code}"]
            title: 単日
            kind: gm
            start: "2026-09-01"
    """)
    camps = load_schedule(master, path)
    assert camps[0]["start"] == camps[0]["end"] == "2026-09-01"
