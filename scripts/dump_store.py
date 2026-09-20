#!/usr/bin/env python3
"""1店ぶんの「画面に出せる材料」を棚卸しする。

年間→月→部門→商品ドリルを作る前に、その店で実際に何月ぶん・どの粒度の
データが warehouse にあるかを確かめる。読み取りだけ。STORE 環境変数で店コード
（既定 1160=ルクアLargo）。
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hansoku.db import get_warehouse
from hansoku.db.warehouse import AggregateQuery
from hansoku.model import (
    GRAIN_MONTH,
    METRIC_COVERS,
    METRIC_DEPT_QTY,
    METRIC_DEPT_SALES,
    METRIC_PRODUCT_QTY,
    METRIC_PRODUCT_SALES,
    METRIC_SALES,
    METRIC_SALES_BUDGET,
)
from hansoku.settings import load_settings

CODE = os.environ.get("STORE", "1160")
FROM = date(2023, 8, 1)
TO = date(2026, 12, 31)


def months_for(wh, metric, group=("date",)):
    rows = wh.aggregate(
        AggregateQuery(
            date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
            metrics=[metric], store_codes=[CODE], group_by=("store_code", "date"),
        )
    )
    return sorted({r["date"].strftime("%Y-%m") for r in rows})


def main() -> int:
    settings = load_settings()
    with get_warehouse(settings) as wh:
        print(f"== 店 {CODE} / 窓 {FROM}〜{TO} ==")
        for label, metric in [
            ("売上(monthly)", METRIC_SALES),
            ("売上予算(budget)", METRIC_SALES_BUDGET),
            ("客数(covers)", METRIC_COVERS),
            ("部門売上(dept_sales)", METRIC_DEPT_SALES),
            ("部門数量(dept_qty)", METRIC_DEPT_QTY),
            ("商品売上(product_sales)", METRIC_PRODUCT_SALES),
        ]:
            ms = months_for(wh, metric)
            print(f"\n{label}: {len(ms)}ヶ月")
            print("  " + (", ".join(ms) if ms else "（なし）"))

        # 一番新しい部門月の中身（部門名一覧）
        drows = wh.aggregate(
            AggregateQuery(
                date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
                metrics=[METRIC_DEPT_SALES], store_codes=[CODE],
                group_by=("store_code", "date", "product_name", "product_category"),
            )
        )
        by_m: dict[str, list] = {}
        for r in drows:
            m = r["date"].strftime("%Y-%m")
            by_m.setdefault(m, []).append(
                (r["product_name"], round(r["value"]), r["product_category"])
            )
        if by_m:
            latest = max(by_m)
            print(f"\n== 部門売上の最新月 {latest} の部門一覧（{len(by_m[latest])}件）==")
            for name, val, cat in sorted(by_m[latest], key=lambda x: -x[1]):
                print(f"  {name}: {val:,}円 (原価率{cat})")

        # 一番新しい商品月の中身（上位20）
        prows = wh.aggregate(
            AggregateQuery(
                date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
                metrics=[METRIC_PRODUCT_SALES], store_codes=[CODE],
                group_by=("store_code", "date", "product_name", "product_category"),
            )
        )
        pby_m: dict[str, list] = {}
        for r in prows:
            m = r["date"].strftime("%Y-%m")
            pby_m.setdefault(m, []).append(
                (r["product_name"], round(r["value"]), r["product_category"])
            )
        if pby_m:
            latest = max(pby_m)
            items = sorted(pby_m[latest], key=lambda x: -x[1])[:20]
            print(f"\n== 商品売上の最新月 {latest} の上位{len(items)} ==")
            for name, val, cat in items:
                print(f"  [{cat}] {name}: {val:,}円")

        # 0円サブ商品（売上0・点数あり＝メイン商品の選択メニュー内訳）の一覧。
        # どのメイン商品のサブかを人が対応付けるための材料。FW区分見出し付きで出す。
        qrows = wh.aggregate(
            AggregateQuery(
                date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
                metrics=[METRIC_PRODUCT_QTY], store_codes=[CODE],
                group_by=("store_code", "date", "product_name", "product_category"),
            )
        )
        qby_m: dict[str, dict] = {}
        # 画面(export)と同じく、点数は (店,月,商品名) で見出し跨ぎ合算、見出しは最後の値を採る。
        import re as _re0
        qsum_by_m: dict[str, dict] = {}   # m -> name -> 合算点数
        glast_by_m: dict[str, dict] = {}  # m -> name -> FW見出し("NN:名前")
        for r in qrows:
            m = r["date"].strftime("%Y-%m")
            nm = r["product_name"]
            qby_m.setdefault(m, {})[nm] = (round(r["value"]), r["product_category"])
            qsum_by_m.setdefault(m, {})[nm] = qsum_by_m.setdefault(m, {}).get(nm, 0) + r["value"]
            cat = r.get("product_category")
            if cat and _re0.match(r"^\s*\d+\s*[:：]", str(cat)):
                glast_by_m.setdefault(m, {})[nm] = str(cat)
        # 対象月: MONTHS 指定があればそれ、無ければ最新の商品月。
        zmonths = [m.strip() for m in os.environ.get("MONTHS", "").split(",") if m.strip()]
        if not zmonths and pby_m:
            zmonths = [max(pby_m)]
        for m in zmonths:
            sales_names = {n for n, v, c in pby_m.get(m, []) if v > 0}
            zeros = [(n, q, cat) for n, (q, cat) in qby_m.get(m, {}).items()
                     if q > 0 and n not in sales_names]
            zeros.sort(key=lambda x: (str(x[2] or ""), -x[1]))
            print(f"\n== {m} の0円サブ商品（売上0・点数あり）{len(zeros)}件 ==")
            if not zeros:
                print("  （なし）")
            for name, q, grp in zeros:
                print(f"  [{grp or '区分なし'}] {name}: {q:,}点")

        # 品目区分「その他」に落ちる商品の全件（全期間で重複集約）。keyword追加で各区分へ
        # 吸収するための材料。売れ筋（売上>0）だけを対象にする。
        try:
            from hansoku.web.export import classify_category, load_store_categories
            rules = load_store_categories().get(CODE)
        except Exception as e:  # noqa: BLE001
            rules = None
            print(f"\n（品目区分ルール読込に失敗: {e}）")
        if rules:
            other_name = rules.get("other", "その他")
            agg: dict[str, float] = {}
            for m, items in pby_m.items():
                for name, val, cat in items:
                    if val <= 0:
                        continue
                    if classify_category(name, rules) == other_name:
                        agg[name] = agg.get(name, 0) + val
            rows = sorted(agg.items(), key=lambda x: -x[1])
            print(f"\n== 品目区分「{other_name}」に落ちる商品 全{len(rows)}種（全期間・売上>0）==")
            if not rows:
                print("  （なし）")
            for name, val in rows:
                print(f"  {name}: 累計{round(val):,}円")

        # PROBE_NAME 環境変数で指定した商品名を月ごとに追う（正体調査用）。
        # 売上（税込）・点数・FW区分見出し(product_category)を月別に並べる。部分一致も拾う。
        probes = [p.strip() for p in os.environ.get("PROBE_NAME", "").split(",") if p.strip()]
        for probe in probes:
            print(f"\n== 商品名『{probe}』を月別に追跡（部分一致含む）==")
            hit = False
            for m in sorted(set(pby_m) | set(qby_m)):
                smap = {n: (v, c) for n, v, c in pby_m.get(m, [])}
                for name in sorted(set(smap) | set(qby_m.get(m, {}))):
                    if probe not in name:
                        continue
                    hit = True
                    sval, rank = smap.get(name, (0, None))
                    qty, grp = qby_m.get(m, {}).get(name, (0, None))
                    print(f"  {m}  [{grp or '見出し無し'}] {name}: "
                          f"売上{sval:,}円 / {qty:,}点 / ランク{rank or '-'}")
            if not hit:
                print("  （該当なし）")

        # FW区分見出し（"NN:名前"）別の商品一覧。区分（アルコール/紅茶 等）を立てる材料。
        # qty行の product_category が見出しのものだけ拾い、見出し→商品→累計点/累計円で集約。
        byhd: dict[str, dict[str, list]] = {}
        import re as _re
        for m, dd in qby_m.items():
            smap = {n: v for n, v, c in pby_m.get(m, [])}
            for name, (qty, grp) in dd.items():
                if grp and _re.match(r"^\s*\d+\s*[:：]", str(grp)):
                    a = byhd.setdefault(str(grp), {}).setdefault(name, [0, 0])
                    a[0] += qty
                    a[1] += smap.get(name, 0)
        print(f"\n== FW区分見出し別 商品一覧（{len(byhd)}見出し・全期間集約）==")
        for hd in sorted(byhd):
            items_hd = sorted(byhd[hd].items(), key=lambda x: -x[1][1])
            print(f"  【{hd}】{len(items_hd)}品")
            for name, (q, v) in items_hd:
                print(f"    {name}: 累計{v:,}円 / {q:,}点")

        # 画面(export)と同じパイプライン（同名×別見出しの売上行を保持し、点数は名前で合算・
        # サブ畳み→合算→品目区分）を忠実に再現。各月の区分結果と、指定月の中身を出す。
        try:
            from hansoku.web.export import (
                ZERO_SUB_OTHER_PARENT as ZERO_OTHER_NAME,
                _categories_for_month, _nest_zero_subs,
            )
        except Exception as e:  # noqa: BLE001
            _nest_zero_subs = None
            print(f"\n（品目区分パイプライン読込に失敗: {e}）")

        def _items_for(m: str) -> list[dict]:
            """export と同じ組み立て: 売上行(name×見出し)ごとに1件＋点数のみ商品。"""
            out: list[dict] = []
            seen: set[str] = set()
            qsum = qsum_by_m.get(m, {})
            glast = glast_by_m.get(m, {})
            for name, sval, _cat in pby_m.get(m, []):
                seen.add(name)
                q = qsum.get(name)
                out.append({"name": name, "sales": round(sval), "rank": None,
                            "qty": round(q) if q else None, "group": glast.get(name)})
            for name, q in qsum.items():        # 売上行の無い点数のみ商品
                if name in seen or not q:
                    continue
                out.append({"name": name, "sales": 0, "rank": None,
                            "qty": round(q), "group": glast.get(name)})
            return out

        if rules and _nest_zero_subs is not None:
            other_name = rules.get("other", "その他")
            months_all = sorted(set(pby_m) | set(qsum_by_m))
            recent = months_all[-4:]
            print(f"\n== 品目区分の最終結果（画面と同じ集計・直近{len(recent)}ヶ月）==")
            for m in recent:
                items = _nest_zero_subs(_items_for(m), rules)
                total = sum(p["sales"] for p in items)
                cats = _categories_for_month(items, rules, total)
                line = "  ".join(f"{c['name']}={c['sales']:,}円({c['count']}品)" for c in cats)
                print(f"\n  [{m}] 合計{round(total):,}円")
                print(f"    {line}")
                others = sorted(
                    [(p["name"], round(p["sales"])) for p in items
                     if classify_category(p.get("name", ""), rules, p.get("group")) == other_name
                     and (p.get("sales") or 0) > 0], key=lambda x: -x[1])
                if others:
                    print(f"    └ その他の売上つき中身 {len(others)}件: "
                          + ", ".join(f"{n}={v:,}円" for n, v in others))
                else:
                    print("    └ その他に売上つき商品なし（売上0）")

            # 詳細ダンプ: DETAIL_MONTH（既定=直近の完全月）について
            #  (A) その他に入る全商品（売上0含む）、(B) 各メインに畳んだサブ一覧。
            detail_m = os.environ.get("DETAIL_MONTH", "").strip()
            if not detail_m:
                detail_m = recent[-2] if len(recent) >= 2 else (recent[-1] if recent else "")
            if detail_m:
                items = _nest_zero_subs(_items_for(detail_m), rules)
                dtotal = sum(p["sales"] for p in items)
                dcats = _categories_for_month(items, rules, dtotal)
                print(f"\n== [{detail_m}] 品目区分の売上構成（合計{round(dtotal):,}円）==")
                for c in dcats:
                    print(f"  {c['name']}: {c['sales']:,}円 ({c['count']}品 / "
                          f"{round(c['share']*100,1)}%)")
                others_all = sorted(
                    [(p["name"], round(p.get("sales") or 0), p.get("qty"), p.get("group"))
                     for p in items
                     if classify_category(p.get("name", ""), rules, p.get("group")) == other_name],
                    key=lambda x: (-x[1], -(x[2] or 0)))
                print(f"\n== [{detail_m}] その他に入る全商品（売上0含む・{len(others_all)}件）==")
                if not others_all:
                    print("  （なし）")
                for name, val, qty, grp in others_all:
                    print(f"  {name}: {val:,}円 / {qty or 0:,}点 [{grp or '見出し無し'}]")

                parents = [p for p in items if p.get("subs")]
                parents.sort(key=lambda p: -(p.get("sales") or 0))
                nsub = sum(len(p["subs"]) for p in parents)
                print(f"\n== [{detail_m}] 各メイン商品に割り振ったサブ"
                      f"{len(parents)}メイン・計{nsub}サブ ==")
                for p in parents:
                    tag = "（表示専用の束ね親）" if p.get("synthetic") else ""
                    q = p.get("qty")
                    qtxt = f" / 数量{q:,}点" if q else ""
                    subs = sorted(p["subs"], key=lambda s: -(s.get("qty") or 0))
                    print(f"\n  ■ {p['name']}  売上{round(p.get('sales') or 0):,}円{qtxt} / "
                          f"サブ{len(subs)}件{tag}")
                    for s in subs:
                        sv = round(s.get("sales") or 0)
                        sq = s.get("qty") or 0
                        extra = f" / 売上{sv:,}円" if sv else ""
                        print(f"      ・{s.get('name')}: {sq:,}点{extra}")

            # 監査: AUDIT_FROM〜AUDIT_TO（既定 2025-01〜2026-08）の全月で、割り振り切れずに
            # 「その他」へ落ちる商品を洗い出す。相談材料。
            af = os.environ.get("AUDIT_FROM", "2025-01")
            at = os.environ.get("AUDIT_TO", "2026-08")
            amonths = [m for m in months_all if af <= m <= at]
            if amonths:
                sell_other: dict[str, list] = {}   # 売上つきでその他に落ちた実売れ（本当の未割当）
                zero_other: dict[str, list] = {}   # その他の内訳（0円キー）に集まった名前
                for m in amonths:
                    its = _nest_zero_subs(_items_for(m), rules)
                    for p in its:
                        cat = classify_category(p.get("name", ""), rules, p.get("group"))
                        if cat != other_name:
                            continue
                        if p.get("name") == ZERO_OTHER_NAME:
                            for s in (p.get("subs") or []):
                                a = zero_other.setdefault(s.get("name"), [0, 0])
                                a[0] += s.get("qty") or 0
                                a[1] += round(s.get("sales") or 0)
                        else:
                            a = sell_other.setdefault(p.get("name"), [0, 0, p.get("group")])
                            a[0] += round(p.get("sales") or 0)
                            a[1] += p.get("qty") or 0
                print(f"\n== 監査 {af}〜{at}：その他に落ちた“売上つき”商品（＝要割り振り）"
                      f"{len(sell_other)}種 ==")
                if not sell_other:
                    print("  （なし＝売上のある商品は全て区分に割り振り済み）")
                for name, (sv, q, grp) in sorted(sell_other.items(), key=lambda x: -x[1][0]):
                    print(f"  {name}: 累計{sv:,}円 / {q:,}点 [{grp or '見出し無し'}]")
                print(f"\n== 監査 {af}〜{at}：その他の内訳（0円キー）に集約された名前"
                      f"{len(zero_other)}種（相談用）==")
                for name, (q, sv) in sorted(zero_other.items(), key=lambda x: -x[1][0]):
                    extra = f" / 売上{sv:,}円" if sv else ""
                    print(f"  {name}: 累計{q:,}点{extra}")

        # MONTHS 環境変数で指定した月の商品上位を出す（例: 前年の秋の商品を洗い出す）。
        want = [m.strip() for m in os.environ.get("MONTHS", "").split(",") if m.strip()]
        for m in want:
            rows = sorted(pby_m.get(m, []), key=lambda x: -x[1])[:40]
            print(f"\n== {m} の商品上位{len(rows)} ==")
            if not rows:
                print("  （この月の商品データなし）")
            for name, val, cat in rows:
                print(f"  [{cat}] {name}: {val:,}円")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
