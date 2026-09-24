"""
実績データ（f_actuals）の型定義。

設計の要:
  * 最小粒度でロングデータに貯め、表示は束ねて出す（細→粗の一方向のみ）。
  * ``grain`` を持つのは、いま取れる実績が月次で、時間帯別（1時間粒度）が
    後から入ってくるため。粒度を混ぜたまま合計すると二重計上になるので、
    集計は必ず grain を絞って行う。
  * ``product_name`` / ``product_category`` は share型施策（ABC分析の分子）用に
    先に空けてある。値が入るのは商品別実績を取り込めるようになってから。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Final

# ── 粒度 ────────────────────────────────────────────────────────────────────
GRAIN_HOUR: Final = "hour"
GRAIN_DAY: Final = "day"
GRAIN_MONTH: Final = "month"
GRAINS: Final = (GRAIN_HOUR, GRAIN_DAY, GRAIN_MONTH)

# ── 指標 ────────────────────────────────────────────────────────────────────
# いま取り込めているもの（FW店長会資料 → 既存共有シート）
METRIC_SALES: Final = "sales"
METRIC_FOOD_SALES: Final = "food_sales"
METRIC_DRINK_SALES: Final = "drink_sales"
METRIC_FOOD_PURCHASE: Final = "food_purchase"
METRIC_DRINK_PURCHASE: Final = "drink_purchase"
METRIC_FOOD_THEORY_COST: Final = "food_theory_cost"
METRIC_DRINK_THEORY_COST: Final = "drink_theory_cost"
METRIC_FOOD_BUDGET_COST: Final = "food_budget_cost"
METRIC_DRINK_BUDGET_COST: Final = "drink_budget_cost"

# FW 損益管理 → 月別予算登録 の売上予算（店舗の目標＝予算対比の基準）
METRIC_SALES_BUDGET: Final = "sales_budget"

# インフォマートの棚卸（月次集計タブ）
METRIC_FOOD_INVENTORY: Final = "food_inventory"
METRIC_DRINK_INVENTORY: Final = "drink_inventory"
METRIC_SUPPLY_INVENTORY: Final = "supply_inventory"

# 取り込み口を先に用意してあるもの（時間帯別売上・ABC分析が入り次第使う）
METRIC_COVERS: Final = "covers"
METRIC_AVG_CHECK: Final = "avg_check"
METRIC_PRODUCT_SALES: Final = "product_sales"
# 商品別の販売点数（ABCの「販売数量」）。売上と並べて「何個売れたか」を出す。
METRIC_PRODUCT_QTY: Final = "product_qty"
# 商品別の原価金額・粗利金額（ABCグリッドの「原価金額」「粗利金額」）。
# 売上・点数と同じ (source, grain, date, store, product) に並べて貯める。
# 原価率は貯めず、必要時に 原価金額/売上 から組み直す（比率は合計できないため）。
METRIC_PRODUCT_COST: Final = "product_cost"
METRIC_PRODUCT_GROSS: Final = "product_gross"
# 部門別（ABC 分類=部門）の売上・数量。ランチ/ドリンク/コース等の構成把握に使う。
METRIC_DEPT_SALES: Final = "dept_sales"
METRIC_DEPT_QTY: Final = "dept_qty"

METRICS: Final = (
    METRIC_SALES,
    METRIC_FOOD_SALES,
    METRIC_DRINK_SALES,
    METRIC_FOOD_PURCHASE,
    METRIC_DRINK_PURCHASE,
    METRIC_FOOD_THEORY_COST,
    METRIC_DRINK_THEORY_COST,
    METRIC_FOOD_BUDGET_COST,
    METRIC_DRINK_BUDGET_COST,
    METRIC_SALES_BUDGET,
    METRIC_FOOD_INVENTORY,
    METRIC_DRINK_INVENTORY,
    METRIC_SUPPLY_INVENTORY,
    METRIC_COVERS,
    METRIC_AVG_CHECK,
    METRIC_PRODUCT_SALES,
    METRIC_PRODUCT_QTY,
    METRIC_PRODUCT_COST,
    METRIC_PRODUCT_GROSS,
    METRIC_DEPT_SALES,
    METRIC_DEPT_QTY,
)

# 合計してよい指標（加法的）。客単価・原価率のような比率は合計できないため、
# 集計時は合計ではなく分子・分母から組み直す。
ADDITIVE_METRICS: Final = frozenset(
    {
        METRIC_SALES,
        METRIC_FOOD_SALES,
        METRIC_DRINK_SALES,
        METRIC_FOOD_PURCHASE,
        METRIC_DRINK_PURCHASE,
        METRIC_FOOD_THEORY_COST,
        METRIC_DRINK_THEORY_COST,
        METRIC_FOOD_BUDGET_COST,
        METRIC_DRINK_BUDGET_COST,
        METRIC_SALES_BUDGET,
        METRIC_FOOD_INVENTORY,
        METRIC_DRINK_INVENTORY,
        METRIC_SUPPLY_INVENTORY,
        METRIC_COVERS,
        METRIC_PRODUCT_SALES,
        METRIC_PRODUCT_QTY,
        METRIC_PRODUCT_COST,
        METRIC_PRODUCT_GROSS,
        METRIC_DEPT_SALES,
        METRIC_DEPT_QTY,
    }
)

# 確定区分。FW共有シートは月半ばの「中間」と月末締めの「確定」を持つ。
KIND_INTERIM: Final = "中間"
KIND_FINAL: Final = "確定"

# FWの「部門」（店ごとに命名がバラバラ）を、販促で見たい標準バケットへ寄せる。
# 天さんの区分: コース(宴会飲み放題込み)／ランチ／アラカルト(フード・ドリンク)／
# 飲み放題／食べ放題。表示順もこの並び。取込ログと画面の集計を同じ規則で揃えるため
# ここに一元化する。
DEPT_BUCKETS: Final = ("コース", "ランチ", "アラカルト", "飲み放題", "食べ放題", "その他")
# アラカルトの中でドリンク扱いにする語（残りはフード）。
_DEPT_DRINK_WORDS: Final = (
    "ドリンク", "飲料", "酒", "ビール", "ワイン", "ウイスキー", "ハイボール",
    "サワー", "カクテル", "焼酎", "日本酒", "ソフト", "スパークリング", "ノンアル",
    "ハッピー",  # ハッピーアワー＝時間帯ドリンク値引き。アラカルト・ドリンク寄り。
)


def dept_bucket(name: str) -> str:
    """FWの部門名（例 "66飲み放題" "10ランチサブ" "99コース"）→ 標準バケット。"""
    n = name
    if "食べ放題" in n or "食放" in n or "食べ放" in n:
        return "食べ放題"
    if "飲み放題" in n or "飲放" in n or "のみほ" in n or "呑み放題" in n:
        return "飲み放題"
    if "ランチ" in n or "昼" in n:
        return "ランチ"
    if "コース" in n or "宴会" in n:
        return "コース"
    return "アラカルト"


def is_drink_dept(name: str) -> bool:
    """アラカルト部門のうちドリンク寄りか（フード/ドリンクの内訳表示用）。"""
    return any(w in name for w in _DEPT_DRINK_WORDS)


# お通し／席チャージ（テーブルチャージ）を表す語。これらは1客に1つ付くため、
# その点数＝アラカルト（一品注文）の人数の近似として使う（一人当たり出品数の分母）。
_COVER_CHARGE_WORDS: Final = (
    "お通し", "おとおし", "御通し", "通し料", "つきだし", "突き出", "突出",
    "付き出", "付出", "席料", "席チャージ", "テーブルチャージ", "カバーチャージ", "チャージ",
)


def is_cover_charge(name: str) -> bool:
    """お通し／席チャージ系か（＝1客1点。アラカルト人数の近似に使う）。"""
    return any(w in name for w in _COVER_CHARGE_WORDS)


@dataclass(frozen=True)
class ActualRow:
    """``f_actuals`` の1行。"""

    store_code: str
    date: date
    grain: str
    metric: str
    value: float
    hour: int | None = None
    product_name: str | None = None
    product_category: str | None = None
    kind: str | None = None
    source: str = "unknown"
    ingested_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.grain not in GRAINS:
            raise ValueError(f"未知の grain です: {self.grain!r}")
        if self.metric not in METRICS:
            raise ValueError(f"未知の metric です: {self.metric!r}")
        if self.grain == GRAIN_HOUR and self.hour is None:
            raise ValueError("grain='hour' の行には hour が必要です")
        if self.grain != GRAIN_HOUR and self.hour is not None:
            raise ValueError(f"grain={self.grain!r} の行に hour は持たせられません")
        if self.hour is not None and not 0 <= self.hour <= 23:
            raise ValueError(f"hour が範囲外です: {self.hour}")

    def with_ingested_at(self, moment: datetime | None = None) -> "ActualRow":
        return ActualRow(
            store_code=self.store_code,
            date=self.date,
            grain=self.grain,
            metric=self.metric,
            value=self.value,
            hour=self.hour,
            product_name=self.product_name,
            product_category=self.product_category,
            kind=self.kind,
            source=self.source,
            ingested_at=moment or datetime.now(timezone.utc),
        )
