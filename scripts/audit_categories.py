#!/usr/bin/env python3
"""店の品目区分ルールの精度監査。

store_categories.yaml のルールで、その店の商品がどの区分に落ちるかを実データで
確かめ、特に「その他」に落ちている商品（＝ルール未整備）を売上順に出す。
ここへキーワードを足せば構成比の精度が上がる。読み取りのみ。STORE 環境変数で店。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from hansoku.db import get_warehouse
from hansoku.db.warehouse import AggregateQuery
from hansoku.model import GRAIN_DAY, GRAIN_MONTH, METRIC_PRODUCT_SALES
from hansoku.settings import load_settings
from hansoku.web.export import classify_category, load_store_categories

CODE = os.environ.get("STORE", "1160")
FROM = date(2024, 1, 1)
TO = date(2026, 12, 31)
NET_DIVISOR = 1.10  # 税込→税抜（export と同じ）

# 飲み物と分かる語（ドリンク/アルコール以外に落ちていたら誤分類の疑い）。
DRINK_HINTS = ["ドリンク", "ラテ", "コーヒー", "珈琲", "カフェ", "ティー", "紅茶", "ソーダ",
               "ジュース", "スムージー", "フロート", "レモネード", "モカ", "コーラ",
               "エスプレッソ", "カプチーノ", "アイスチョコ"]


def _schedule_titles() -> dict[str, str]:
    """config/schedule.yaml から 施策id → タイトル。監査の見出し用（読み取りのみ）。"""
    path = Path(__file__).resolve().parent.parent / "config" / "schedule.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for c in data.get("campaigns") or []:
        cid = str(c.get("id", ""))
        if cid:
            out[cid] = str(c.get("title", ""))
    return out


def audit_campaign_pages(rows, rules) -> None:
    """販促ページ（campDeptMix）が見せる「この販促だけの部門別内訳」を実データで再現し、
    施策ごとに 商品→品目区分 の割り振りが正しいかを一覧する。誤分類の疑いは★で。
    ソースは export._build_campaign_actuals と同じ fw_abc_camp（登録した販売期間の実績）。"""
    titles = _schedule_titles()
    # 施策id → 商品名 → 税抜売上
    per_camp: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        cid = r.get("product_category")
        name = r.get("product_name")
        if not cid or not name:
            continue
        per_camp[cid][name] += (r["value"] or 0) / NET_DIVISOR

    print(f"\n== 販促ページの部門割り振り監査（fw_abc_camp・{CODE}） {len(per_camp)}施策 ==")
    if not per_camp:
        print("  fw_abc_camp の実績がまだありません（abc-campaign 未取込の販促は監査対象外）。")
        return
    other_name = rules.get("other", "その他")
    flagged = 0
    for cid in sorted(per_camp):
        items = per_camp[cid]
        tot = sum(items.values()) or 1
        by_cat: dict[str, float] = defaultdict(float)
        susp: list[tuple[str, str, float]] = []
        for name, s in items.items():
            cat = classify_category(name, rules)
            by_cat[cat] += s
            if cat not in ("ドリンク", "アルコール") and any(h in name for h in DRINK_HINTS):
                susp.append((name, cat, s))
        title = titles.get(cid, "")
        cats = "／".join(f"{c} {v/tot*100:.0f}%" for c, v in sorted(by_cat.items(), key=lambda x: -x[1]))
        head = f"  ▸ {cid} {title}".rstrip()
        print(f"{head}  [{round(tot):,}円]  {cats}")
        for name, cat, s in sorted(susp, key=lambda x: -x[2]):
            print(f"      ★誤分類の疑い [{cat}] {name}  {round(s):,}円")
            flagged += 1
        if other_name in by_cat:
            for name, s in sorted(items.items(), key=lambda x: -x[1]):
                if classify_category(name, rules) == other_name:
                    print(f"      ・未分類({other_name}) {name}  {round(s):,}円")
    print(f"\n  → 誤分類の疑い 計 {flagged}件（★）。0なら販促ページの部門割り振りは規則上OK。")


def main() -> int:
    rules = load_store_categories().get(CODE)
    if not rules:
        print(f"店 {CODE} の区分ルールが store_categories.yaml にありません。")
        return 0
    settings = load_settings()
    with get_warehouse(settings) as wh:
        rows = wh.aggregate(
            AggregateQuery(
                date_from=FROM, date_to=TO, grain=GRAIN_MONTH,
                metrics=[METRIC_PRODUCT_SALES], store_codes=[CODE],
                group_by=("store_code", "product_name"),
            )
        )
        # 販促ページ（この販促だけの部門別内訳）用に、fw_abc_camp を同じ接続で取っておく。
        camp_rows = wh.aggregate(
            AggregateQuery(
                date_from=FROM, date_to=TO, grain=GRAIN_DAY,
                metrics=[METRIC_PRODUCT_SALES], store_codes=[CODE],
                group_by=("product_category", "product_name"),
                sources=["fw_abc_camp"],
            )
        )
    other_name = rules.get("other", "その他")
    by_cat_sales: dict[str, float] = defaultdict(float)
    other_products: dict[str, float] = defaultdict(float)
    # 飲み物と分かる語を含むのに、ドリンク/アルコール以外に分類されている＝誤分類の疑い。
    drink_hints = ["ドリンク", "ラテ", "コーヒー", "珈琲", "カフェ", "ティー", "紅茶", "ソーダ",
                   "ジュース", "スムージー", "フロート", "レモネード", "モカ", "コーラ",
                   "エスプレッソ", "カプチーノ", "アイスチョコ"]
    suspicious: dict[str, tuple[str, float]] = {}
    total = 0.0
    for r in rows:
        name = r["product_name"]
        val = r["value"]
        total += val
        cat = classify_category(name, rules)
        by_cat_sales[cat] += val
        if cat == other_name:
            other_products[name] += val
        if cat not in ("ドリンク", "アルコール") and any(h in (name or "") for h in drink_hints):
            suspicious[name] = (cat, suspicious.get(name, (cat, 0))[1] + val)

    print(f"== 店 {CODE} 品目区分の精度監査（{FROM}〜{TO} 累計） ==")
    print(f"総売上（商品計）: {round(total):,}円")
    print("\n-- 区分別 売上シェア --")
    for cat, s in sorted(by_cat_sales.items(), key=lambda x: -x[1]):
        print(f"  {cat:8}: {round(s):>12,}円  ({s/ (total or 1) *100:5.1f}%)")

    print(f"\n-- 飲み物語を含むのに非ドリンク区分＝誤分類の疑い {len(suspicious)}件 売上順 --")
    if suspicious:
        for name, (cat, s) in sorted(suspicious.items(), key=lambda x: -x[1][1]):
            print(f"  [{cat:6}] {round(s):>11,}円  {name}")
    else:
        print("  なし（飲み物の誤分類は検出されず）")

    osum = sum(other_products.values())
    print(f"\n-- 「{other_name}」に落ちている商品 {len(other_products)}件（{osum/ (total or 1)*100:.1f}%）売上順 --")
    for name, s in sorted(other_products.items(), key=lambda x: -x[1])[:60]:
        print(f"  {round(s):>11,}円  {name}")

    # 販促ページ（この販促だけの部門別内訳）の割り振りを施策ごとに監査。
    audit_campaign_pages(camp_rows, rules)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
