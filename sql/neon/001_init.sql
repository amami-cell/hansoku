-- ============================================================
-- Neon (PostgreSQL): マスタ・施策・目標・制作物メタ・要因メモ・集計結果
--
--   頻繁な UPDATE と画面からの即時読み書きが必要なものはここ。
--   大量の実績（f_actuals）は BigQuery 側にあり、store_code / campaign_id / date を
--   共通キーに論理結合する。夜間バッチが BigQuery を集計し、その結果を
--   f_daily / f_campaign_summary に書き戻す。画面表示時に BigQuery は叩かない。
--
--   enum は PostgreSQL のネイティブ ENUM ではなく TEXT + CHECK にしている。
--   新しい指標・比較基準を足すときに CHECK を1行広げるだけで済み、
--   トランザクション内で流せるマイグレーションになるため。
-- ============================================================

-- ── 店舗マスタ ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS m_stores (
    store_code          TEXT PRIMARY KEY,
    store_name          TEXT        NOT NULL,
    source_name         TEXT        NOT NULL,
    infomart_code       TEXT        NOT NULL DEFAULT '',
    brand               TEXT        NOT NULL,
    brand_name          TEXT        NOT NULL DEFAULT '',
    file_prefix         TEXT        NOT NULL DEFAULT '',
    is_shared_facility  BOOLEAN     NOT NULL DEFAULT FALSE,
    active              BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 既に作られているテーブルにも列を足せるようにしておく。
-- CREATE TABLE IF NOT EXISTS は既存テーブルには何もしないため、
-- 後から増えた列はこの形で明示的に足す（何度流しても安全）。
ALTER TABLE m_stores ADD COLUMN IF NOT EXISTS infomart_code TEXT NOT NULL DEFAULT '';

COMMENT ON TABLE  m_stores IS '店舗マスタ。store_code は FW（Foodist Journal）の店舗コード';
COMMENT ON COLUMN m_stores.source_name IS
    'FW共有シートB列の生の値。"0001015_すさび湯 歌舞伎町" のようにコードを含む';
COMMENT ON COLUMN m_stores.infomart_code IS
    'インフォマート側の店舗コード。FW とは別体系で24店中10店が食い違うため、'
    '原価・仕入の取り込みではこちらで突き合わせる';
COMMENT ON COLUMN m_stores.brand IS 'ブランドコード。スケジュール画面の色分けの単位';

-- ── 権限マスタ（多対多）──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS m_access (
    email       TEXT        NOT NULL,
    store_code  TEXT        NOT NULL REFERENCES m_stores (store_code) ON DELETE CASCADE,
    role        TEXT        NOT NULL CHECK (role IN ('admin', 'manager')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (email, store_code)
);
COMMENT ON TABLE m_access IS
    '1人が複数行を持つことで複数店担当を表す。manager は自分の担当店のみ、admin は全店';
CREATE INDEX IF NOT EXISTS idx_access_email ON m_access (email);

-- ── 時間帯グループマスタ（店舗別に可変）──────────────────────────────────
CREATE TABLE IF NOT EXISTS m_timeslots (
    store_code  TEXT    NOT NULL REFERENCES m_stores (store_code) ON DELETE CASCADE,
    slot_name   TEXT    NOT NULL,
    hour_from   INTEGER NOT NULL CHECK (hour_from BETWEEN 0 AND 23),
    hour_to     INTEGER NOT NULL CHECK (hour_to   BETWEEN 1 AND 24),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (store_code, slot_name),
    CHECK (hour_from < hour_to)
);
COMMENT ON TABLE m_timeslots IS
    '生データは1時間粒度で持ち、このマスタで束ねて集計する。hour_to は排他（終了時を含まない）';

-- ── 施策マスタ ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS m_campaigns (
    campaign_id       TEXT PRIMARY KEY,
    name              TEXT        NOT NULL,
    scope             TEXT        NOT NULL CHECK (scope IN ('group', 'store')),
    target_stores     TEXT[]      NOT NULL DEFAULT '{}',
    date_from         DATE        NOT NULL,
    date_to           DATE        NOT NULL,
    primary_metric    TEXT        NOT NULL,
    campaign_type     TEXT        NOT NULL
        CHECK (campaign_type IN ('share', 'cumulative', 'avg_check', 'traffic', 'cost')),
    comparisons       TEXT[]      NOT NULL DEFAULT '{}',
    prev_campaign_id  TEXT        REFERENCES m_campaigns (campaign_id) ON DELETE SET NULL,
    template_id       TEXT,
    has_creative      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_by        TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (date_from <= date_to),
    -- 比較基準は §7-2 の4種のみ。新しい基準を足すときはこの配列を1つ広げる。
    CHECK (comparisons <@ ARRAY['vs_target', 'vs_recent', 'vs_yoy', 'vs_prev_campaign']::TEXT[])
);
COMMENT ON COLUMN m_campaigns.campaign_type IS '主指標の測り方（§7-1）。比較方式は含まない';
COMMENT ON COLUMN m_campaigns.comparisons   IS 'この施策で表示する比較基準（§7-2、複数選択）';
COMMENT ON COLUMN m_campaigns.target_stores IS 'scope=group なら全 active 店に展開する';
CREATE INDEX IF NOT EXISTS idx_campaigns_period ON m_campaigns (date_from, date_to);
CREATE INDEX IF NOT EXISTS idx_campaigns_stores ON m_campaigns USING GIN (target_stores);

-- ── 目標マスタ（施策1対多）──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS m_goals (
    goal_id           TEXT PRIMARY KEY,
    campaign_id       TEXT        NOT NULL REFERENCES m_campaigns (campaign_id) ON DELETE CASCADE,
    metric            TEXT        NOT NULL,
    segment           JSONB       NOT NULL DEFAULT '{}'::JSONB,
    target_type       TEXT        NOT NULL
        CHECK (target_type IN ('relative_recent', 'relative_yoy', 'absolute')),
    target_value      DOUBLE PRECISION NOT NULL,
    target_direction  TEXT        NOT NULL
        CHECK (target_direction IN ('higher_better', 'lower_better')),
    baseline_weeks    INTEGER     NOT NULL DEFAULT 4 CHECK (baseline_weeks > 0),
    scope             TEXT        NOT NULL CHECK (scope IN ('group', 'store')),
    set_by            TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON COLUMN m_goals.segment IS
    '対象の絞り込み {store?, timeslot?, weekday_type?}。平日/休日・時間帯など';
COMMENT ON COLUMN m_goals.target_direction IS
    'lower_better は原価型。達成率バーの向きと色を反転させる';
CREATE INDEX IF NOT EXISTS idx_goals_campaign ON m_goals (campaign_id);

-- ── share型施策の対象定義（店舗別）──────────────────────────────────────
CREATE TABLE IF NOT EXISTS m_share_targets (
    id            TEXT PRIMARY KEY,
    campaign_id   TEXT   NOT NULL REFERENCES m_campaigns (campaign_id) ON DELETE CASCADE,
    store_code    TEXT   NOT NULL REFERENCES m_stores (store_code) ON DELETE CASCADE,
    match_type    TEXT   NOT NULL CHECK (match_type IN ('category', 'product')),
    match_values  TEXT[] NOT NULL DEFAULT '{}',
    UNIQUE (campaign_id, store_code)
);
COMMENT ON TABLE m_share_targets IS
    '店舗ごとにメニューが違うため、share型の対象は店舗別に定義する。'
    'category を優先し、店によってカテゴリで拾えない場合のみ product でフォールバックする';

-- ── 制作物（メタのみ。実PDFは Cloudflare R2）────────────────────────────
CREATE TABLE IF NOT EXISTS m_creatives (
    creative_id   TEXT PRIMARY KEY,
    campaign_id   TEXT        NOT NULL REFERENCES m_campaigns (campaign_id) ON DELETE CASCADE,
    r2_key        TEXT        NOT NULL UNIQUE,
    file_name     TEXT        NOT NULL DEFAULT '',
    content_type  TEXT        NOT NULL DEFAULT 'application/pdf',
    byte_size     BIGINT      NOT NULL DEFAULT 0,
    thumb_base64  TEXT,
    uploaded_by   TEXT        NOT NULL,
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON COLUMN m_creatives.r2_key IS 'R2上のキー。例: creatives/{年}/{campaign_id}/{filename}.pdf';
COMMENT ON COLUMN m_creatives.thumb_base64 IS '1ページ目サムネイルのキャッシュ。一覧の高速表示用';
CREATE INDEX IF NOT EXISTS idx_creatives_campaign ON m_creatives (campaign_id);

-- ── 要因メモ ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS m_reviews (
    review_id    TEXT PRIMARY KEY,
    campaign_id  TEXT        NOT NULL REFERENCES m_campaigns (campaign_id) ON DELETE CASCADE,
    factor_tags  TEXT[]      NOT NULL DEFAULT '{}',
    memo         TEXT        NOT NULL DEFAULT '',
    written_by   TEXT        NOT NULL,
    written_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON COLUMN m_reviews.factor_tags IS '天候/競合/オペ/認知/価格/仕入交渉 など。後の横断分析に使う';
CREATE INDEX IF NOT EXISTS idx_reviews_campaign ON m_reviews (campaign_id);
CREATE INDEX IF NOT EXISTS idx_reviews_tags     ON m_reviews USING GIN (factor_tags);

-- ── 事前計算キャッシュ: 日次サマリ ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS f_daily (
    store_code  TEXT             NOT NULL,
    date        DATE             NOT NULL,
    grain       TEXT             NOT NULL CHECK (grain IN ('hour', 'day', 'month')),
    metric      TEXT             NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    updated_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (store_code, date, grain, metric)
);
COMMENT ON TABLE f_daily IS
    '夜間バッチが BigQuery を集計して書き戻す日次サマリ。画面はこれを読むだけ';

-- ── 事前計算キャッシュ: 施策サマリ（対比・達成率まで計算済み）────────────
CREATE TABLE IF NOT EXISTS f_campaign_summary (
    campaign_id       TEXT             NOT NULL REFERENCES m_campaigns (campaign_id) ON DELETE CASCADE,
    -- '*' は施策全体の合算。店舗別の値は store_code を入れる（NULL を避け主キーに使えるようにする）
    scope_store_code  TEXT             NOT NULL DEFAULT '*',
    metric            TEXT             NOT NULL,
    basis             TEXT             NOT NULL
        CHECK (basis IN ('actual', 'vs_target', 'vs_recent', 'vs_yoy', 'vs_prev_campaign')),
    actual_value      DOUBLE PRECISION,
    baseline_value    DOUBLE PRECISION,
    delta             DOUBLE PRECISION,
    delta_pct         DOUBLE PRECISION,
    achievement_rate  DOUBLE PRECISION,
    projection        DOUBLE PRECISION,
    as_of             DATE             NOT NULL,
    updated_at        TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, scope_store_code, metric, basis)
);
COMMENT ON COLUMN f_campaign_summary.projection IS
    '着地見込み。前年同施策の日別ペース曲線に今年実績を重ねて推定する';
COMMENT ON COLUMN f_campaign_summary.as_of IS
    '期間途中でも暫定値を出すため、どの時点までの集計かを保持する';
