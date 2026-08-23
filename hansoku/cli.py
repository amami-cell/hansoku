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

from .analytics import RATIO_METRICS, ratio, totals
from .db import AggregateQuery, get_appdb, get_warehouse
from .ingest.fw_sheet import TAB_TO_METRIC, ingest
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

    tabs = sub.add_parser("sheet-tabs", help="取り込み元シートのタブ一覧を出す（診断用）")
    tabs.add_argument("--spreadsheet", help="スプレッドシートID（既定はFW共有シート）")
    tabs.add_argument(
        "--sample",
        action="append",
        help="このタブの先頭数行を表示する（列構造の確認用。複数指定可）",
    )
    tabs.add_argument("--sample-rows", type=int, default=5, help="表示する行数")
    tabs.set_defaults(func=cmd_sheet_tabs, sample=None)

    grant = sub.add_parser("grant-admin", help="admin 権限を付与する")
    grant.add_argument("email")
    grant.set_defaults(func=cmd_grant_admin)

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
