-- 個人アカウント（名前＋自分のパスワード）と監査ログ。
-- 8888（APP_PASSWORD）は「参加コード＆初期化コード」として残し、初回登録・
-- パスワード忘れ時の再設定だけに使う。個人パスワードは PBKDF2 でハッシュ化して保存
-- （平文は保存しない）。オーナー（天見 真悟）は管理画面で権限変更・初期化ができる。
CREATE TABLE IF NOT EXISTS app_users (
    name        TEXT PRIMARY KEY,          -- ログイン名＝表示名
    pass_hash   TEXT NOT NULL DEFAULT '',  -- PBKDF2ハッシュ(base64)。未設定なら要パスワード設定
    pass_salt   TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'editor',   -- viewer / editor / owner
    disabled    BOOLEAN NOT NULL DEFAULT false,
    must_reset  BOOLEAN NOT NULL DEFAULT false,   -- オーナーが初期化した＝次回8888で再設定
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login  TIMESTAMPTZ
);
COMMENT ON TABLE app_users IS '個人アカウント。名前＋PBKDF2ハッシュ。8888は参加/初期化コード。';

-- 監査ログ：誰が・いつ・何をしたか（書き込み操作を記録）。
CREATE TABLE IF NOT EXISTS audit_log (
    id       BIGSERIAL PRIMARY KEY,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor    TEXT NOT NULL DEFAULT '',   -- app_users.name
    action   TEXT NOT NULL DEFAULT '',   -- plan.set / creative.add / note.set 等
    target   TEXT NOT NULL DEFAULT '',   -- 対象ID/短い説明
    detail   TEXT NOT NULL DEFAULT ''    -- 補足（任意）
);
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);
COMMENT ON TABLE audit_log IS '監査ログ。書き込み操作の 名前・日時・内容 を記録。';
