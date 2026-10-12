"""uレジ（FoodingJournal）の『商品別ABC』CSV → f_actuals。

FW未連動の店（1766 ぎふや福岡天神）は、FWからも共有シートからも商品別が取れない。
uレジのABC CSVを手で書き出してもらい、ここで取り込む。1店・1ヶ月ぶん。

CSV列（見出しで引く）:
  順位, ランク, 商品名, カテゴリ１, カテゴリ２, 販売単価, 平均単価,
  売上数量, 売上金額, 構成比, 累計構成比, 商品設定原価, 粗利益, 粗利率
  ※金額は税込・¥とカンマ付き。原価/粗利は「1点あたり」。合計＝単価×数量。

取り込む指標（source=uleji_csv）:
  - 商品別: PRODUCT_SALES(税込)・PRODUCT_QTY・PRODUCT_COST(税込)・PRODUCT_GROSS(税込)
    PRODUCT_QTY の product_category に『NN:カテゴリ２』を載せる＝画面の「区分→商品」
    ドリル用（dept_as_category の店で区分名に一致する）。PRODUCT_SALES はランク。
  - 部門別: カテゴリ２で集計し DEPT_SALES/DEPT_QTY。product_name=『NN:カテゴリ２』、
    product_category=原価率（%）。→ 画面の区分(カテゴリ２)になる。
  - トップライン: 任意で METRIC_SALES(税込)・METRIC_COVERS（引数で渡す）。
    1766 の売上/客数は通常 pos_sheet(優先)が入れるので、ここは欠けている時の保険。
"""
from __future__ import annotations

import csv
import io
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from ..model import (
    GRAIN_MONTH,
    KIND_FINAL,
    METRIC_COVERS,
    METRIC_DEPT_QTY,
    METRIC_DEPT_SALES,
    METRIC_PRODUCT_COST,
    METRIC_PRODUCT_GROSS,
    METRIC_PRODUCT_QTY,
    METRIC_PRODUCT_SALES,
    METRIC_SALES,
    ActualRow,
)

SOURCE = "uleji_csv"

# カテゴリ２のうち「部門」にしたくない運用名はまとめて その他 扱い（区分に出さない）。
_OTHER_CATS = {"商品カテゴリ無し2", "商品カテゴリ無し1", "サービス", "FOOD", ""}


def _num(s: str | None) -> int:
    """'¥1,660' / '1,660' / '-' → int（空や記号は0）。"""
    t = (s or "").replace("¥", "").replace(",", "").replace("￥", "").strip()
    neg = t.startswith("-")
    t = t.lstrip("-")
    return (-1 if neg else 1) * int(t) if t.isdigit() else 0


def _month_date(month: str):
    from datetime import date as _date

    y, m = month.split("-")
    return _date(int(y), int(m), 1)


def parse_abc_csv(path: Path) -> list[dict]:
    """ABC CSVを行dictのリストに（金額は数値化・原価/粗利は合計へ）。"""
    raw = Path(path).read_text(encoding="utf-8-sig")
    rows: list[dict] = []
    for r in csv.DictReader(io.StringIO(raw)):
        name = (r.get("商品名") or "").strip()
        if not name:
            continue
        qty = _num(r.get("売上数量"))
        sales = _num(r.get("売上金額"))
        cost_u = _num(r.get("商品設定原価"))
        gross_u = _num(r.get("粗利益"))
        cat2 = (r.get("カテゴリ２") or "").strip()
        if unicodedata.normalize("NFKC", cat2) in _OTHER_CATS or cat2 in _OTHER_CATS:
            cat2 = "その他"
        rows.append({
            "name": name,
            "rank": (r.get("ランク") or "").strip() or None,
            "cat1": (r.get("カテゴリ１") or "").strip(),
            "cat2": cat2,
            "qty": qty,
            "sales": sales,
            "cost": cost_u * qty,
            "gross": gross_u * qty,
        })
    return rows


def build_rows(
    store_code: str,
    month: str,
    items: list[dict],
    *,
    sales_total: int | None = None,
    covers: int | None = None,
    source: str = SOURCE,
) -> list[ActualRow]:
    rep = _month_date(month)
    now = datetime.now(timezone.utc)

    def row(metric, value, product_name=None, product_category=None):
        return ActualRow(
            store_code=store_code, date=rep, grain=GRAIN_MONTH, metric=metric,
            value=float(value), product_name=product_name,
            product_category=product_category, kind=KIND_FINAL,
            source=source, ingested_at=now,
        )

    # カテゴリ２→表示番号（売上の大きい順に 01,02,…）。区分見出し "NN:カテゴリ２" に使う。
    cat_sales: dict[str, float] = {}
    for it in items:
        if it["sales"] > 0:
            cat_sales[it["cat2"]] = cat_sales.get(it["cat2"], 0.0) + it["sales"]
    order = [c for c, _ in sorted(cat_sales.items(), key=lambda kv: -kv[1])]
    code_of = {c: f"{i + 1:02d}:{c}" for i, c in enumerate(order)}

    out: list[ActualRow] = []
    # ── 商品別 ──
    for it in items:
        if it["sales"] <= 0 and it["qty"] <= 0:
            continue
        nm = it["name"][:80]
        if it["sales"] > 0:
            out.append(row(METRIC_PRODUCT_SALES, it["sales"], nm, it["rank"]))
            if it["cost"] > 0:
                out.append(row(METRIC_PRODUCT_COST, it["cost"], nm))
            if it["gross"] != 0:
                out.append(row(METRIC_PRODUCT_GROSS, it["gross"], nm))
        if it["qty"] > 0:
            grp = code_of.get(it["cat2"])  # 売上0のカテゴリは見出し無し
            out.append(row(METRIC_PRODUCT_QTY, it["qty"], nm, grp))

    # ── 部門別（カテゴリ２） ──
    agg: dict[str, list] = {}
    for it in items:
        if it["sales"] <= 0 and it["qty"] <= 0:
            continue
        a = agg.setdefault(it["cat2"], [0.0, 0.0, 0.0])
        a[0] += it["sales"]; a[1] += it["qty"]; a[2] += it["cost"]
    for cat2, (s, q, c) in agg.items():
        if s <= 0:
            continue
        name = code_of.get(cat2, f"99:{cat2}")
        rate = f"{round(c / s * 100, 1)}" if s else ""
        out.append(row(METRIC_DEPT_SALES, s, name, rate))
        if q > 0:
            out.append(row(METRIC_DEPT_QTY, q, name, rate))

    # ── トップライン（任意・保険） ──
    if sales_total:
        out.append(row(METRIC_SALES, sales_total))
    if covers:
        out.append(row(METRIC_COVERS, covers))
    return out


def ingest_uleji_abc(
    warehouse,
    store_code: str,
    month: str,
    csv_path: Path,
    *,
    sales_total: int | None = None,
    covers: int | None = None,
    source: str = SOURCE,
    dry_run: bool = False,
) -> int:
    items = parse_abc_csv(Path(csv_path))
    rows = build_rows(store_code, month, items,
                      sales_total=sales_total, covers=covers, source=source)
    n_prod = sum(1 for it in items if it["sales"] > 0 or it["qty"] > 0)
    print(f"[uレジ] {store_code} {month} CSV {len(items)}品（売れ筋{n_prod}）→ {len(rows)}行"
          f"（source={source}）")
    if dry_run:
        print("[uレジ] dry-run のため書き込みません")
        return 0
    warehouse.ensure_schema()
    loaded = warehouse.replace_actuals(rows, scope_stores=True, scope_metrics=True)
    print(f"[uレジ] warehouse へ {loaded} 行 書き込みました")
    return loaded
