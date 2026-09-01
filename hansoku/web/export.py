"""
画面が読む JSON を書き出す。

閲覧を速くする要は、画面表示のときにデータベースを叩かないこと。
夜間バッチがここで JSON を作り、Cloudflare Pages が CDN から配る。
何人が何回開いても DB の稼働時間は増えないため、無料枠も守られる。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from ..analytics import RATIO_METRICS, ratio
from ..db.warehouse import AggregateQuery, Warehouse
from ..model import (
    DEPT_BUCKETS,
    GRAIN_HOUR,
    GRAIN_MONTH,
    METRIC_COVERS,
    METRIC_DEPT_QTY,
    METRIC_DEPT_SALES,
    METRIC_DRINK_SALES,
    METRIC_DRINK_THEORY_COST,
    METRIC_FOOD_SALES,
    METRIC_FOOD_THEORY_COST,
    METRIC_PRODUCT_SALES,
    METRIC_SALES,
    METRIC_SALES_BUDGET,
    dept_bucket,
    is_drink_dept,
)

# 店舗詳細に出す売れ筋商品の件数
PRODUCTS_TOP_N = 12
from ..settings import ROOT
from ..stores import StoreMaster

# 施策スケジュールの種類（色分けに使う）。未知の種類は dev（その他開発）に寄せる。
VALID_KINDS = {"gm", "lunch", "osusume", "bounenkai", "dev", "closure"}
DEFAULT_SCHEDULE_PATH = ROOT / "config" / "schedule.yaml"
DEFAULT_CREATIVES_PATH = ROOT / "config" / "creatives.yaml"
DEFAULT_LUNCH_PATH = ROOT / "config" / "lunch_analysis.json"


def load_lunch(path: Path | None = None) -> list[dict]:
    """人が貼る config/lunch_analysis.json（ランチ効果分析）を読む。無ければ空。"""
    p = Path(path) if path else DEFAULT_LUNCH_PATH
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    stores = data.get("stores", []) if isinstance(data, dict) else []
    return stores if isinstance(stores, list) else []


DEFAULT_ENV_EFFECTS_PATH = ROOT / "config" / "env_effects.json"


def load_env_effects(path: Path | None = None) -> list[dict]:
    """人が貼る config/env_effects.json（施策前後の時間帯別分析）を読む。無ければ空。"""
    p = Path(path) if path else DEFAULT_ENV_EFFECTS_PATH
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    effects = data.get("effects", []) if isinstance(data, dict) else []
    return effects if isinstance(effects, list) else []


DEFAULT_PROPOSALS_PATH = ROOT / "config" / "proposals.json"


def load_proposals(path: Path | None = None) -> dict:
    """人/AIが書く config/proposals.json（施策別の次回提案）を読む。無ければ空。

    形: {"施策id": {"next": "次回への一手…", "by": "AI", "at": "YYYY-MM-DD"}}
    """
    p = Path(path) if path else DEFAULT_PROPOSALS_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    props = data.get("proposals", data) if isinstance(data, dict) else {}
    return props if isinstance(props, dict) else {}


def _resolve_stores(master: StoreMaster, stores_field) -> tuple[list[str], bool]:
    """stores 欄（all / コードor店名の配列）を稼働店コードの一覧に直す。

    返り値は (コード一覧, 全店フラグ)。実在しないコード・店名は黙って落とす。
    """
    active = set(master.active_codes)
    if stores_field in ("all", "*", None, ""):
        return sorted(active), True
    codes: list[str] = []
    for raw in stores_field:
        token = str(raw)
        if token in active:
            codes.append(token)
            continue
        hit = master.find_by_name(token)
        if hit and hit.store_code in active:
            codes.append(hit.store_code)
    return list(dict.fromkeys(codes)), False  # 重複を除く（順序は保つ）


def load_schedule(
    master: StoreMaster, path: Path | str | None = None
) -> list[dict]:
    """人が書く config/schedule.yaml を読み、画面が使える形に正規化する。

    対象店コードが1つも実在しない施策は捨てる（コードの打ち間違いを黙って通さない）。
    ファイルが無ければ空リスト（施策ゼロでも画面は成立する）。
    """
    path = Path(path) if path else DEFAULT_SCHEDULE_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    active = set(master.active_codes)
    out: list[dict] = []
    for index, camp in enumerate(data.get("campaigns") or []):
        stores_field = camp.get("stores", "all")
        scope_all = stores_field in ("all", "*", None, "")
        if scope_all:
            codes = sorted(active)
        else:
            # store_code そのもの、または店名（find_by_name で解決）どちらでも書ける
            codes = []
            for raw in stores_field:
                token = str(raw)
                if token in active:
                    codes.append(token)
                    continue
                hit = master.find_by_name(token)
                if hit and hit.store_code in active:
                    codes.append(hit.store_code)
            codes = list(dict.fromkeys(codes))  # 重複を除く（順序は保つ）
        if not codes:
            continue

        kind = camp.get("kind", "dev")
        if kind not in VALID_KINDS:
            kind = "dev"
        start = str(camp["start"])
        end = str(camp.get("end", start))
        out.append(
            {
                "id": str(camp.get("id", f"c{index}")),
                "stores": codes,
                "scope_all": scope_all,
                "title": str(camp.get("title", "")),
                "kind": kind,
                "start": start,
                "end": end,
                "note": str(camp.get("note", "")) if camp.get("note") else "",
                # 施策が効くFW ABC部門（コース/食べ放題/ランチ等）。書くと施策詳細に
                # その部門の実績＋構成比を出す。未設定は None（種類から自動推定）。
                "bucket": str(camp.get("bucket")).strip() if camp.get("bucket") else None,
                # 商品内訳のキーワード（部分一致）。書くと該当商品の実績＋部門内構成比を
                # 施策詳細に「表示」で出す。文字列でも配列でも可。未設定は空配列。
                "items": _parse_items(camp.get("items")),
                # 販促の目標数値（売上・円）。アプリ内で入力していく。未設定は None。
                "target": _parse_target(camp.get("target")),
                # 要因メモ（アプリ内で入力・Neon共有）。書き出し時に焼き込む。未設定は空。
                "memo": "",
            }
        )
    return out


def _parse_items(value) -> list[str]:
    """商品内訳キーワードを文字列リストに正規化する。文字列単体・配列どちらも可。"""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = [value]
    out: list[str] = []
    for v in value:
        s = str(v).strip()
        if s:
            out.append(s)
    return out


def _parse_target(value) -> int | None:
    """目標値を円の整数に正規化する。空・数字でないものは None。"""
    if value is None or value == "":
        return None
    try:
        return int(round(float(str(value).replace(",", "").replace("円", "").strip())))
    except (ValueError, TypeError):
        return None


def load_creatives(
    master: StoreMaster,
    campaigns: list[dict] | None = None,
    path: Path | str | None = None,
) -> list[dict]:
    """人が書く config/creatives.yaml を読み、ギャラリーが使える形に正規化する。

    施策(campaign)に紐づけると対象店・種類をそこから引き継ぐ。file（R2キー）が
    無い項目は「まだPDF未登録」なので落とす。ファイルが無ければ空リスト。
    URL は本番と同一ドメインの /creatives/... にする（Cloudflare Access の内側で配信）。
    """
    path = Path(path) if path else DEFAULT_CREATIVES_PATH
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    by_id = {c["id"]: c for c in (campaigns or [])}
    out: list[dict] = []
    for index, item in enumerate(data.get("creatives") or []):
        key = str(item.get("file") or "").strip()
        title = str(item.get("title") or "").strip()
        if not key or not title:
            continue  # PDF未登録 or 名前なしは出さない

        camp_id = str(item.get("campaign") or "").strip()
        camp = by_id.get(camp_id)

        if "stores" in item and item.get("stores") not in (None, ""):
            codes, scope_all = _resolve_stores(master, item["stores"])
        elif camp is not None:
            codes, scope_all = list(camp["stores"]), bool(camp.get("scope_all"))
        else:
            codes, scope_all = sorted(master.active_codes), True
        if not codes:
            continue

        kind = str(item.get("kind") or (camp["kind"] if camp else "dev"))
        if kind not in VALID_KINDS:
            kind = "dev"

        out.append(
            {
                "id": str(item.get("id", f"cr{index}")),
                "title": title,
                "campaign_id": camp_id if camp is not None else "",
                "campaign_title": camp["title"] if camp is not None else "",
                "stores": codes,
                "scope_all": scope_all,
                "kind": kind,
                "date": str(item.get("date") or ""),
                # 同一ドメイン配信（Worker が R2 から返す）。先頭スラッシュ必須。
                "url": "/" + key.lstrip("/"),
            }
        )
    # 新しい掲出日から先に並べる（日付なしは末尾）
    out.sort(key=lambda c: c["date"] or "0000-00-00", reverse=True)
    return out

# 画面に出す指標。増やすときはここに足す。
METRICS = [
    METRIC_SALES,
    METRIC_FOOD_SALES,
    METRIC_DRINK_SALES,
    METRIC_FOOD_THEORY_COST,
    METRIC_DRINK_THEORY_COST,
]


def _assemble_departments(depts: dict[str, dict]) -> dict:
    """1店・1ヶ月ぶんの生部門（{部門名:{sales,qty,rate}}）を、標準バケット構成
    （コース/ランチ/アラカルト/飲み放題/食べ放題）＋生部門 raw に組み立てる。
    店舗詳細・施策詳細・月次シリーズで共用する。"""
    total = sum(d["sales"] for d in depts.values()) or 1.0
    buckets: dict[str, dict] = {}
    alacarte_split = {"フード": 0.0, "ドリンク": 0.0}
    raw_list = []
    for name, d in depts.items():
        bucket = dept_bucket(name)
        cost = d["sales"] * (d["rate"] / 100.0) if d["rate"] is not None else 0.0
        b = buckets.setdefault(bucket, {"sales": 0.0, "qty": 0.0, "cost": 0.0})
        b["sales"] += d["sales"]
        b["qty"] += d["qty"]
        b["cost"] += cost
        if bucket == "アラカルト":
            key = "ドリンク" if is_drink_dept(name) else "フード"
            alacarte_split[key] += d["sales"]
        raw_list.append(
            {
                "name": name,
                "bucket": bucket,
                "sales": round(d["sales"]),
                "qty": round(d["qty"]),
                "cost_rate": round(d["rate"], 1) if d["rate"] is not None else None,
            }
        )
    bucket_list = []
    for name in DEPT_BUCKETS:
        if name not in buckets:
            continue
        b = buckets[name]
        bucket_list.append(
            {
                "name": name,
                "sales": round(b["sales"]),
                "qty": round(b["qty"]),
                "share": round(b["sales"] / total, 4),
                "cost_rate": round(b["cost"] / b["sales"] * 100, 1) if b["sales"] else None,
            }
        )
    raw_list.sort(key=lambda r: r["sales"], reverse=True)
    return {
        "total_sales": round(total),
        "buckets": bucket_list,
        "alacarte": {k: round(v) for k, v in alacarte_split.items()},
        "raw": raw_list,
    }


def _build_abc_by_month(
    warehouse: Warehouse, master: StoreMaster, date_from: date, date_to: date
) -> tuple[dict, dict]:
    """FW ABC（部門・商品）を月ごとに組み立てる。返り値 (departments_monthly,
    products_monthly)＝各 {店コード: {"YYYY-MM": ...}}。月次で蓄積したABC（毎月取込）を
    施策詳細で月ごとに並べる／前年同月と比べるために使う。当月分しか無くても成立する。"""
    codes = list(master.active_codes)
    # 部門売上（店×月×部門×原価率）
    dept_raw: dict[str, dict[str, dict[str, dict]]] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_DEPT_SALES],
            store_codes=codes,
            group_by=("store_code", "date", "product_name", "product_category"),
        )
    ):
        m = row["date"].strftime("%Y-%m")
        try:
            rate = float(row["product_category"]) if row["product_category"] else None
        except (TypeError, ValueError):
            rate = None
        d = (
            dept_raw.setdefault(row["store_code"], {})
            .setdefault(m, {})
            .setdefault(row["product_name"], {"sales": 0.0, "qty": 0.0, "rate": rate})
        )
        d["sales"] += row["value"]
        if rate is not None:
            d["rate"] = rate
    # 部門数量（店×月×部門）
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_DEPT_QTY],
            store_codes=codes,
            group_by=("store_code", "date", "product_name"),
        )
    ):
        m = row["date"].strftime("%Y-%m")
        store = dept_raw.get(row["store_code"], {}).get(m)
        if store and row["product_name"] in store:
            store[row["product_name"]]["qty"] += row["value"]
    departments_monthly: dict[str, dict[str, dict]] = {}
    for code, months in dept_raw.items():
        for m, depts in months.items():
            departments_monthly.setdefault(code, {})[m] = _assemble_departments(depts)
    # 商品売上（店×月×商品）
    prod_tmp: dict[str, dict[str, list]] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_PRODUCT_SALES],
            store_codes=codes,
            group_by=("store_code", "date", "product_name", "product_category"),
        )
    ):
        m = row["date"].strftime("%Y-%m")
        prod_tmp.setdefault(row["store_code"], {}).setdefault(m, []).append(
            {
                "name": row["product_name"],
                "sales": round(row["value"]),
                "rank": row["product_category"],
            }
        )
    products_monthly: dict[str, dict[str, list]] = {}
    for code, months in prod_tmp.items():
        for m, items in months.items():
            items.sort(key=lambda p: p["sales"], reverse=True)
            products_monthly.setdefault(code, {})[m] = items[:PRODUCTS_TOP_N]
    return departments_monthly, products_monthly


def build(
    warehouse: Warehouse,
    master: StoreMaster,
    *,
    date_from: date,
    date_to: date,
    campaigns: list[dict] | None = None,
    creatives: list[dict] | None = None,
) -> dict:
    """画面が必要とするものを1つの辞書にまとめる。"""
    rows = warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=METRICS,
            store_codes=master.active_codes,
            group_by=("store_code", "date", "metric"),
        )
    )

    # [店舗][年月][指標] = 値 の形に畳む。画面側で組み替えやすい。
    monthly: dict[str, dict[str, dict[str, float]]] = {}
    months: set[str] = set()
    for row in rows:
        month = row["date"].strftime("%Y-%m")
        months.add(month)
        monthly.setdefault(row["store_code"], {}).setdefault(month, {})[row["metric"]] = (
            round(row["value"])
        )

    # 売上予算（FW 月別予算登録）。予算対比の基準。指標選択には出さず別枠で持つ。
    budget: dict[str, dict[str, int]] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_SALES_BUDGET],
            store_codes=master.active_codes,
            group_by=("store_code", "date", "metric"),
        )
    ):
        month = row["date"].strftime("%Y-%m")
        budget.setdefault(row["store_code"], {})[month] = round(row["value"])

    # 客数（集客）。FW月別日別売上推移 由来。前年同月ぶんも入っているので、
    # 施策期間の集客を前年比・前月比で見られる。指標選択には出さず別枠で持つ
    # （客数は「人」で、円の指標と混ぜると書式が壊れるため）。
    covers: dict[str, dict[str, int]] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_COVERS],
            store_codes=master.active_codes,
            group_by=("store_code", "date", "metric"),
        )
    ):
        month = row["date"].strftime("%Y-%m")
        covers.setdefault(row["store_code"], {})[month] = round(row["value"])

    # 時間帯別の売上・客数（FW時間帯別売上）。取り込んだ月ぶんを時間帯で束ねる。
    # 時間帯別販促のピーク把握に使う。取り込み前は空。
    hourly: dict[str, dict[str, dict[str, int]]] = {}
    hourly_month: str | None = None
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_HOUR,
            metrics=[METRIC_SALES, METRIC_COVERS],
            store_codes=master.active_codes,
            group_by=("store_code", "hour", "metric"),
        )
    ):
        h = str(int(row["hour"]))
        hourly.setdefault(row["store_code"], {}).setdefault(h, {})[row["metric"]] = round(
            row["value"]
        )
    # 代表月（何月ぶんの時間帯プロファイルか）をラベル用に1つ拾う
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_HOUR,
            metrics=[METRIC_SALES],
            store_codes=master.active_codes,
            group_by=("date",),
        )
    ):
        hourly_month = row["date"].strftime("%Y-%m")

    # 売れ筋商品（FW ABC分析）。商品別売上の上位を店ごとに持つ。product_category は
    # FWのABCランク（A/B/C）。おすすめ料理・売れ筋の把握に使う。取り込み前は空。
    prod_by_store: dict[str, list[dict]] = {}
    prod_month: str | None = None
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_PRODUCT_SALES],
            store_codes=master.active_codes,
            group_by=("store_code", "product_name", "product_category"),
        )
    ):
        prod_by_store.setdefault(row["store_code"], []).append(
            {
                "name": row["product_name"],
                "sales": round(row["value"]),
                "rank": row["product_category"],
            }
        )
    products: dict[str, list[dict]] = {}
    for code, items in prod_by_store.items():
        items.sort(key=lambda p: p["sales"], reverse=True)
        products[code] = items[:PRODUCTS_TOP_N]

    # 全店（グループ全体）の売れ筋。ABC分析は既定で「全店」集計なので、擬似店舗
    # コード "_group" に入れてある。おすすめ料理候補としてTOPページに出す。
    products_group: list[dict] = []
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_PRODUCT_SALES],
            store_codes=["_group"],
            group_by=("product_name", "product_category"),
        )
    ):
        products_group.append(
            {
                "name": row["product_name"],
                "sales": round(row["value"]),
                "rank": row["product_category"],
            }
        )
    products_group.sort(key=lambda p: p["sales"], reverse=True)
    products_group = products_group[:PRODUCTS_TOP_N]

    # 部門内訳（FW ABC 分類=部門）。店ごとにバラバラな部門名を標準バケット
    # （コース/ランチ/アラカルト/飲み放題/食べ放題）へ寄せて、店舗詳細で構成比を出す。
    # 生の部門も raw に残す。売上=dept_sales・数量=dept_qty・原価率=product_category(%)。
    dept_raw: dict[str, dict[str, dict]] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_DEPT_SALES],
            store_codes=master.active_codes,
            group_by=("store_code", "product_name", "product_category"),
        )
    ):
        try:
            rate = float(row["product_category"]) if row["product_category"] else None
        except (TypeError, ValueError):
            rate = None
        d = dept_raw.setdefault(row["store_code"], {}).setdefault(
            row["product_name"], {"sales": 0.0, "qty": 0.0, "rate": rate}
        )
        d["sales"] += row["value"]
        if rate is not None:
            d["rate"] = rate
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_DEPT_QTY],
            store_codes=master.active_codes,
            group_by=("store_code", "product_name"),
        )
    ):
        store = dept_raw.get(row["store_code"])
        if store and row["product_name"] in store:
            store[row["product_name"]]["qty"] += row["value"]

    departments: dict[str, dict] = {
        code: _assemble_departments(depts) for code, depts in dept_raw.items()
    }

    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=date_from,
            date_to=date_to,
            grain=GRAIN_MONTH,
            metrics=[METRIC_PRODUCT_SALES],
            store_codes=[*master.active_codes, "_group"],
            group_by=("date",),
        )
    ):
        prod_month = row["date"].strftime("%Y-%m")

    # 原価率は行ごとに平均できないため、分子・分母を合計してから割る
    cost_rates: dict[str, dict[str, float]] = {}
    for month in sorted(months):
        start = datetime.strptime(month, "%Y-%m").date()
        end = date(start.year, start.month, 28)  # 月内であればよい
        for value in ratio(
            warehouse,
            "cost_rate",
            date_from=start,
            date_to=end,
            store_codes=master.active_codes,
        ):
            # value が None は分母（売上）欠測、0 は分子（理論原価）が未取得。
            # どちらも「原価率が算出できない」ので出さない。0% を載せると
            # lower_better の原価率で「達成」に見えてしまう。
            if value.value:
                cost_rates.setdefault(value.store_code, {})[month] = round(value.value, 4)

    # FW ABC（部門・商品）の月次シリーズ。毎月ABCを取り込むと月ごとに積み上がり、
    # 施策詳細で ケーキ/ジェラート/パフェ・食べ放題・宴会コース を月ごとに並べられる。
    departments_monthly, products_monthly = _build_abc_by_month(
        warehouse, master, date_from, date_to
    )

    # エリア（大阪/東京/…）と、それぞれに属する稼働店コード
    regions = [
        {"name": r, "stores": [s.store_code for s in master.in_region(r)]}
        for r in master.regions
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "months": sorted(months),
        "metrics": METRICS,
        "regions": regions,
        "stores": [
            {
                "code": s.store_code,
                "name": s.store_name,
                "brand": s.brand,
                "brand_name": s.brand_name,
                "region": s.region,
                # 同エリアの他店（近隣比較の相手）。実績のある店だけ。
                "neighbors": [
                    n.store_code for n in master.in_region(s.region)
                    if n.store_code != s.store_code and n.store_code in monthly
                ],
                "shared_facility": s.is_shared_facility,
            }
            for s in master.active
            if s.store_code in monthly
        ],
        "monthly": monthly,
        "cost_rate": cost_rates,
        # 店舗の月次売上予算（FW月別予算登録）。まだ取り込み前は空。
        "budget": budget,
        # 店舗の月次客数（FW月別日別売上推移）。集客の前年比・前月比に使う。空でも可。
        "covers": covers,
        # 店舗の時間帯別 売上・客数（FW時間帯別売上）。時間帯別販促の検討に使う。
        "hourly": hourly,
        "hourly_month": hourly_month,
        # 店舗の売れ筋商品 上位（FW ABC分析・店舗別）。今は空でも可（全店を使う）。
        "products": products,
        # 全店（グループ全体）の売れ筋商品 上位。おすすめ料理候補としてTOPに出す。
        "products_group": products_group,
        "products_month": prod_month,
        # 部門内訳（FW ABC 分類=部門・店舗別）。コース/ランチ/アラカルト/飲み放題/食べ放題の
        # 構成比＋生の部門。abc-store-ingest で店を1つずつ焼くと入る。空でも画面は成立する。
        "departments": departments,
        # FW ABC の月次シリーズ（店×月）。毎月ABCを取り込むと積み上がる。施策詳細で
        # 商品/部門を「1ヵ月毎」に並べ、前年同月と比べるのに使う。空でも画面は成立する。
        "departments_monthly": departments_monthly,
        "products_monthly": products_monthly,
        # 施策スケジュール（config/schedule.yaml 由来）。空でも画面は成立する。
        "campaigns": campaigns or [],
        # 制作物ギャラリー（config/creatives.yaml 由来）。空でも画面は成立する。
        "creatives": creatives or [],
        # ランチ効果分析（config/lunch_analysis.json 由来・店舗別）。空でも画面は成立する。
        "lunch": load_lunch(),
        # 施策前後の時間帯別分析（config/env_effects.json 由来・施策別）。空でも画面は成立する。
        "env_effects": load_env_effects(),
        # 施策別の次回提案（config/proposals.json 由来・人/AI）。空でも画面は成立する。
        "proposals": load_proposals(),
    }


def write(payload: dict, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "dashboard.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return path
