"""config/schedule.yaml の検査。

台帳はこの仕組みで唯一の人力入力なので、間違えたときに黙って消えるのがいちばん困る。
`load_schedule` は、実在しない店コードも未知の kind も例外にせず握りつぶす（画面を
止めないための設計で、それ自体は妥当）。その代わりに、書いた内容が意図どおり
解釈されたかをここで確かめる。

DBもFWも要らない。schedule.yaml と config/stores.yaml だけで走る。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .stores import StoreMaster
from .web.export import DEFAULT_SCHEDULE_PATH, VALID_KINDS

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" / "warn"
    where: str  # 何番目のどのidか
    message: str

    def __str__(self) -> str:
        mark = "✗" if self.level == "error" else "⚠"
        return f"  {mark} {self.where}  {self.message}"


def lint_schedule(master: StoreMaster, path: Path | str | None = None) -> list[Finding]:
    path = Path(path) if path else DEFAULT_SCHEDULE_PATH
    if not path.exists():
        return [Finding("error", str(path), "ファイルがありません")]
    with Path(path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    active = set(master.active_codes)
    findings: list[Finding] = []
    seen_ids: dict[str, int] = {}
    no_basis: list[str] = []

    for index, camp in enumerate(data.get("campaigns") or []):
        cid = str(camp.get("id") or "")
        where = f"[{index}] {cid or '(idなし)'}"

        if not cid:
            findings.append(Finding("error", where, "id がありません"))
        elif cid in seen_ids:
            findings.append(
                Finding(
                    "error", where,
                    f"id が {seen_ids[cid]} 番目と重複しています。"
                    "目標とメモの鍵は id@開始年 なので、開始年まで同じだと"
                    "2件が同じ目標・同じメモを共有します",
                )
            )
        else:
            seen_ids[cid] = index

        # 対象店。書いたのに解決できなかったトークンは、いま黙って捨てられている。
        stores_field = camp.get("stores", "all")
        if stores_field not in ("all", "*", None, ""):
            if not isinstance(stores_field, (list, tuple)):
                findings.append(
                    Finding("error", where, f"stores は配列か \"all\" です: {stores_field!r}")
                )
            else:
                resolved = []
                for raw in stores_field:
                    token = str(raw)
                    if token in active:
                        resolved.append(token)
                        continue
                    hit = master.find_by_name(token)
                    if hit and hit.store_code in active:
                        resolved.append(hit.store_code)
                        continue
                    findings.append(
                        Finding(
                            "error", where,
                            f"stores の {token!r} が稼働店に見つかりません（黙って捨てられます）",
                        )
                    )
                if not resolved:
                    findings.append(
                        Finding(
                            "error", where,
                            "対象店が1つも解決できないので、この施策は画面に出ません",
                        )
                    )

        kind = camp.get("kind", "dev")
        if kind not in VALID_KINDS:
            findings.append(
                Finding(
                    "error", where,
                    f"kind {kind!r} は未知です。dev として扱われます"
                    f"（使える値: {', '.join(sorted(VALID_KINDS))}）",
                )
            )

        start = str(camp.get("start") or "")
        end = str(camp.get("end") or start)
        if not _DATE_RE.match(start):
            findings.append(Finding("error", where, f"start が YYYY-MM-DD ではありません: {start!r}"))
        if end and not _DATE_RE.match(end):
            findings.append(Finding("error", where, f"end が YYYY-MM-DD ではありません: {end!r}"))
        if _DATE_RE.match(start) and _DATE_RE.match(end) and end < start:
            findings.append(Finding("error", where, f"end {end} が start {start} より前です"))

        # 効果の測り方。bucket（部門）か items（商品名）が無いと、その施策の
        # 効果は出せない（店全体の売上を出すと、同月に重なる他の施策と同じ数字に
        # なるだけなので、あえて出さない）。GM改定は店全体が範囲なので対象外。
        # 忘年会・ランチは kind から部門を推定できる。
        if (
            not (camp.get("items") or camp.get("bucket"))
            and kind not in ("gm", "bounenkai", "lunch", "closure")
        ):
            no_basis.append(f"{cid or index} {camp.get('title', '')}")

        if camp.get("items") is not None and not isinstance(camp["items"], (list, tuple)):
            findings.append(Finding("error", where, "items は配列です"))
        if camp.get("target") is not None:
            # 目標はアプリ（/api/targets → Neon）に一本化した。台帳にも書けると
            # 書き手がどちらに入れるか迷い、どちらが効いているのか分からなくなる。
            # 台帳＝施策の定義（いつ・どこで・何を・どう測るか）、
            # アプリ＝目標と振り返り（期中に何度も直すもの）。
            findings.append(
                Finding(
                    "error", where,
                    "target は台帳に書きません（画面から入力してください）。"
                    "台帳には残っても読まれないので、消してください",
                )
            )

    if no_basis:
        findings.append(
            Finding(
                "warn",
                f"効果 {len(no_basis)}件",
                "bucket（部門）も items（商品名）も無いので効果を出せません。"
                "例: " + " / ".join(no_basis[:4]) + "。"
                "fw.yml の abc-detail でその店・その月の部門と売れ筋を出せます",
            )
        )

    return findings


def report_schedule_lint(master: StoreMaster, path: Path | str | None = None) -> int:
    findings = lint_schedule(master, path)
    errors = [f for f in findings if f.level == "error"]
    warns = [f for f in findings if f.level == "warn"]

    print("=== 台帳（config/schedule.yaml）の検査 ===\n")
    if errors:
        print(f"直すべきもの {len(errors)}件:")
        for f in errors:
            print(f)
        print()
    if warns:
        print(f"気をつけたいもの {len(warns)}件:")
        for f in warns[:20]:
            print(f)
        if len(warns) > 20:
            print(f"  … 他 {len(warns) - 20}件")
        print()
    if not findings:
        print("問題ありません。")
    if errors:
        print(f"::error::[台帳] 直すべきもの {len(errors)}件")
        return 1
    return 0
