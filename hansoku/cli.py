"""
コマンドライン入口。

  python -m hansoku.cli init-schema     スキーマ作成＋店舗マスタ同期
  python -m hansoku.cli ingest-fw       FW共有シート → f_actuals
  python -m hansoku.cli aggregate       任意の店舗×期間×指標×時間帯で集計
  python -m hansoku.cli grant-admin     admin 権限を付与
  python -m hansoku.cli stores          店舗マスタの確認

HANSOKU_ENV=cloud で BigQuery / Neon / R2、未設定ならローカル実装に繋がる。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from .analytics import RATIO_METRICS, ratio, totals
from .db import AggregateQuery, get_appdb, get_warehouse
from .ingest.fw_sheet import TAB_TO_METRIC, ingest
from .ingest.infomart_sheet import ingest as infomart_ingest
from .ingest.sheets_client import FixtureSheetReader, GoogleSheetReader
from .model import GRAIN_MONTH, GRAINS
from .settings import load_settings
from .stores import StoreMaster


def _date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


# ── 各コマンド ──────────────────────────────────────────────────────────────
def cmd_init_schema(args: argparse.Namespace) -> int:
    settings = load_settings()
    master = StoreMaster.load(args.stores)

    with get_warehouse(settings) as warehouse:
        warehouse.ensure_schema()
        print(f"[warehouse] f_actuals を用意しました（{settings.env}）")

    with get_appdb(settings) as db:
        db.ensure_schema()
        count = db.sync_stores(master.all)
        print(f"[appdb] スキーマを適用し、店舗マスタ {count} 件を同期しました")
    return 0


def cmd_ingest_fw(args: argparse.Namespace) -> int:
    settings = load_settings()
    master = StoreMaster.load(args.stores)

    if args.fixture:
        reader = FixtureSheetReader.from_file(args.fixture)
        print(f"[source] フィクスチャ: {args.fixture}")
    else:
        if not settings.sources.service_account_json:
            print(
                "GOOGLE_SERVICE_ACCOUNT_JSON が未設定です。"
                " 認証情報を入れるか --fixture でローカル検証してください。",
                file=sys.stderr,
            )
            return 2
        reader = GoogleSheetReader(
            settings.sources.fw_spreadsheet_id, settings.sources.service_account_json
        )
        print(f"[source] 共有シート: {settings.sources.fw_spreadsheet_id}（読み取りのみ）")

    months = set(args.month) if args.month else None
    try:
        with get_warehouse(settings) as warehouse:
            report = ingest(
                reader, master, warehouse, year_months=months, strict=not args.lenient
            )
    except RuntimeError as exc:
        # 取りこぼしがあった場合。運用者が読む出力なので、トレースバックは出さずに
        # 何が落ちたかだけを示す。
        print(exc, file=sys.stderr)
        print(
            "\n店舗マスタに無い店名は config/stores.yaml に追加してください。"
            "\n取りこぼしを承知で取り込む場合は --lenient を付けてください。",
            file=sys.stderr,
        )
        return 1
    print(report.summary())
    return 0 if report.ok else 1


def cmd_aggregate(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not args.group_by:
        args.group_by = ["store_code"]
    with get_warehouse(settings) as warehouse:
        if args.metric in RATIO_METRICS:
            rows = ratio(
                warehouse,
                args.metric,
                date_from=args.date_from,
                date_to=args.date_to,
                store_codes=args.store or None,
                grain=args.grain,
                hours=args.hour or None,
            )
            for row in rows:
                shown = "―" if row.value is None else f"{row.value:.2%}"
                print(f"{row.store_code:>6}  {args.metric:<12} {shown:>10}")
            return 0

        rows = warehouse.aggregate(
            AggregateQuery(
                date_from=args.date_from,
                date_to=args.date_to,
                grain=args.grain,
                metrics=[args.metric],
                store_codes=args.store or None,
                hours=args.hour or None,
                group_by=tuple(args.group_by),
            )
        )
        if not rows:
            print("該当する実績がありません")
            return 0

        keys = list(args.group_by)
        header = "  ".join(f"{k:>10}" for k in keys)
        print(f"{header}  {'value':>15}")
        for row in rows:
            cells = "  ".join(f"{str(row[k]):>10}" for k in keys)
            print(f"{cells}  {row['value']:>15,.0f}")
        print(f"\n合計 {sum(r['value'] for r in rows):,.0f}  ({len(rows)} 行)")
    return 0


def cmd_ingest_infomart(args: argparse.Namespace) -> int:
    settings = load_settings()
    master = StoreMaster.load(args.stores)

    if args.fixture:
        reader = FixtureSheetReader.from_file(args.fixture)
        print(f"[source] フィクスチャ: {args.fixture}")
    else:
        if not settings.sources.service_account_json:
            print("GOOGLE_SERVICE_ACCOUNT_JSON が未設定です。", file=sys.stderr)
            return 2
        reader = GoogleSheetReader(
            settings.sources.fw_spreadsheet_id, settings.sources.service_account_json
        )
        print(f"[source] 共有シート「月次集計」タブ（読み取りのみ）")

    months = set(args.month) if args.month else None
    try:
        with get_warehouse(settings) as warehouse:
            report = infomart_ingest(
                reader, master, warehouse, year_months=months, strict=not args.lenient
            )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        print(
            "\n店舗マスタに無い店名は config/stores.yaml の infomart_name を確認してください。",
            file=sys.stderr,
        )
        return 1
    print(report.summary())
    return 0 if report.ok else 1


def cmd_sheet_tabs(args: argparse.Namespace) -> int:
    """
    取り込み元シートのタブ一覧を出す。

    どの指標が既に書き出されているかを確かめるための診断用。
    「インフォマートの集計タブはもう存在するのか」のような問いに、
    シートを開かずに答えられる。
    """
    settings = load_settings()
    if not settings.sources.service_account_json:
        print("GOOGLE_SERVICE_ACCOUNT_JSON が未設定です。", file=sys.stderr)
        return 2

    reader = GoogleSheetReader(
        args.spreadsheet or settings.sources.fw_spreadsheet_id,
        settings.sources.service_account_json,
    )
    known = set(TAB_TO_METRIC)
    tabs = reader.tab_names()
    print(f"タブ数: {len(tabs)}\n")
    for tab in tabs:
        mark = "取込済" if tab in known else "未使用"
        rows = len(reader.values(tab))
        print(f"  [{mark}] {tab}  （{rows} 行）")

    unused = [t for t in tabs if t not in known]
    if unused:
        print(f"\n取り込んでいないタブ: {', '.join(unused)}")

    for tab in args.sample or ():
        if tab not in tabs:
            print(f"\n[{tab}] このタブは存在しません", file=sys.stderr)
            continue
        rows = reader.values(tab)
        print(f"\n=== {tab} の先頭{min(len(rows), args.sample_rows)}行 ===")
        for index, row in enumerate(rows[: args.sample_rows], start=1):
            print(f"  {index:>3}: {row}")
    return 0


def cmd_fw_explore(args: argparse.Namespace) -> int:
    from .ingest.fw_explore import explore

    return explore(args.path or [], Path(args.artifacts))


def cmd_fw_stores(args: argparse.Namespace) -> int:
    from .ingest.fw_explore import list_stores

    return list_stores(Path(args.artifacts))


def cmd_fw_budget(args: argparse.Namespace) -> int:
    from .ingest.fw_budget import ingest, probe

    if args.mode == "probe":
        return probe(Path(args.artifacts))
    if args.mode == "manager-dl":
        from .ingest.fw_budget import probe_manager_dl

        return probe_manager_dl(Path(args.artifacts), month=args.month)
    if args.mode == "ingest":
        master = StoreMaster.load(args.stores)
        if args.dry_run:
            # dry-run は warehouse 不要（書き込まない）
            return ingest(
                None, master,
                artifacts=Path(args.artifacts),
                months_back=args.months, months_ahead=args.ahead,
                store_limit=args.limit, only_store=args.store or None, dry_run=True,
            )
        settings = load_settings()
        with get_warehouse(settings) as warehouse:
            return ingest(
                warehouse, master,
                artifacts=Path(args.artifacts),
                months_back=args.months, months_ahead=args.ahead,
                store_limit=args.limit, only_store=args.store or None, dry_run=False,
            )
    raise SystemExit(f"未知のモード: {args.mode}")


def cmd_fw_daily(args: argparse.Namespace) -> int:
    from .ingest.fw_daily import (
        ingest_abc,
        ingest_hourly,
        ingest_monthly,
        probe,
        report_probe,
    )

    if args.mode == "probe":
        return probe(Path(args.artifacts))
    if args.mode == "uriage-probe":
        from .ingest.fw_daily import probe_uriage_suii

        return probe_uriage_suii(Path(args.artifacts), month=args.month or "2024-03")
    if args.mode == "abc-store-probe":
        from .ingest.fw_daily import probe_abc_store

        def _slash(d: str | None) -> str:
            return (d or "").replace("-", "/")

        kw = {}
        if args.abc_levels:
            # 見たい分類だけに絞る。全部出すとログが千行を超えて、肝心の節が
            # 埋もれて読めない（部門だけ確かめたい場面が多い）。
            kw["levels"] = tuple(x.strip() for x in args.abc_levels.split(",") if x.strip())
        return probe_abc_store(
            Path(args.artifacts),
            store=args.abc_store or "ぎふや 天満橋店",
            d_from=_slash(args.abc_from),
            d_to=_slash(args.abc_to),
            **kw,
        )
    if args.mode == "abc-dom-probe":
        from .ingest.fw_daily import probe_abc_dom

        return probe_abc_dom(
            Path(args.artifacts),
            store=args.abc_store or "ひよこ飯店",
        )
    if args.mode == "monthly-coverage":
        from .ingest.fw_daily import report_monthly_coverage

        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return report_monthly_coverage(
                warehouse,
                master,
                date_from=args.abc_from or "2024-01",
                date_to=args.abc_to or "2026-08",
                metric=args.metric,
            )
    if args.mode == "source-audit":
        from .ingest.fw_daily import report_source_audit

        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return report_source_audit(
                warehouse,
                master,
                metric=args.metric,
                date_from=args.abc_from or "2024-09",
                date_to=args.abc_to or "2026-08",
            )
    if args.mode == "data-audit":
        from .ingest.fw_daily import report_data_audit

        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return report_data_audit(
                warehouse,
                master,
                date_from=args.abc_from or "2024-09",
                date_to=args.abc_to or "2026-08",
            )
    if args.mode == "abc-detail":
        from .ingest.fw_daily import report_abc_detail

        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return report_abc_detail(
                warehouse,
                master,
                month=args.month or "2025-12",
                store_filter=args.abc_store or None,
                items=args.abc_item or None,
            )
    if args.mode == "abc-coverage":
        from .ingest.fw_daily import report_abc_coverage

        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return report_abc_coverage(warehouse, master, month=args.month)
    if args.mode == "abc-store-ingest":
        from .ingest.fw_daily import ingest_abc_store

        if not args.abc_store:
            raise SystemExit("abc-store-ingest には --abc-store（店コード or 店名）が必要です")
        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return ingest_abc_store(
                warehouse,
                master,
                artifacts=Path(args.artifacts),
                store=args.abc_store,
                month=args.month,
                dry_run=args.dry_run,
            )
    if args.mode == "lunch-analyze":
        from .ingest.fw_daily import analyze_lunch

        return analyze_lunch(
            Path(args.artifacts),
            store=args.abc_store or "ぎふや 天満橋店",
            item=args.abc_item or "冷やし鶏",
            month=args.abc_month or "08",
            end_day=int(args.abc_end_day),
        )
    if args.mode == "abc-totals-probe":
        from .ingest.fw_daily import probe_abc_totals

        def _slash(d: str) -> str:
            return d.strip().replace("-", "/")

        raw = args.abc_ranges or (
            "基準:2026-08-01:2026-08-14,eタバコ後:2026-08-15:2026-08-16,"
            "空調後:2026-08-17:2026-08-23,新ランチ後:2026-08-24:2026-08-26"
        )
        ranges: list[tuple[str, str, str]] = []
        for chunk in raw.split(","):
            parts = chunk.split(":")
            if len(parts) != 3:
                continue
            label, d_from, d_to = parts
            ranges.append((label.strip(), _slash(d_from), _slash(d_to)))
        return probe_abc_totals(
            Path(args.artifacts),
            store=args.abc_store or "NagaGutsu",
            ranges=ranges,
        )
    if args.mode == "menu-hourly-probe":
        from .ingest.fw_daily import probe_menu_hourly

        def _slash(d: str) -> str:
            return d.strip().replace("-", "/")

        raw = args.hourly_ranges or (
            "基準:2026-08-01:2026-08-14,空調後:2026-08-17:2026-08-23"
        )
        ranges: list[tuple[str, str, str]] = []
        for chunk in raw.split(","):
            parts = chunk.split(":")
            if len(parts) != 3:
                continue
            label, d_from, d_to = parts
            ranges.append((label.strip(), _slash(d_from), _slash(d_to)))
        return probe_menu_hourly(
            Path(args.artifacts),
            store=args.hourly_store or "NagaGutsu",
            ranges=ranges,
        )
    if args.mode == "hourly-store-probe":
        from .ingest.fw_daily import probe_hourly_store

        def _slash(d: str) -> str:
            return d.strip().replace("-", "/")

        raw = args.hourly_ranges or (
            "基準:2026-08-01:2026-08-14,eタバコ後:2026-08-15:2026-08-16,"
            "空調後:2026-08-17:2026-08-23,新ランチ後:2026-08-24:2026-08-26"
        )
        ranges: list[tuple[str, str, str]] = []
        for chunk in raw.split(","):
            parts = chunk.split(":")
            if len(parts) != 3:
                continue
            label, d_from, d_to = parts
            ranges.append((label.strip(), _slash(d_from), _slash(d_to)))
        return probe_hourly_store(
            Path(args.artifacts),
            store=args.hourly_store or "NagaGutsu",
            ranges=ranges,
        )
    if args.mode == "report":
        return report_probe(Path(args.artifacts), args.menu or "損益管理,実績管理業務,月別日別実績")
    if args.mode in ("monthly", "monthly-dry"):
        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return ingest_monthly(
                warehouse,
                master,
                artifacts=Path(args.artifacts),
                store_limit=args.limit,
                dry_run=(args.mode == "monthly-dry" or args.dry_run),
                end_month=args.month,
                store_filter=args.abc_store or None,
            )
    if args.mode in ("hourly", "hourly-dry"):
        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return ingest_hourly(
                warehouse,
                master,
                artifacts=Path(args.artifacts),
                month=args.month,
                store_limit=args.limit,
                dry_run=(args.mode == "hourly-dry" or args.dry_run),
            )
    if args.mode in ("abc", "abc-dry"):
        settings = load_settings()
        master = StoreMaster.load(args.stores)
        with get_warehouse(settings) as warehouse:
            return ingest_abc(
                warehouse,
                master,
                artifacts=Path(args.artifacts),
                month=args.month,
                store_limit=args.limit,
                dry_run=(args.mode == "abc-dry" or args.dry_run),
            )
    raise SystemExit(f"未知のモード: {args.mode}")


def cmd_promo_migrate_keys(args: argparse.Namespace) -> int:
    """目標・メモの鍵を 素の施策id → id@開始年 に移す（一度きり）。"""
    from .web.export import load_schedule

    master = StoreMaster.load(args.stores)
    keys = {c["id"]: c["key"] for c in load_schedule(master)}
    settings = load_settings()
    with get_appdb(settings) as db:
        moved = db.migrate_promo_keys(keys, dry_run=args.dry_run)
    if not moved:
        print("移すものはありません（すべて鍵つきです）。")
        return 0
    print(("[試算] " if args.dry_run else "") + f"{len(moved)}件:")
    for line in moved:
        print(f"  {line}")
    return 0


def cmd_schedule_lint(args: argparse.Namespace) -> int:
    """台帳（config/schedule.yaml）の書き方を検査する。DBもFWも要らない。"""
    from .schedule_lint import report_schedule_lint

    master = StoreMaster.load(args.stores)
    return report_schedule_lint(master, args.path)


def cmd_export_web(args: argparse.Namespace) -> int:
    from .web.export import build, load_creatives, load_schedule, write

    settings = load_settings()
    master = StoreMaster.load(args.stores)
    campaigns = load_schedule(master)
    creatives = load_creatives(master, campaigns)
    # 目標と要因メモは dashboard.json に焼き込まない。
    #
    # 画面は /api/targets・/api/notes から直に読む（Cloudflare Access の内側）。
    # 焼き込むと、この JSON をそのまま公開するデザイン確認用プレビュー
    # （deploy-preview.yml・認証なし）に、本部の要因メモまで載ってしまう。
    # 目標・メモの置き場所をアプリに一本化したので、焼き込む必要も無くなった。
    with get_warehouse(settings) as warehouse:
        payload = build(
            warehouse,
            master,
            date_from=args.date_from,
            date_to=args.date_to,
            campaigns=campaigns,
            creatives=creatives,
        )
    path = write(payload, Path(args.out))
    print(f"書き出し完了: {path}")
    print(f"  店舗 {len(payload['stores'])} / 月 {len(payload['months'])}"
          f" / 施策 {len(payload['campaigns'])} / 制作物 {len(payload['creatives'])}")
    return 0


def cmd_creatives_upload(args: argparse.Namespace) -> int:
    """制作物PDFを R2（ローカルはFS）へ保存し、creatives.yaml に貼る1行を出す。"""
    from datetime import date as _date

    from .db import creative_key, get_object_store

    src = Path(args.file)
    if not src.exists():
        raise SystemExit(f"ファイルが見つかりません: {src}")
    data = src.read_bytes()
    year = args.year or _date.today().year
    campaign_id = args.campaign or "misc"
    key = creative_key(year, campaign_id, src.name)

    ctype = "application/pdf" if src.suffix.lower() == ".pdf" else "application/octet-stream"
    store = get_object_store(load_settings())
    url = store.put(key, data, content_type=ctype)
    title = args.title or src.stem

    print(f"保存しました（{len(data):,} bytes）: {key}")
    print(f"  参照URL: {url}")
    print("\n── config/creatives.yaml に以下を追記してください ──")
    print(f"- id: {campaign_id}-{src.stem}")
    print(f"  title: {title}")
    if args.campaign:
        print(f"  campaign: {campaign_id}")
    if args.date:
        print(f"  date: {args.date}")
    print(f"  file: {key}")
    return 0


def cmd_creatives_sync(args: argparse.Namespace) -> int:
    """creatives.yaml が指す file(R2キー) を、手元のソースPDFから R2 へ揃える。

    ソースは assets/creatives-src/（コミット済み）。キーの basename が一致する
    PDF を、宣言どおりのキーで put する（冪等・上書き）。デプロイから毎回呼べば
    R2 は台帳に追従する。既に同じキーがあれば飛ばす。
    """
    from .db import get_object_store
    from .web.export import load_creatives, load_schedule

    src_dir = Path(args.src)
    master = StoreMaster.load(args.stores)
    creatives = load_creatives(master, load_schedule(master))
    store = get_object_store(load_settings())

    put = skipped = missing = 0
    for cr in creatives:
        key = cr["url"].lstrip("/")
        local = src_dir / Path(key).name
        if not local.exists():
            print(f"[creatives] ソース無し（スキップ）: {local}")
            missing += 1
            continue
        if not args.force and store.exists(key):
            print(f"[creatives] 既にR2にあり（スキップ）: {key}")
            skipped += 1
            continue
        ctype = "application/pdf" if local.suffix.lower() == ".pdf" else "application/octet-stream"
        store.put(key, local.read_bytes(), content_type=ctype)
        print(f"[creatives] R2へ: {key}（{local.stat().st_size:,} bytes）")
        put += 1
    print(f"[creatives] 完了: 追加 {put} / 既存 {skipped} / ソース無し {missing}")
    return 0


def cmd_creatives_list(args: argparse.Namespace) -> int:
    """creatives.yaml を解決して一覧表示する（画面に載る形の確認）。"""
    from .web.export import load_creatives, load_schedule

    master = StoreMaster.load(args.stores)
    creatives = load_creatives(master, load_schedule(master))
    if not creatives:
        print("制作物はまだありません（config/creatives.yaml は空、または file 未設定）。")
        return 0
    for cr in creatives:
        scope = "全店" if cr["scope_all"] else f"{len(cr['stores'])}店"
        link = f" ←{cr['campaign_title']}" if cr["campaign_title"] else ""
        print(f"[{cr['date'] or '日付なし'}] {cr['title']}（{scope}{link}）  {cr['url']}")
    print(f"\n合計 {len(creatives)} 件")
    return 0


def cmd_grant_admin(args: argparse.Namespace) -> int:
    with get_appdb() as db:
        count = db.grant_admin(args.email)
    print(f"{args.email} に全 {count} 店の admin 権限を付与しました")
    return 0


def cmd_stores(args: argparse.Namespace) -> int:
    master = StoreMaster.load(args.stores)
    for store in master.all:
        flag = "" if store.active else "（休止）"
        shared = " [共営施設]" if store.is_shared_facility else ""
        print(f"{store.store_code:>6}  {store.brand:<16} {store.store_name}{shared}{flag}")
    print(f"\n合計 {len(master)} 店 / 稼働 {len(master.active)} 店")
    return 0


# ── パーサ ──────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hansoku", description=__doc__)
    parser.add_argument("--stores", default=None, help="店舗マスタYAMLのパス")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-schema", help="スキーマ作成と店舗マスタ同期").set_defaults(
        func=cmd_init_schema
    )

    ingest_parser = sub.add_parser("ingest-fw", help="FW共有シートから実績を取り込む")
    ingest_parser.add_argument("--fixture", help="共有シートの代わりに読むJSON（ローカル検証用）")
    ingest_parser.add_argument(
        "--month", action="append", help="対象年月 YYYY-MM（複数指定可。既定は全件）"
    )
    ingest_parser.add_argument(
        "--lenient",
        action="store_true",
        help="取りこぼしがあっても中断しない（既定は中断して報告する）",
    )
    ingest_parser.set_defaults(func=cmd_ingest_fw)

    agg = sub.add_parser("aggregate", help="任意の店舗×期間×指標×時間帯で集計する")
    agg.add_argument("--metric", required=True, help="sales / cost_rate など")
    agg.add_argument("--date-from", required=True, type=_date, dest="date_from")
    agg.add_argument("--date-to", required=True, type=_date, dest="date_to")
    agg.add_argument("--store", action="append", help="店舗コード（複数指定可）")
    agg.add_argument("--grain", default=GRAIN_MONTH, choices=GRAINS)
    agg.add_argument("--hour", action="append", type=int, help="時（0-23、複数指定可）")
    agg.add_argument(
        "--group-by",
        action="append",
        dest="group_by",
        choices=list(AggregateQuery.ALLOWED_GROUP_BY),
        help="束ね方（複数指定可。既定は store_code）。date を指定すると月別の推移が見られる",
    )
    agg.set_defaults(func=cmd_aggregate, group_by=None)

    im = sub.add_parser(
        "ingest-infomart", help="インフォマート棚卸（月次集計タブ）から実績を取り込む"
    )
    im.add_argument("--fixture", help="共有シートの代わりに読むJSON（ローカル検証用）")
    im.add_argument("--month", action="append", help="対象年月 YYYY-MM（複数指定可）")
    im.add_argument("--lenient", action="store_true", help="取りこぼしがあっても中断しない")
    im.set_defaults(func=cmd_ingest_infomart)

    tabs = sub.add_parser("sheet-tabs", help="取り込み元シートのタブ一覧を出す（診断用）")
    tabs.add_argument("--spreadsheet", help="スプレッドシートID（既定はFW共有シート）")
    tabs.add_argument(
        "--sample",
        action="append",
        help="このタブの先頭数行を表示する（列構造の確認用。複数指定可）",
    )
    tabs.add_argument("--sample-rows", type=int, default=5, help="表示する行数")
    tabs.set_defaults(func=cmd_sheet_tabs, sample=None)

    explore = sub.add_parser(
        "fw-explore", help="FWの画面構造を調べる（セレクタを書く前の下調べ用）"
    )
    explore.add_argument(
        "--path",
        action="append",
        help="順にクリックするメニュー名（複数指定可）",
    )
    explore.add_argument("--artifacts", default=".local/fw-artifacts", help="記録の保存先")
    explore.set_defaults(func=cmd_fw_explore)

    fwstores = sub.add_parser(
        "fw-stores", help="FWの店舗選択（エリア:イニシエート）の全店を吸い出す"
    )
    fwstores.add_argument("--artifacts", default=".local/fw-artifacts", help="記録の保存先")
    fwstores.set_defaults(func=cmd_fw_stores)

    fwbudget = sub.add_parser(
        "fw-budget", help="FW月別予算登録から売上予算を取り込む"
    )
    fwbudget.add_argument("--mode", default="probe", choices=["probe", "ingest", "manager-dl"], help="動作")
    fwbudget.add_argument("--months", type=int, default=3, help="遡る月数（当月含む）")
    fwbudget.add_argument("--ahead", type=int, default=0, help="先付け予算を読む先の月数")
    fwbudget.add_argument("--store", default=None, help="1店だけ深く遡る: 店コード or 店名の一部")
    fwbudget.add_argument("--month", default="2026-07", help="manager-dl: 対象月 YYYY-MM")
    fwbudget.add_argument("--limit", type=int, default=None, help="先頭N店だけ（試走用）")
    fwbudget.add_argument("--dry-run", action="store_true", help="書き込まず印字のみ")
    fwbudget.add_argument("--stores", default=None, help="stores.yaml のパス")
    fwbudget.add_argument("--artifacts", default=".local/fw-artifacts", help="記録の保存先")
    fwbudget.set_defaults(func=cmd_fw_budget)

    fwdaily = sub.add_parser(
        "fw-daily", help="FW日別実績入力から日別の実績を取り込む（まずprobe）"
    )
    fwdaily.add_argument(
        "--mode",
        default="probe",
        choices=["probe", "ingest", "report", "monthly", "monthly-dry",
                 "hourly", "hourly-dry", "abc", "abc-dry", "abc-store-probe",
                 "lunch-analyze", "hourly-store-probe", "abc-totals-probe",
                 "menu-hourly-probe", "abc-store-ingest", "abc-coverage",
                 "abc-dom-probe", "uriage-probe", "monthly-coverage",
                 "abc-detail", "data-audit", "source-audit"],
        help="動作（monthly=月別日別売上推移、hourly=時間帯別売上、abc=ABC分析から取り込む）",
    )
    fwdaily.add_argument(
        "--month",
        default=None,
        help="hourly/abc の対象月（YYYY-MM、既定は前月）。"
        "monthly では『対象月』＝末尾の月（カンマ区切りで複数可・過去バックフィル用）",
    )
    fwdaily.add_argument("--abc-store", default=None, dest="abc_store",
                         help="abc-store-probe/lunch-analyze の対象店名（部分一致）")
    fwdaily.add_argument("--abc-from", default=None, dest="abc_from",
                         help="abc-store-probe の開始日 YYYY-MM-DD")
    fwdaily.add_argument("--abc-to", default=None, dest="abc_to",
                         help="abc-store-probe の終了日 YYYY-MM-DD")
    fwdaily.add_argument("--abc-levels", default=None, dest="abc_levels",
                         help="abc-store-probe で出す分類（カンマ区切り。既定=全商品,部門,グループ,メニュー）")
    fwdaily.add_argument("--abc-item", default=None, dest="abc_item",
                         help="lunch-analyze: 開始日検出に使う新商品名の一部（既定=冷やし鶏）")
    fwdaily.add_argument("--abc-month", default=None, dest="abc_month",
                         help="lunch-analyze: 対象月 MM（既定=08）")
    fwdaily.add_argument("--abc-end-day", default=25, dest="abc_end_day",
                         help="lunch-analyze: 期間の終了日（既定=25）")
    fwdaily.add_argument("--hourly-store", default=None, dest="hourly_store",
                         help="hourly-store-probe の対象店名（コンボ部分一致、既定=NagaGutsu）")
    fwdaily.add_argument("--hourly-ranges", default=None, dest="hourly_ranges",
                         help="hourly-store-probe の期間 'ラベル:from:to,...'（YYYY-MM-DD）")
    fwdaily.add_argument("--abc-ranges", default=None, dest="abc_ranges",
                         help="abc-totals-probe の期間 'ラベル:from:to,...'（YYYY-MM-DD）")
    fwdaily.add_argument(
        "--metric",
        default=None,
        help="monthly-coverage で数える指標（既定 sales。ABCの穴探しは dept_sales）",
    )
    fwdaily.add_argument("--menu", default=None, help="report モードで開く帳票名")
    fwdaily.add_argument("--months", type=int, default=2, help="遡る月数")
    fwdaily.add_argument("--limit", type=int, default=None, help="先頭N店だけ（試走用）")
    fwdaily.add_argument("--dry-run", action="store_true", help="書き込まず印字のみ")
    fwdaily.add_argument("--stores", default=None, help="stores.yaml のパス")
    fwdaily.add_argument("--artifacts", default=".local/fw-artifacts", help="記録の保存先")
    fwdaily.set_defaults(func=cmd_fw_daily)

    export = sub.add_parser("export-web", help="画面が読む JSON を書き出す")
    export.add_argument("--date-from", required=True, type=_date, dest="date_from")
    export.add_argument("--date-to", required=True, type=_date, dest="date_to")
    export.add_argument("--out", default="web/data", help="書き出し先ディレクトリ")
    export.set_defaults(func=cmd_export_web)

    pmk = sub.add_parser(
        "promo-migrate-keys",
        help="目標・メモの鍵を 素の施策id → id@開始年 へ移す（一度きり）",
    )
    pmk.add_argument("--dry-run", action="store_true", help="書き換えず、移す対象だけ出す")
    pmk.set_defaults(func=cmd_promo_migrate_keys)

    slint = sub.add_parser(
        "schedule-lint",
        help="台帳 config/schedule.yaml を検査する（黙って消える書き方を見つける）",
    )
    slint.add_argument("--path", default=None, help="schedule.yaml のパス")
    slint.set_defaults(func=cmd_schedule_lint)

    grant = sub.add_parser("grant-admin", help="admin 権限を付与する")
    grant.add_argument("email")
    grant.set_defaults(func=cmd_grant_admin)

    cup = sub.add_parser("creatives-upload", help="制作物PDFをR2へ保存する")
    cup.add_argument("file", help="アップロードするPDFのパス")
    cup.add_argument("--campaign", default=None, help="紐づく施策ID（schedule.yaml）")
    cup.add_argument("--title", default=None, help="表示名（省略時はファイル名）")
    cup.add_argument("--year", type=int, default=None, help="保管年（省略時は今年）")
    cup.add_argument("--date", default=None, help="掲出日 YYYY-MM-DD（貼付用）")
    cup.set_defaults(func=cmd_creatives_upload)

    csync = sub.add_parser(
        "creatives-sync", help="creatives.yaml のPDFをソースからR2へ揃える"
    )
    csync.add_argument("--src", default="assets/creatives-src", help="ソースPDFの置き場")
    csync.add_argument("--stores", default=None, help="stores.yaml のパス")
    csync.add_argument("--force", action="store_true", help="既存キーも上書きする")
    csync.set_defaults(func=cmd_creatives_sync)

    sub.add_parser("creatives-list", help="制作物ギャラリーの一覧を表示する").set_defaults(
        func=cmd_creatives_list
    )

    sub.add_parser("stores", help="店舗マスタを表示する").set_defaults(func=cmd_stores)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        # `| head` のように出力を途中で打ち切られた場合。異常ではないので静かに終わる。
        try:
            sys.stdout.close()
        finally:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
