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
    # 目標はアプリ（Neon）に一本化したので、台帳から読み込まない。
    camps = {c["id"]: c for c in load_schedule(master, path)}
    assert "target" not in camps["withgoal"]
    assert "target" not in camps["nogoal"]


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


def test_目標とメモの鍵は開始年つき(master, tmp_path):
    """id は年をまたいで使い回されるので（秋おすすめは毎年ある）、
    目標とメモは id ではなく id@開始年 にぶら下げる。これが無いと
    来年の同じ施策が今年ぶんを上書きする。"""
    code = master.active_codes[0]
    path = _write(tmp_path, f"""
        campaigns:
          - id: r-osusume
            stores: ["{code}"]
            title: 秋おすすめ
            kind: osusume
            start: "2026-09-15"
            end: "2026-11-30"
    """)
    camps = load_schedule(master, path)
    assert camps[0]["key"] == "r-osusume@2026"

    path2 = _write(tmp_path, f"""
        campaigns:
          - id: r-osusume
            stores: ["{code}"]
            title: 秋おすすめ
            kind: osusume
            start: "2027-09-15"
            end: "2027-11-30"
    """)
    assert load_schedule(master, path2)[0]["key"] == "r-osusume@2027"


def test_目標とメモは書き出しに含めない(master, tmp_path):
    """dashboard.json はデザイン確認用プレビュー（認証なし）でもそのまま公開される。

    目標と要因メモは本部の内部情報なので焼き込まない。画面は
    /api/targets・/api/notes から Cloudflare Access の内側で直に読む。
    """
    code = master.active_codes[0]
    path = _write(tmp_path, f"""
        campaigns:
          - id: 2026-a
            stores: ["{code}"]
            title: t
            kind: gm
            start: "2026-01-01"
    """)
    camp = load_schedule(master, path)[0]
    assert "target" not in camp
    assert "memo" not in camp
