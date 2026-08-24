-- 販促の要因メモ（アプリ内で入力する短い所見）。
-- 「なぜ動いた/動かなかったか」を人が残す。数値は自動集計・メモだけ人が書く、
-- という当初の原則の"メモ"側。promo_targets と同じく schedule.yaml の施策idをキーに、
-- ログインできる全員が編集でき、set_by に Access のメールを残す。
CREATE TABLE IF NOT EXISTS promo_notes (
    campaign_id  TEXT PRIMARY KEY,
    note         TEXT NOT NULL DEFAULT '',
    set_by       TEXT NOT NULL DEFAULT '',
    set_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE promo_notes IS '販促ごとの要因メモ（アプリ内入力）。キーは schedule.yaml の施策id';
