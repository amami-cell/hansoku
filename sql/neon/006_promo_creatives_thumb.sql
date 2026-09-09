-- POP（PDF）の1ページ目サムネ画像のR2キー。
-- iframeのPDFはスマホで真っ白になるため、1ページ目を画像化して小窓に出す。
-- 画像POPやサムネ未生成のものは空（従来表示にフォールバック）。
ALTER TABLE promo_creatives ADD COLUMN IF NOT EXISTS thumb_key TEXT NOT NULL DEFAULT '';
