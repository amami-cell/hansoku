-- 販促の手動ステータス（保留/中止/今季なし/完了）。
-- 実施中/予定/終了は期間から自動で出るが、それに乗らない業務判断（見送り・中止・
-- 区切って完了）を店長が画面から切り替えて全員に共有する。目標・メモと同じく
-- 「回」単位（campaign_id = 施策id@開始年）で持つ。空にすると自動判定に戻る。
CREATE TABLE IF NOT EXISTS promo_status (
    campaign_id  TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    set_by       TEXT NOT NULL DEFAULT '',
    set_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE promo_status IS '販促の手動ステータス（自動の実施中/予定/終了への上書き表示）。';
