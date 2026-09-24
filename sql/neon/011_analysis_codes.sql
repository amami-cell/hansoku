-- FW「分析用コード設定」から取り込む 商品→分析用コード の対応表。
-- 部門別客数（宴会=コード5の出数 等）や一人当たり出品数の分母算出に使う。
-- store_code は m_stores と同じ正規化コード（例 "1006"）。analysis_code が NULL＝FW未入力。
CREATE TABLE IF NOT EXISTS analysis_codes (
    store_code     TEXT        NOT NULL,
    product_code   TEXT        NOT NULL,
    product_name   TEXT        NOT NULL DEFAULT '',
    analysis_code  INTEGER,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store_code, product_code)
);

COMMENT ON TABLE  analysis_codes IS 'FW分析用コード設定（商品→分析用コード1〜28）。部門別客数・一人当たりの算出に使う';
COMMENT ON COLUMN analysis_codes.analysis_code IS '分析用コード(1〜28)。NULL＝FWで未入力（アラート対象）';

CREATE INDEX IF NOT EXISTS idx_analysis_codes_store ON analysis_codes (store_code);
