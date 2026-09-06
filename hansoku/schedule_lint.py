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
# id に年が入っていれば、来年の同じ施策を別idにできる。目標・メモは campaign_id に
# ぶら下がるので、年が無いと 2027年の秋おすすめが 2026年の目標を上書きする。
_YEAR_IN_ID_RE = re.compile(r"(^|[^0-9])20\d{2}([^0-9]|$)")


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
    no_year: list[str] = []

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
                    "目標とメモは id にぶら下がるので、2件が同じ目標・同じメモを共有します",
                )
            )
        else:
            seen_ids[cid] = index
            if not _YEAR_IN_ID_RE.search(cid):
                no_year.append(cid)

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

        if camp.get("items") is not None and not isinstance(camp["items"], (list, tuple)):
            findings.append(Finding("error", where, "items は配列です"))
        if camp.get("target") is not None:
            try:
                float(camp["target"])
            except (TypeError, ValueError):
                findings.append(Finding("error", where, f"target が数値ではありません: {camp['target']!r}"))

    # 年の欠落は1件ずつ出すと埋もれるので、まとめて1行にする。
    if no_year:
        findings.append(
            Finding(
                "warn",
                f"id {len(no_year)}件",
                "id に年が入っていません（例 r1006-osusume → 2026-1006-osusume）。"
                "目標とメモは id にぶら下がるので、来年の同じ施策を同じ id で書くと"
                "今年ぶんを上書きします。別 id にすれば今度は今年のメモが迷子になります",
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
