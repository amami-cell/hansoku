-- 販促の目標（アプリ内で入力する目標売上）。
-- 施策は現状 config/schedule.yaml 由来なので、m_campaigns には依存させず、
-- schedule.yaml の施策id（例 "s1006-natsu"）をそのままキーにする軽量テーブル。
-- 「ログインできる全員」が編集できる運用のため、set_by に Access の
-- メールアドレスを残して、誰が最後に更新したか分かるようにする。
CREATE TABLE IF NOT EXISTS promo_targets (
    campaign_id   TEXT PRIMARY KEY,
    metric        TEXT NOT NULL DEFAULT 'sales',
    target_value  BIGINT NOT NULL CHECK (target_value >= 0),
    set_by        TEXT NOT NULL DEFAULT '',
    set_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE promo_targets IS '販促ごとの目標売上（アプリ内入力）。キーは schedule.yaml の施策id';
