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
    GRAIN_MONTH,
    METRIC_DRINK_SALES,
    METRIC_DRINK_THEORY_COST,
    METRIC_FOOD_SALES,
    METRIC_FOOD_THEORY_COST,
    METRIC_SALES,
    METRIC_SALES_BUDGET,
)
from ..settings import ROOT
from ..stores import StoreMaster

# 施策スケジュールの種類（色分けに使う）。未知の種類は dev（その他開発）に寄せる。
VALID_KINDS = {"gm", "lunch", "osusume", "bounenkai", "dev", "closure"}
DEFAULT_SCHEDULE_PATH = ROOT / "config" / "schedule.yaml"


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
                # 販促の目標数値（売上・円）。アプリ内で入力していく。未設定は None。
                "target": _parse_target(camp.get("target")),
            }
        )
    return out


def _parse_target(value) -> int | None:
    """目標値を円の整数に正規化する。空・数字でないものは None。"""
    if value is None or value == "":
        return None
    try:
        return int(round(float(str(value).replace(",", "").replace("円", "").strip())))
    except (ValueError, TypeError):
        return None

# 画面に出す指標。増やすときはここに足す。
METRICS = [
    METRIC_SALES,
    METRIC_FOOD_SALES,
    METRIC_DRINK_SALES,
    METRIC_FOOD_THEORY_COST,
    METRIC_DRINK_THEORY_COST,
]


def build(
    warehouse: Warehouse,
    master: StoreMaster,
    *,
    date_from: date,
    date_to: date,
    campaigns: list[dict] | None = None,
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
        # 施策スケジュール（config/schedule.yaml 由来）。空でも画面は成立する。
        "campaigns": campaigns or [],
    }


def write(payload: dict, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "dashboard.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return path
