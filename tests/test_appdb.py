"""
Neon（PostgreSQL）スキーマの検証。

配列・JSONB・CHECK 制約・外部キーは実PostgreSQLでしか検証できないため、
ここは必ず実DBに対して流す（HANSOKU_TEST_DATABASE_URL）。
"""
import pytest


EXPECTED_TABLES = {
    "m_stores", "m_access", "m_timeslots", "m_campaigns", "m_goals",
    "m_share_targets", "m_creatives", "m_reviews", "f_daily", "f_campaign_summary",
    "promo_targets", "promo_notes",
}


class Testスキーマ:
    def test_全テーブルが作られる(self, appdb):
        rows = appdb.query(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        assert {r["table_name"] for r in rows} >= EXPECTED_TABLES


class Test販促目標:
    def test_登録更新削除できる(self, appdb):
        assert appdb.list_promo_targets() == {}
        appdb.set_promo_target("s1006-natsu", 16_000_000, set_by="amami@8sin.co.jp")
        assert appdb.list_promo_targets() == {"s1006-natsu": 16_000_000}
        # 同じidは上書き（更新者も残る）
        appdb.set_promo_target("s1006-natsu", 17_000_000, set_by="other@example.com")
        assert appdb.list_promo_targets()["s1006-natsu"] == 17_000_000
        row = appdb.query(
            "SELECT set_by FROM promo_targets WHERE campaign_id = 's1006-natsu'"
        )[0]
        assert row["set_by"] == "other@example.com"
        # 削除
        appdb.delete_promo_target("s1006-natsu")
        assert appdb.list_promo_targets() == {}

    def test_何度流しても壊れない(self, appdb):
        appdb.ensure_schema()
        appdb.ensure_schema()
        rows = appdb.query("SELECT count(*) c FROM m_stores")
        assert rows[0]["c"] >= 0


class Test要因メモ:
    def test_登録更新削除できる(self, appdb):
        assert appdb.list_promo_notes() == {}
        appdb.set_promo_note("s1006-natsu", "客足が伸びた。天候も良好", set_by="amami@8sin.co.jp")
        assert appdb.list_promo_notes() == {"s1006-natsu": "客足が伸びた。天候も良好"}
        # 上書き（更新者も残る）
        appdb.set_promo_note("s1006-natsu", "前年並みに落ち着いた", set_by="other@example.com")
        assert appdb.list_promo_notes()["s1006-natsu"] == "前年並みに落ち着いた"
        row = appdb.query(
            "SELECT set_by FROM promo_notes WHERE campaign_id = 's1006-natsu'"
        )[0]
        assert row["set_by"] == "other@example.com"
        # 削除
        appdb.delete_promo_note("s1006-natsu")
        assert appdb.list_promo_notes() == {}

    def test_空メモは一覧に出さない(self, appdb):
        appdb.set_promo_note("s1006-natsu", "   ", set_by="amami@8sin.co.jp")
        assert appdb.list_promo_notes() == {}


class Test店舗マスタ同期:
    def test_全店が入る(self, appdb, master):
        assert appdb.sync_stores(master.all) == 24
        assert appdb.query("SELECT count(*) c FROM m_stores")[0]["c"] == 24

    def test_再同期しても重複しない(self, appdb, master):
        appdb.sync_stores(master.all)
        appdb.sync_stores(master.all)
        assert appdb.query("SELECT count(*) c FROM m_stores")[0]["c"] == 24

    def test_変更が反映される(self, appdb, master):
        appdb.sync_stores(master.all)
        appdb.execute("UPDATE m_stores SET store_name = 'ずれた名前' WHERE store_code = '1015'")
        appdb.sync_stores(master.all)
        row = appdb.query("SELECT store_name FROM m_stores WHERE store_code = '1015'")[0]
        assert row["store_name"] == "すさび湯 歌舞伎町"

    def test_インフォマートのコードも保存される(self, appdb, master):
        appdb.sync_stores(master.all)
        row = appdb.query("SELECT infomart_code FROM m_stores WHERE store_code = '1154'")[0]
        assert row["infomart_code"] == "922"

    def test_エリアも保存される(self, appdb, master):
        appdb.sync_stores(master.all)
        rows = appdb.query(
            "SELECT region, count(*) c FROM m_stores GROUP BY region ORDER BY c DESC"
        )
        top = rows[0]
        assert top["region"] == "大阪" and top["c"] == 16


class Test権限:
    @pytest.fixture
    def seeded(self, appdb, master):
        appdb.sync_stores(master.all)
        return appdb

    def test_adminは全店見られる(self, seeded):
        seeded.grant_admin("amami@8sin.co.jp")
        assert len(seeded.stores_for("amami@8sin.co.jp")) == 24

    def test_店長は担当店だけ見られる(self, seeded):
        seeded.grant("tencho@example.com", "1015", "manager")
        seeded.grant("tencho@example.com", "1743", "manager")
        assert seeded.stores_for("tencho@example.com") == ["1015", "1743"]

    def test_未登録ユーザーは何も見られない(self, seeded):
        assert seeded.stores_for("stranger@example.com") == []

    def test_メールアドレスの大文字小文字を区別しない(self, seeded):
        seeded.grant("Tencho@Example.com", "1015", "manager")
        assert seeded.stores_for("tencho@example.com") == ["1015"]

    def test_休止店はadminの閲覧範囲から外れる(self, seeded):
        seeded.grant_admin("amami@8sin.co.jp")
        seeded.execute("UPDATE m_stores SET active = false WHERE store_code = '1015'")
        assert "1015" not in seeded.stores_for("amami@8sin.co.jp")


class Test施策の制約:
    @pytest.fixture
    def seeded(self, appdb, master):
        appdb.sync_stores(master.all)
        return appdb

    def _insert(self, db, **overrides):
        values = dict(
            campaign_id="2026-08-susabiyu-hamo",
            name="鱧フェア",
            scope="store",
            target_stores=["1015"],
            date_from="2026-08-01",
            date_to="2026-08-31",
            primary_metric="sales",
            campaign_type="share",
            comparisons=["vs_yoy", "vs_target"],
            created_by="amami@8sin.co.jp",
        )
        values.update(overrides)
        db.execute(
            """
            INSERT INTO m_campaigns (campaign_id, name, scope, target_stores, date_from,
                                     date_to, primary_metric, campaign_type, comparisons, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            tuple(values.values()),
        )

    def test_施策を登録できる(self, seeded):
        self._insert(seeded)
        row = seeded.query("SELECT * FROM m_campaigns")[0]
        assert row["target_stores"] == ["1015"]
        assert row["comparisons"] == ["vs_yoy", "vs_target"]

    def test_未知の施策タイプは弾く(self, seeded):
        with pytest.raises(Exception):
            self._insert(seeded, campaign_type="unknown_type")

    def test_未知の比較基準は弾く(self, seeded):
        with pytest.raises(Exception):
            self._insert(seeded, comparisons=["vs_moon"])

    def test_期間が逆なら弾く(self, seeded):
        with pytest.raises(Exception):
            self._insert(seeded, date_from="2026-09-01", date_to="2026-08-01")

    def test_目標を施策にぶら下げられる(self, seeded):
        self._insert(seeded)
        seeded.execute(
            """
            INSERT INTO m_goals (goal_id, campaign_id, metric, segment, target_type,
                                 target_value, target_direction, scope, set_by)
            VALUES ('g1','2026-08-susabiyu-hamo','sales','{"weekday_type":"weekday"}',
                    'relative_yoy', 10, 'higher_better', 'store', 'amami@8sin.co.jp')
            """
        )
        row = seeded.query("SELECT segment FROM m_goals")[0]
        assert row["segment"] == {"weekday_type": "weekday"}

    def test_原価型の目標はlower_betterで持てる(self, seeded):
        self._insert(seeded, campaign_type="cost", primary_metric="cost_rate")
        seeded.execute(
            """
            INSERT INTO m_goals (goal_id, campaign_id, metric, target_type, target_value,
                                 target_direction, scope, set_by)
            VALUES ('g2','2026-08-susabiyu-hamo','cost_rate','absolute', 28,
                    'lower_better', 'store', 'amami@8sin.co.jp')
            """
        )
        assert seeded.query("SELECT target_direction FROM m_goals")[0][
            "target_direction"
        ] == "lower_better"

    def test_share型の対象を店舗別に持てる(self, seeded):
        self._insert(seeded)
        seeded.execute(
            """
            INSERT INTO m_share_targets (id, campaign_id, store_code, match_type, match_values)
            VALUES ('s1','2026-08-susabiyu-hamo','1015','category', ARRAY['刺身']),
                   ('s2','2026-08-susabiyu-hamo','1743','product',  ARRAY['鱧の落とし','鱧天'])
            """
        )
        rows = seeded.query("SELECT store_code, match_type, match_values FROM m_share_targets ORDER BY id")
        assert rows[0]["match_type"] == "category"
        assert rows[1]["match_values"] == ["鱧の落とし", "鱧天"]

    def test_同じ施策に同じ店を二重登録できない(self, seeded):
        self._insert(seeded)
        seeded.execute(
            "INSERT INTO m_share_targets VALUES ('s1','2026-08-susabiyu-hamo','1015','category', ARRAY['刺身'])"
        )
        with pytest.raises(Exception):
            seeded.execute(
                "INSERT INTO m_share_targets VALUES ('s2','2026-08-susabiyu-hamo','1015','product', ARRAY['鱧'])"
            )

    def test_施策を消すとぶら下がりも消える(self, seeded):
        self._insert(seeded)
        seeded.execute(
            """
            INSERT INTO m_reviews (review_id, campaign_id, factor_tags, memo, written_by)
            VALUES ('r1','2026-08-susabiyu-hamo', ARRAY['天候','仕入交渉'], '長雨で客足が鈍い', 'amami@8sin.co.jp')
            """
        )
        seeded.execute("DELETE FROM m_campaigns WHERE campaign_id = '2026-08-susabiyu-hamo'")
        assert seeded.query("SELECT count(*) c FROM m_reviews")[0]["c"] == 0

    def test_集計結果は施策全体と店舗別を同居させられる(self, seeded):
        self._insert(seeded)
        seeded.execute(
            """
            INSERT INTO f_campaign_summary (campaign_id, scope_store_code, metric, basis,
                                            actual_value, as_of)
            VALUES ('2026-08-susabiyu-hamo','*',   'sales','actual', 1000, '2026-08-15'),
                   ('2026-08-susabiyu-hamo','1015','sales','actual',  600, '2026-08-15')
            """
        )
        assert seeded.query("SELECT count(*) c FROM f_campaign_summary")[0]["c"] == 2


class Testスキーマの移行:
    """
    列を後から足したとき、既に作られているテーブルにも反映されること。

    CREATE TABLE IF NOT EXISTS は既存テーブルには何もしないため、
    新しい列は ALTER で明示的に足す必要がある。これを忘れると、
    ローカルの作り立てDBでは通るのに本番だけ落ちる。
    """

    def test_旧スキーマのテーブルにも列が足される(self, appdb, master):
        # infomart_code が無かった頃の m_stores を再現する
        appdb.execute("ALTER TABLE m_stores DROP COLUMN IF EXISTS infomart_code")
        columns = appdb.query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'm_stores'"
        )
        assert "infomart_code" not in {c["column_name"] for c in columns}

        appdb.ensure_schema()

        columns = appdb.query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'm_stores'"
        )
        assert "infomart_code" in {c["column_name"] for c in columns}
        # 移行後に書き込めることまで確かめる（列があるだけでは足りない）
        assert appdb.sync_stores(master.all) == 24

    def test_移行しても既存データは消えない(self, appdb, master):
        appdb.sync_stores(master.all)
        appdb.execute("ALTER TABLE m_stores DROP COLUMN IF EXISTS infomart_code")
        appdb.ensure_schema()
        assert appdb.query("SELECT count(*) c FROM m_stores")[0]["c"] == 24


class Test鍵の移行:
    """目標・メモは施策の「回」にぶら下がる（id@開始年）。

    素の id のままだと、来年の秋おすすめが今年の目標・メモを上書きする。
    すでに画面から入れ直したぶんを、古い値で潰さないことが肝心。
    """

    def test_素のidを鍵つきへ移す(self, appdb):
        appdb.set_promo_target("r1006-osusume", 16_000_000)
        appdb.set_promo_note("r1006-osusume", "客足が伸びた")
        moved = appdb.migrate_promo_keys({"r1006-osusume": "r1006-osusume@2026"})
        assert len(moved) == 2
        assert appdb.list_promo_targets() == {"r1006-osusume@2026": 16_000_000}
        assert appdb.list_promo_notes() == {"r1006-osusume@2026": "客足が伸びた"}

    def test_試算では書き換えない(self, appdb):
        appdb.set_promo_target("r1006-osusume", 16_000_000)
        moved = appdb.migrate_promo_keys(
            {"r1006-osusume": "r1006-osusume@2026"}, dry_run=True
        )
        assert moved
        assert appdb.list_promo_targets() == {"r1006-osusume": 16_000_000}

    def test_新しい鍵が既にあれば古い方で潰さない(self, appdb):
        appdb.set_promo_target("r1006-osusume", 16_000_000)          # 古い
        appdb.set_promo_target("r1006-osusume@2026", 20_000_000)     # 画面から入れ直した
        appdb.migrate_promo_keys({"r1006-osusume": "r1006-osusume@2026"})
        assert appdb.list_promo_targets()["r1006-osusume@2026"] == 20_000_000

    def test_台帳に無いidは触らない(self, appdb):
        appdb.set_promo_target("消えた施策", 1_000_000)
        appdb.migrate_promo_keys({"r1006-osusume": "r1006-osusume@2026"})
        assert appdb.list_promo_targets() == {"消えた施策": 1_000_000}

    def test_何度流しても同じ(self, appdb):
        appdb.set_promo_target("r1006-osusume", 16_000_000)
        keys = {"r1006-osusume": "r1006-osusume@2026"}
        appdb.migrate_promo_keys(keys)
        assert appdb.migrate_promo_keys(keys) == []
        assert appdb.list_promo_targets() == {"r1006-osusume@2026": 16_000_000}
