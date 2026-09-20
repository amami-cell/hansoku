-- 販促の目標を「販促 × 指標(metric)」で複数持てるようにする。
-- 従来は campaign_id 単独の主キー＝1販促1目標（売上のみ）。目標項目（売上・客数・
-- 客単価・原価率・時間帯別…）を並べて設定できるよう、主キーを (campaign_id, metric) に拡張。
-- 目標値は % や 客単価 など整数以外も入りうるので NUMERIC に広げる。全て冪等DDL。
ALTER TABLE promo_targets
    ALTER COLUMN target_value TYPE NUMERIC USING target_value::numeric;
ALTER TABLE promo_targets DROP CONSTRAINT IF EXISTS promo_targets_pkey;
ALTER TABLE promo_targets ADD CONSTRAINT promo_targets_pkey PRIMARY KEY (campaign_id, metric);
COMMENT ON COLUMN promo_targets.metric IS
    '目標の指標。sales=売上円 / covers=客数 / avg_check=客単価円 / cost_rate=原価率% /'
    ' food_cost_rate / drink_cost_rate / hour_sales / hour_covers / hour_avg_check（1日平均）';
