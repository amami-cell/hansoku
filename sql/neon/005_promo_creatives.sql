-- アプリ内アップロードした制作物（POP・資料）。
-- 台帳(config/creatives.yaml)は「事前に用意した公式PDF」用。
-- こちらは店長が画面からその場で足すぶん。実体は R2、ここはメタだけ持つ。
-- 施策(campaign_id)に紐づけると施策の期間の下に、店(store_code)だけなら店の制作物に出る。
CREATE TABLE IF NOT EXISTS promo_creatives (
    id           TEXT PRIMARY KEY,          -- R2キーを兼ねた一意ID
    campaign_id  TEXT NOT NULL DEFAULT '',  -- schedule.yaml の施策id（空なら店ひも付けのみ）
    store_code   TEXT NOT NULL DEFAULT '',  -- 店コード（campaign から引ける時は空でも可）
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'dev',
    r2_key       TEXT NOT NULL,             -- creatives/uploads/... （/creatives/ で配信）
    mime         TEXT NOT NULL DEFAULT 'application/octet-stream',
    doc_date     TEXT NOT NULL DEFAULT '',  -- 掲出日 YYYY-MM-DD（任意・表示順）
    set_by       TEXT NOT NULL DEFAULT '',
    set_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_promo_creatives_campaign ON promo_creatives (campaign_id);
CREATE INDEX IF NOT EXISTS ix_promo_creatives_store    ON promo_creatives (store_code);
COMMENT ON TABLE promo_creatives IS 'アプリ内アップロードの制作物（POP・画像・資料）。実体はR2、ここはメタ。';
