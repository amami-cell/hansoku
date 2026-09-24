"""
関数の中で使っている名前が、ちゃんと存在するか。

`fw_daily.py` は4000行あり、編集を範囲指定で当てている。実際に
**`_watch_page` の定義ごと消して**しまい、テストは全部通ったのに本番で
`NameError` で落ちた（呼ぶテストが無く、import だけは通るため）。

ブラウザを動かす関数は手元で実行できないので、せめて「呼んでいる名前が
存在すること」だけは機械で確かめる。
"""
import ast
import builtins
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "hansoku" / "ingest" / "fw_daily.py"


def _undefined_names(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    known = set(dir(builtins))
    local: set[str] = set()

    for node in tree.body:  # モジュール直下の定義と import
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    local.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            local.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                local.add((a.asname or a.name).split(".")[0])

    bad = []
    # ⚠️ **モジュール直下の関数ごと**に、その中身をまとめて見る。
    #    入れ子の関数を単独で見ると、外側の変数を読んでいるだけのものを
    #    「未定義」と言い出す（最初そう書いて59件の誤検出を出した）。
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # その関数の中で束縛される名前（引数・代入・import・内包表記など）
        bound = {a.arg for a in node.args.args + node.args.kwonlyargs}
        if node.args.vararg:
            bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            bound.add(node.args.kwarg.arg)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                bound.add(sub.id)
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(sub.name)
                if sub is not node:
                    bound.update(a.arg for a in sub.args.args + sub.args.kwonlyargs)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                bound.update((a.asname or a.name).split(".")[0] for a in sub.names)
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                bound.add(sub.name)
            elif isinstance(sub, ast.Global):
                bound.update(sub.names)
            elif isinstance(sub, ast.Lambda):
                # ラムダの引数。これを忘れると `lambda r: ...` の r を
                # 「未定義」と言い出す。
                bound.update(a.arg for a in sub.args.args + sub.args.kwonlyargs)
                if sub.args.vararg:
                    bound.add(sub.args.vararg.arg)
                if sub.args.kwarg:
                    bound.add(sub.args.kwarg.arg)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id not in bound and sub.id not in local and sub.id not in known:
                    bad.append(f"{node.name}() の中の {sub.id}")
    return sorted(set(bad))


def test_使っている名前が全部ある():
    # ⚠️ ここが落ちたら、編集で定義を消したか綴りを間違えている。
    assert _undefined_names(SRC) == []


def _duplicate_defs(path: pathlib.Path) -> list[str]:
    """モジュール直下で同じ名前を2回定義していないか。

    範囲指定の編集を誤って**ブロックごと二重化**し、古い定義が後ろに残って
    そちらが勝った。テストは全部通り、本番だけ古い挙動になる。名前は
    存在するので `_undefined_names` では捕まらない。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen[node.name] = seen.get(node.name, 0) + 1
    return sorted(n for n, c in seen.items() if c > 1)


def test_同じ名前を二度定義していない():
    # ⚠️ ここが落ちたら、編集でブロックを二重化している。
    #    **後ろの定義が勝つ**ので、直したつもりの変更が効かない。
    assert _duplicate_defs(SRC) == []
