# 販促の取り込みプレイブック（どのパターンでも即・台帳化）

原則は変わらない：**人は台帳を書くだけ／数値は自動で集計**。
どんな販促（PDF・施策の連絡）が来ても、この手順で「schedule → creatives → 効果データ」に落とすと、
店舗詳細・施策の効果・**年間販促（振り返り）**に自動で並ぶ。

## 0. 届いたらまず特定する4点
1. **店舗**（コード。`config/stores.yaml` で名前→コード）
2. **種類（kind）**（下の表）
3. **期間**（開始・終了。単日なら開始のみ）
4. **PDFの有無**（POP/チラシ/メニュー/グランドメニュー）

## 1. 施策を台帳に足す（必須・全パターン共通）
`config/schedule.yaml` の `campaigns:` に1行：

```yaml
- { id: r<店コード>-<slug>, stores: ["<店コード>"], title: <表示名>, kind: <種類>,
    start: "YYYY-MM-DD", end: "YYYY-MM-DD", note: "<一言メモ>" }
```

- `id` は一意（例 `r1151-lunch`）。`stores` は複数可、全店は `scope_all: true`。
- 単日イベント（GM改定・ランチ変更・打ち出し日）は `end` 省略可。
- `target:`（目標売上・円）を書けば達成率が出る（アプリ内でも入力可）。

## 2. PDFがあれば制作物に足す（POP/チラシ/メニュー）
1. PDFを `assets/creatives-src/<ファイル名>.pdf` に置く。
2. `config/creatives.yaml` に1行：

```yaml
- id: <一意id>
  title: <表示名>
  campaign: <上の施策id>      # これで対象店・種類を継承
  date: "YYYY-MM-DD"
  file: creatives/<年>/<施策id>/<ファイル名>.pdf   # R2キー。basename が assets と一致すればデプロイ時に自動アップ
```

## 3. 効果データを拾う（種類別・下の表の「効果ソース」）
数値は基本は月次で自動集計（前年比・前月比・集客）。
それだけで足りないパターンは、FWツールで引いて対応する JSON台帳に貼る。

| 種類(kind) | 例 | schedule | PDF | 効果ソース（貼り先） | 使うFWツール |
|---|---|---|---|---|---|
| `gm` GM改定 | メニュー改定 | 開始日 | グランドメニュー | 月次自動（前年比/前月比・集客） | ―（自動） |
| `osusume` おすすめ/フェア | 秋おすすめ | 開始〜終了 | チラシ/POP | 月次自動＋(必要なら商品) `products` | `abc-store-probe` |
| `lunch` ランチ変更 | 日替わり/沼パスタ | 開始日 | ランチメニュー | `config/lunch_analysis.json`（店別・食数/原価/構成比/トッピング） | `lunch-analyze`／`abc-store-probe` |
| `bounenkai` 忘年会 | コース | 開始〜終了 | コースメニュー | 月次自動（客数・売上） | ―（自動） |
| `dev` その他開発/環境・オペ | 電子タバコ可・空調改善・新機材 | 開始日 | （任意） | `config/env_effects.json`（時間帯 前後・深夜の品数×単価） | `hourly-store-probe`／`abc-totals-probe`／`menu-hourly-probe` |
| `closure` 休業 | 改装休業 | 開始〜終了 | ― | ―（在不在の把握のみ） | ― |

### 効果データ台帳の形（貼り先）
- **月次（自動）**: 何もしない。確定月が出れば施策詳細に 実績＋前年比＋前月比＋集客 が乗る。
- **`config/lunch_analysis.json`**: `stores[]`（store_code/menu_title/period/lunch{sales,cost_rate}/daily_meals/toppings/menu）。→ 店舗詳細「新ランチ効果」＋一覧。
- **`config/env_effects.json`**: `effects[]`（campaign_ids/base_label/after_label/periods[]{covers,sales,items,food_*,drink_*,nomihoudai,night_*}）。→ 施策詳細「効果（時間帯別・前後）」＋深夜分解。
- **`products`（ABC）**: 全店売れ筋は自動取込。個店の商品は `abc-store-probe` で確認。

## 4. 反映（毎回同じ）
1. `node --check web/app.js`（画面を触ったら）
2. `bash scripts/check.sh`（Python/取込を触ったら）
3. commit → push（main）
4. デプロイ：`deploy.yml` を実行（creatives-sync でPDFをR2へ→export→Cloudflare公開）

## 5. 年間で振り返る
- TOP「年間販促 →」で年（2024〜今年）を選ぶと、四半期別に施策が並ぶ（実績＋直近差異）。
- 各行「詳細を確認 →」で施策詳細（前後・深夜分解・POP・要因メモ）へ。
- 過去分（2024〜）を振り返りたいときは、その施策を上の手順で `schedule.yaml` に足す（効果データは分かる範囲で）。

## FWツール早見（GitHub Actions: fw.yml を workflow_dispatch）
- `abc-store-probe` … 1店・任意期間の商品ABC（全商品/部門/グループ/メニュー、ランチ部門も）
- `abc-totals-probe` … 1店・複数期間のグループ合計（フード/ドリンク/飲み放題→品数・有料単価）
- `hourly-store-probe` … 1店・複数期間の時間帯別（客数・売上・客単価）
- `menu-hourly-probe` … 1店・複数期間の時間帯別メニュー出数（時間帯×商品の出数＝深夜の品数）
- `lunch-analyze` … 新ランチの開始日検出＋期間比較
