-- 販促プラン（アプリ内で起票・複製する販促の計画）。
-- schedule.yaml 由来の「確定した販促」とは別に、店長がアプリ上で起票する計画を持つ。
-- 去年の枠を複製して日付・対象区分・目標・メモを入れて起票 → 年間ビュー/PDCA に
-- 「計画」として並ぶ。数値は自動集計・計画は人が書く、という原則の「計画」側。
-- id はアプリ生成（plan-...）。ログインできる全員が編集でき set_by にメールを残す。
CREATE TABLE IF NOT EXISTS promo_plans (
    id           TEXT PRIMARY KEY,
    store_code   TEXT NOT NULL,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'dev',
    bucket       TEXT NOT NULL DEFAULT '',
    start_date   DATE NOT NULL,
    end_date     DATE NOT NULL,
    goal         BIGINT,
    note         TEXT NOT NULL DEFAULT '',
    source_id    TEXT NOT NULL DEFAULT '',
    set_by       TEXT NOT NULL DEFAULT '',
    set_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS promo_plans_store_idx ON promo_plans (store_code);
COMMENT ON TABLE promo_plans IS 'アプリ内で起票する販促プラン（計画）。schedule.yaml の確定販促とは別。';
