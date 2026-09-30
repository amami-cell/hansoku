-- 分析用コードCSVの「部門名称」を保存する。ABCの部門グリッドが出ない店
-- （商品と部門がABC上で未紐付け：1069/1111/1137/1151/1168 等）の品目区分を、
-- 完備している商品別売上を この 商品→部門 で束ね直して作るために使う。
-- 値は "NN:名前"（例 "13:冷菜"）。部門が無い商品は空文字。
ALTER TABLE analysis_codes ADD COLUMN IF NOT EXISTS dept_name TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN analysis_codes.dept_name IS 'FW分析用コードCSVの部門（"NN:名前"）。ABC部門が取れない店の品目区分の束ね元';
