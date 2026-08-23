-- ============================================================
-- PostgreSQL (Neon): 実績ロングデータ f_actuals
--
-- 当初は BigQuery に置く設計だったが、GCPプロジェクトが
-- サンドボックス（お支払い情報未登録）で、
--   * DML が使えない → 冪等な再取り込みができない
--   * テーブルが60日で自動削除される → 前年同期比が作れない
-- という制限にかかるため、当面は Neon に置く。
--
-- いま取り込めているのは月次9指標のみで、年間わずか2,600行程度。
-- Neon の無料枠に対して十分小さく、何年分でも保持できる。
-- 時間帯別売上・ABC分析（年150万行規模）が実際に取れるようになった時点で
-- BigQuery へ移す。Warehouse 抽象があるので切り替えは実装の差し替えで済む。
-- ============================================================

CREATE TABLE IF NOT EXISTS f_actuals (
    store_code       TEXT             NOT NULL,
    date             DATE             NOT NULL,
    grain            TEXT             NOT NULL CHECK (grain IN ('hour', 'day', 'month')),
    hour             INTEGER          CHECK (hour IS NULL OR hour BETWEEN 0 AND 23),
    metric           TEXT             NOT NULL,
    value            DOUBLE PRECISION NOT NULL,
    product_name     TEXT,
    product_category TEXT,
    kind             TEXT,
    source           TEXT             NOT NULL,
    ingested_at      TIMESTAMPTZ      NOT NULL DEFAULT now()
);

COMMENT ON TABLE f_actuals IS
    '実績ロングデータ。店舗×日付×時×指標の最小粒度で保持する';
COMMENT ON COLUMN f_actuals.grain IS
    '粒度。混ぜて合計すると二重計上になるため、集計時は必ず絞る。grain=month の date はその月の1日';
COMMENT ON COLUMN f_actuals.hour IS '0-23。grain=hour のときのみ値が入る';
COMMENT ON COLUMN f_actuals.product_category IS
    'ABC分析のカテゴリ。share型施策の分子に使う（カテゴリ優先、商品名フォールバック）';
COMMENT ON COLUMN f_actuals.kind IS '取り込み元の確定区分。確定 が 中間 を上書きする';
COMMENT ON COLUMN f_actuals.source IS '集約元。トレース用（例: fw_sheet）';

-- 取り込みは (source, grain, date) 単位で入れ替えるので、その組み合わせで引けるようにする
CREATE INDEX IF NOT EXISTS idx_actuals_scope
    ON f_actuals (source, grain, date);

-- 集計は「店舗×期間×指標」で絞るのが基本形
CREATE INDEX IF NOT EXISTS idx_actuals_lookup
    ON f_actuals (grain, date, store_code, metric);

-- share型施策の分子（対象カテゴリ・対象商品の売上）を引くため。
-- 商品別実績が入るまでは全行 NULL なので、部分インデックスにして無駄を省く。
CREATE INDEX IF NOT EXISTS idx_actuals_product
    ON f_actuals (grain, date, store_code, product_category)
    WHERE product_category IS NOT NULL;
