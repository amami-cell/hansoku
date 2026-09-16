"""
店舗マスタ（m_stores）の読み込みと、店名 → store_code の解決。

FW共有シートは店舗を「店名」で持っているが、このシステムの結合キーは
``store_code`` なので、取り込みの入口で必ずコードへ寄せる。
解決できなかった店名は黙って捨てず、呼び出し側に返して落とせるようにする。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .normalize import store_code as normalize_store_code
from .normalize import split_source_store_name, store_key
from .settings import ROOT

DEFAULT_STORES_PATH = ROOT / "config" / "stores.yaml"


@dataclass(frozen=True)
class Store:
    store_code: str
    store_name: str
    source_name: str
    # インフォマートは FW と別のコード体系・別の店名表記を使う。
    # 棚卸タブは店名しか持たないため、名前でも引けるよう両方を保持する。
    infomart_code: str
    infomart_name: str
    # エリア（大阪/東京/京都/兵庫/福岡）。近隣店比較・エリア別集計の単位。
    region: str
    brand: str
    brand_name: str
    file_prefix: str
    is_shared_facility: bool
    active: bool
    # 実績の出どころ。既定は "fw"（FW=Foodist Journal に連動済み）。
    # FW未連動の店は "uleji"（uレジ管理）/"dainy"（ダイニー管理アプリ）を入れる。
    # 取り込みは触らないが、カバレッジ確認で「取れないのか・取り漏れか」を分ける。
    pos: str = "fw"
    # FWのABCで部門（分類）が取れるか。FW側で商品に部門が紐付いていない店は
    # 何度取り込んでも部門0件になる。取込の不具合と区別するために持つ。
    abc_dept: bool = True
    # 開業日 "YYYY-MM-DD"（分かる範囲で）。この月より前は営業していない＝実績が
    # 無いのが正しい。カバレッジで「開店前の月」を欠けではなく対象外(N/A)として
    # 扱い、各店を"営業期間で完備か"で見るために持つ。
    opened: str = ""
    # 業態変更（リニューアル）した月 "YYYY-MM"。この月より前は別の店の数字なので、
    # またぐ前年比は「同じ店の比較」ではない。数字は消さずに注意書きを出すために持つ。
    renewal_month: str = ""
    # リニューアル前の店名。推移を見るときに、どこまでが旧店かを示す。
    former_name: str = ""
    # 現場での呼び名。マスタ名と違うことが多い（1006 は「大衆寿司酒場すさび湯」
    # だが、店長も本部も「すさび湯 梅田」と呼ぶ）。画面のさがす欄で引けるようにする。
    aliases: tuple[str, ...] = ()
    # 読みがな。アプリの検索でだけ使う。スマホでかな入力のまま探す人が
    # 「うめだ」で 1006 に辿り着けるようにするためのもので、
    # 取り込み時の店名照合には使わない（ゆるすぎて別の店に当たりうる）。
    yomi: tuple[str, ...] = ()


class UnknownStoreError(LookupError):
    """店舗マスタに無い店名・コードが取り込み元に現れた。"""


class StoreMaster:
    """店舗マスタ。店名・コードのどちらからでも引ける。"""

    def __init__(self, stores: list[Store]):
        self._stores = stores
        self._by_code: dict[str, Store] = {}
        self._by_key: dict[str, Store] = {}
        for store in stores:
            if store.store_code in self._by_code:
                raise ValueError(f"store_code が重複しています: {store.store_code}")
            self._by_code[store.store_code] = store
            # 表示名・元名・通称の全部から引けるようにする。取り込み元によって表記が
            # 違ううえ、人（台帳・検索）が使うのは通称のほうなので、両方要る。
            for name in (
                store.source_name,
                store.store_name,
                store.infomart_name,
                *store.aliases,
            ):
                if not name:
                    continue
                key = store_key(name)
                existing = self._by_key.get(key)
                if existing is not None and existing.store_code != store.store_code:
                    # 正規化は記号・空白・長音を落とすため、店名の付け方によっては
                    # 別の店が同じキーに潰れうる。黙って片方を捨てると、その店の
                    # 実績がもう一方の店に混ざるので、必ずここで気づけるようにする。
                    raise ValueError(
                        f"店名が正規化後に衝突しています: "
                        f"{existing.store_code}({existing.store_name}) と "
                        f"{store.store_code}({store.store_name}) が共に {key!r}。"
                        f" config/stores.yaml の store_name を区別できる名前にしてください。"
                    )
                self._by_key.setdefault(key, store)

    # ── 読み込み ──────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path | str | None = None) -> "StoreMaster":
        target = Path(path) if path else DEFAULT_STORES_PATH
        with open(target, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        stores = [
            Store(
                store_code=normalize_store_code(row["store_code"]),
                store_name=row["store_name"],
                source_name=row.get("source_name", row["store_name"]),
                infomart_code=str(row.get("infomart_code", "") or ""),
                infomart_name=str(row.get("infomart_name", "") or ""),
                region=str(row.get("region", "") or ""),
                brand=row.get("brand", ""),
                brand_name=row.get("brand_name", ""),
                file_prefix=row.get("file_prefix", ""),
                is_shared_facility=bool(row.get("is_shared_facility", False)),
                active=bool(row.get("active", True)),
                pos=str(row.get("pos", "") or "fw"),
                abc_dept=bool(row.get("abc_dept", True)),
                opened=str(row.get("opened", "") or ""),
                renewal_month=str(row.get("renewal_month", "") or ""),
                former_name=str(row.get("former_name", "") or ""),
                aliases=tuple(str(a) for a in (row.get("aliases") or []) if a),
                yomi=tuple(str(y) for y in (row.get("yomi") or []) if y),
            )
            for row in data.get("stores", [])
        ]
        if not stores:
            raise ValueError(f"店舗マスタが空です: {target}")
        return cls(stores)

    # ── 参照 ──────────────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._stores)

    def __iter__(self):
        return iter(self._stores)

    @property
    def all(self) -> list[Store]:
        return list(self._stores)

    @property
    def active(self) -> list[Store]:
        """稼働中の店舗のみ。集約ループと scope=group の展開先はこちらを使う。"""
        return [s for s in self._stores if s.active]

    @property
    def active_codes(self) -> list[str]:
        return [s.store_code for s in self.active]

    @property
    def regions(self) -> list[str]:
        """出現順を保った、稼働店のエリア一覧。"""
        seen: list[str] = []
        for store in self.active:
            if store.region and store.region not in seen:
                seen.append(store.region)
        return seen

    def in_region(self, region: str) -> list["Store"]:
        return [s for s in self.active if s.region == region]

    def by_code(self, code: str | int) -> Store:
        normalized = normalize_store_code(code)
        try:
            return self._by_code[normalized]
        except KeyError as exc:
            raise UnknownStoreError(f"店舗マスタに無いコードです: {code!r}") from exc

    def by_name(self, name: str) -> Store:
        """取り込み元の店名から店舗を引く（表記揺れを吸収）。"""
        try:
            return self._by_key[store_key(name)]
        except KeyError as exc:
            raise UnknownStoreError(f"店舗マスタに無い店名です: {name!r}") from exc

    def find_by_name(self, name: str) -> Store | None:
        """
        取り込み元の店名から店舗を探す。引けなければ None。

        店名にコードが埋め込まれていれば（FW共有シートの "0001015_..." 形式）
        それを最優先で使う。店名の表記は変わりうるが、コードは変わらないため。
        コードが無ければ従来どおり正規化した店名で突き合わせる。
        """
        code, bare = split_source_store_name(name)
        if code is not None:
            found = self._by_code.get(normalize_store_code(code))
            if found is not None:
                return found
            # コードは付いているがマスタに無い＝新店。店名で拾えるかだけ試す。
            return self._by_key.get(store_key(bare))
        return self._by_key.get(store_key(name))

    def by_infomart_code(self, code: str | int) -> Store | None:
        """インフォマート側のコードから引く（原価・仕入の取り込み用）。"""
        normalized = normalize_store_code(code)
        return next(
            (s for s in self._stores if s.infomart_code
             and normalize_store_code(s.infomart_code) == normalized),
            None,
        )
