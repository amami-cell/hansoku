# 販促マネジメント・ダッシュボード（HASSIN / イニシエート）

飲食グループ24店舗の **販促施策マネジメント＋簡易BI＋制作物アーカイブ** を統合するシステム。

既存の自動化資産を**再収集せず、参照・集約する上位レイヤー**として作る。
新規スクレイピングは行わず、既存パイプラインが出力した結果を読むだけ。

数値は全て自動集約し、**人が手入力するのは「施策登録」と「要因メモ」だけ**。
これを崩さないことが、運用が続くかどうかの分かれ目になる。

---

## 現在の状態: Phase 0（土台）完了

| Phase | 内容 | 状態 |
|---|---|---|
| 0 | マスタ＋集約パイプライン | ✅ 完了 |
| 1 | 施策CMS・スケジュール・PDF・対目標表示 | 未着手 |
| 2 | 対比レポート＋達成率エンジン | 未着手 |
| 3 | 振り返りギャラリー・要因分析・AI要約・朝のブリーフ | 未着手 |

---

## 構成

```
実績・施策・目標・マスタ・集計結果  → Neon           サーバーレス PostgreSQL
PDF・制作物                      → Cloudflare R2  ファイル蓄積（下り無料）
表示                             → GitHub Pages   公開JSON・SPA
書き込み・認証                    → GAS Web App
バッチ集計・通知                  → GitHub Actions
```

```
バッチが Neon のマスタと f_actuals を読む
  → 集計して f_daily / f_campaign_summary へ書き戻す
  → 画面は計算済みのそれを読むだけ
```

**画面表示時に生の実績を集計しない**（遅延を避けるため事前計算する）。

### 実績の置き場について

当初は実績（`f_actuals`）を BigQuery に置く設計だったが、GCPプロジェクトが
サンドボックス（お支払い情報未登録）で次の制限にかかるため、当面 Neon に置いている。

- DML が使えない → 冪等な再取り込みができない
- **テーブルが60日で自動削除される → 前年同期比が原理的に作れない**

いま取り込めているのは月次9指標のみで年間2,600行程度と小さく、Neon の無料枠で
何年分でも保持できる。時間帯別売上・ABC分析（年150万行規模）が実際に取れるように
なった時点で BigQuery へ移す。`Warehouse` 抽象があるので切り替えは実装の差し替えで済み、
`WAREHOUSE_BACKEND=bigquery` で選べるところまで作ってある。

---

## データソースの現状（重要）

実績の取り込み元は、既存パイプライン
（[`infomart_automation`](https://github.com/amami-cell/-) の `foodist_journal.py`）が
FW の「損益管理 → 実績管理業務 → 店長会資料DL」から取得して書き込んでいる
Google スプレッドシート。このシステムは**それを読むだけ**で、書き込みは一切しない。

いま取れているのは **月次の9指標**:

| 共有シートのタブ | このシステムの指標 |
|---|---|
| 売上 | `sales` |
| F売上 / D売上 | `food_sales` / `drink_sales` |
| F食材費仕入 / D飲料費仕入 | `food_purchase` / `drink_purchase` |
| フード理論原価 / ドリンク理論原価 | `food_theory_cost` / `drink_theory_cost` |
| F予算 / D予算 | `food_budget_cost` / `drink_budget_cost` |

**まだ取れていないもの**: 時間帯別売上（1時間粒度）・客数・客単価・ABC分析。
既存パイプラインはこれらを取得していない（FW の別画面にある）。

`f_actuals` は最初から `hour` / `product_name` / `product_category` の列を持たせてあるので、
時間帯別・商品別が取れるようになったら**スキーマ変更なしにそのまま流し込める**。
それまでは `grain='month'` の行だけが入る。

---

## 使い方

```bash
pip install -r requirements.txt

# 認証情報なしで動く（DuckDB / ローカルPostgres / ローカルFS）
python -m hansoku.cli stores                    # 店舗マスタを確認
python -m hansoku.cli init-schema               # スキーマ作成＋店舗マスタ同期
python -m hansoku.cli ingest-fw --fixture tests/fixtures/fw_sheet_sample.json --lenient

# 任意の店舗×期間×指標×時間帯で集計する
python -m hansoku.cli aggregate --metric sales     --date-from 2026-08-01 --date-to 2026-08-31
python -m hansoku.cli aggregate --metric cost_rate --date-from 2026-08-01 --date-to 2026-08-31 --store 1015
python -m hansoku.cli aggregate --metric sales --grain hour --hour 18 --hour 19 --hour 20 \
                                --date-from 2026-08-01 --date-to 2026-08-31
```

本番（BigQuery / Neon / R2）へ繋ぐには `HANSOKU_ENV=cloud` と認証情報を設定する。
手順は [docs/setup.md](docs/setup.md)。

---

## テスト

```bash
python -m pytest tests/ -q
```

Neon スキーマの検証は実 PostgreSQL に対して行う（配列・JSONB・CHECK 制約は
実DBでしか確かめられないため）。接続先が無い環境では自動でスキップする。

```bash
export HANSOKU_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:5432/hansoku_test'
python -m pytest tests/ -q
```

---

## 設計上の約束

- **粒度は細→粗の一方向のみ。** `f_actuals` は最小粒度で貯め、表示は束ねて出す。
  粒度の違う行を混ぜて合計しないよう、集計は必ず `grain` を絞る。
- **取りこぼしを成功にしない。** 店舗マスタに無い店名やタブの欠落があれば、
  既定で取り込みを中断する。0件で正常終了すると、画面に出ないことに気づけない。
- **比率は分子・分母を合計してから割る。** 原価率や客単価を行ごとに平均すると狂う。
  分母が無いときは 0 ではなく `None` を返す（0% を「達成」と誤読させないため）。
- **認証情報はコードに置かない。** 全て環境変数（GitHub Secrets）から読む。
- **相関であって因果ではない。** 施策期間が重なると主指標の帰属は切り分けられない。
  この前提は UI にも明示する。
