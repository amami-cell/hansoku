"""
FW 損益管理 → 実績管理業務 → 日別実績入力 から日別の実績を取り込む。

月別予算（fw_budget）と同じ独自ウィジェット（store-combo-box）＋検索＋
input で組んだグリッド。まず ``probe`` で日別グリッドの構造（何行・何列・
どのラベルにどの値）を吸い出し、それを見てから ``ingest`` を書く。

メニュー階層（探索で確認済み）:
  損益管理 → 実績管理業務(/app/profit_loss/pl-menu-actual-result)

probe の結論（2026-08 時点）: FW の日別実績はまとめ取りに向かない。
  * 日別実績入力       … 1日ずつの手入力画面（値は0が多い）。
  * 日別損益計算書（日別）… EJS TreeGrid の単日P&L（勘定科目別/当日・当月累計・
                          当年累計）。1ヶ月分の日別売上が一覧化されない。
いずれも「店×月で1枚に31日分の売上」という形では取れないため、日別実績の
一括取り込みは保留。probe はメニュー/グリッド構造を確認する診断用に残す。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .fw_browser import FWError, fw_session
# 月別予算で確立した部品を流用する（店舗コンボ・検索・月読み取り）
from .fw_budget import (
    _click_search,
    _combo_options,
    _dump_inputs_grouped,
    _read_month,
    _select_combo,
)

DAILY_MENU = ("損益管理", "実績管理業務", "日別損益計算書（日別）")


def _open_daily(session) -> None:
    """日別実績入力の画面まで遷移する。"""
    for label in DAILY_MENU:
        if not session.click_text(label):
            session.snapshot(f"missing_{label}")
            session.dump_clickables(f"failed_{label}")
            raise FWError(f"「{label}」に進めませんでした")
        session.snapshot(f"opened_{label}")


def _open_menu(session, labels) -> None:
    for label in labels:
        if not session.click_text(label):
            session.snapshot(f"missing_{label}")
            session.dump_clickables(f"failed_{label}")
            raise FWError(f"「{label}」に進めませんでした")
        session.snapshot(f"opened_{label}")


def report_probe(artifacts: Path, path_str: str) -> int:
    """カンマ区切りのメニューパスを順にクリックして開き、店舗を選び検索して
    グリッド構造を吸い出す。日別売上/客数がどの画面にあるかを特定する診断。
    例: "損益管理,実績管理業務,月別日別実績"
    """
    labels = [s.strip() for s in path_str.split(",") if s.strip()]
    last_label = labels[-1] if labels else path_str
    with fw_session(artifacts) as session:
        _open_menu(session, labels)
        items = session.dump_clickables("report_screen")
        print(f"[report] 「{last_label}」の操作要素 {len(items)}件")
        options = _combo_options(session)
        print(f"[report] 店舗コンボボックス {len(options)}件")
        if options:
            print(f"[report] 先頭店舗を選ぶ: {options[0]['value']} {options[0]['name']}")
            _select_combo(session, options[0]["value"])
        _click_search(session)
        time.sleep(1.2)
        session.snapshot("after_search")
        print(f"[report] 表示中の月: {_read_month(session)}")
        info = session.page.evaluate(
            """() => ({
            url: location.href,
            tables: document.querySelectorAll('table').length,
            inputs: [...document.querySelectorAll('input')].filter(
                i => i.offsetParent && !['button','checkbox','radio','submit'].includes(i.type)).length,
            hits: [...document.querySelectorAll('*')].filter(
                el => el.children.length === 0 && /客数|客単価|売上高|来店|人数/.test(el.innerText || ''))
                .slice(0, 24).map(el => (el.innerText || '').replace(/\\s+/g,' ').trim().slice(0, 16)),
        })"""
        )
        print(f"[report]   url={info['url']}")
        print(f"[report]   table={info['tables']} input={info['inputs']}")
        print(f"[report]   客数/客単価/売上のラベル: {info['hits']}")
        _dump_grid_tables(session)
        _dump_report_rows(session)
        _dump_inputs_grouped(session)
    print(f"\n成果物: {artifacts}")
    return 0


def _dump_grid_tables(session) -> None:
    """表（table）を2次元で吸い出す。日別は縦31行の表のことが多い。"""
    tables = session.page.evaluate(
        """() => {
        const clip = s => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 20);
        const out = [];
        for (const t of [...document.querySelectorAll('table')].filter(t => t.offsetParent).slice(0, 3)) {
            const rows = [];
            for (const tr of t.querySelectorAll('tr')) {
                const cells = [];
                for (const c of tr.querySelectorAll('th,td')) {
                    const inp = c.querySelector('input,select');
                    cells.push(clip(inp ? (inp.value || '') : c.innerText));
                }
                if (cells.some(x => x)) rows.push(cells);
            }
            out.push(rows);
        }
        return out;
    }"""
    )
    for ti, rows in enumerate(tables):
        print(f"---- 表 {ti}（{len(rows)}行）----")
        for row in rows[:40]:
            print("   ", " | ".join(row))


def _dump_report_rows(session) -> None:
    """div で組んだ帳票向け。日付(YYYY/MM/DD や M/D)を含む行を探して、その
    行の数値セルを並べて出す。表(table)でなくても日別行を捉えられる。"""
    rows = session.page.evaluate(
        """() => {
        const clip = s => (s || '').replace(/\\s+/g, ' ').trim();
        const dateRe = /^\\s*\\d{1,4}[\\/\\-年]\\d{1,2}([\\/\\-月]\\d{1,2})?|^\\s*\\d{1,2}\\s*[日()（]/;
        const out = [];
        // 日付らしいテキストを持つ葉を起点に、共通の行コンテナを推定して数値を集める
        const leaves = [...document.querySelectorAll('*')].filter(
            el => el.children.length === 0 && dateRe.test(el.innerText || ''));
        const seen = new Set();
        for (const leaf of leaves.slice(0, 40)) {
            let row = leaf;
            for (let i = 0; i < 4 && row.parentElement; i++) {
                row = row.parentElement;
                const nums = [...row.querySelectorAll('*')].filter(
                    e => e.children.length === 0 && /[0-9]/.test(e.innerText || ''));
                if (nums.length >= 3) break;
            }
            const key = clip(row.innerText).slice(0, 40);
            if (!key || seen.has(key)) continue;
            seen.add(key);
            const cells = [...row.querySelectorAll('*')]
                .filter(e => e.children.length === 0 && clip(e.innerText))
                .map(e => clip(e.innerText)).slice(0, 12);
            out.push(cells);
        }
        return out.slice(0, 20);
    }"""
    )
    print(f"---- 帳票の日別行らしきもの {len(rows)}件 ----")
    for r in rows:
        print("   ", " | ".join(r))


# ── 月別日別売上推移（販売管理→店舗業務）からの月次取り込み ──────────────
#
# この画面（URL 末尾 month_or_day_transition）は EJS TreeGrid で、1店ぶんの
# 直近12ヶ月を縦に並べ、各月について次の列を持つ:
#   予算 実績 達成率 予実差異 前年実績 前年比 前年差異
#   客数 客数予算比 客数前年実績 客数前年比 客数前年差異
#   客単価 前年客単価 客単価差異
# ここから 売上(実績)・売上前年(前年実績)・客数・客数前年 を取り、
#   * 当月ぶん   → その月の1日
#   * 前年ぶん   → 1年前の同月の1日
# として書く。1回のスクレイプで約24ヶ月ぶんの売上・客数が揃い、前年比・前月比を
# アプリ側で正確に出せるようになる（集客＝客数もこれで入る）。
#
# TreeGrid は固定列（期間）とスクロール列（数値）が別テーブルに分かれて描画される
# ことがあるため、セルを「画面上の縦位置(top)」でまとめて1つの視覚行に復元する。
URIAGE_SUII_MENU = ("販売管理", "店舗業務", "月別日別売上推移")

# パーセント列を除いた「整数セルだけ」の並び（左→右）と、そこでの位置:
#   0予算 1実績 2予実差異 3前年実績 4前年差異 5客数 6客数前年実績
#   7客数前年差異 8客単価 9前年客単価 10客単価差異
_INT_SALES = 1
_INT_SALES_PRIOR = 3
_INT_COVERS = 5
_INT_COVERS_PRIOR = 6


def _open_uriage_suii(session) -> None:
    """月別日別売上推移の画面まで遷移する。"""
    for label in URIAGE_SUII_MENU:
        if not session.click_text(label):
            session.snapshot(f"missing_{label}")
            session.dump_clickables(f"failed_{label}")
            raise FWError(f"「{label}」に進めませんでした")
        session.snapshot(f"opened_{label}")


def _extract_month_grid(session) -> list[dict]:
    """月別日別売上推移のグリッドを視覚行に復元して返す。

    各要素は {"period": "YYYY-MM", "ints": [整数のみ左→右]}。
    期間セル（YYYY年MM月）を含む視覚行だけを対象にするので、
    ヘッダ・メニュー・合計行（期間を持たない）は自然に除外される。
    """
    rows = session.page.evaluate(
        r"""() => {
        const clip = s => (s || '').replace(/\s+/g, ' ').trim();
        // 葉（子を持たない要素）のうち、可視でテキストがあるものを集める。
        // input は value を、それ以外は innerText を採る。
        const leaves = [];
        for (const el of document.querySelectorAll('td, th, div, span, input')) {
            if (!el.offsetParent) continue;
            if (el.children && el.children.length) continue;
            const txt = clip(el.tagName === 'INPUT' ? el.value : el.innerText);
            if (!txt) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            leaves.push({ top: Math.round(r.top / 4) * 4, left: Math.round(r.left), txt });
        }
        // 縦位置でまとめて1視覚行に。行内は左→右に並べる。
        const byTop = new Map();
        for (const lf of leaves) {
            if (!byTop.has(lf.top)) byTop.set(lf.top, []);
            byTop.get(lf.top).push(lf);
        }
        const out = [];
        for (const top of [...byTop.keys()].sort((a, b) => a - b)) {
            const cells = byTop.get(top).sort((a, b) => a.left - b.left).map(c => c.txt);
            out.push(cells);
        }
        return out;
    }"""
    )
    period_re = re.compile(r"(20\d{2})年\s*(\d{1,2})月")
    int_re = re.compile(r"^-?[\d,]+$")
    result: list[dict] = []
    seen: set[str] = set()
    for cells in rows:
        period = None
        for c in cells:
            m = period_re.search(c)
            if m:
                period = f"{m.group(1)}-{int(m.group(2)):02d}"
                break
        if not period or period in seen:
            continue
        ints: list[int] = []
        for c in cells:
            c2 = c.replace(",", "")
            if int_re.match(c) and any(ch.isdigit() for ch in c2):
                ints.append(int(c2))
        # 期間＋整数が十分あるものだけを1ヶ月の行として採る
        if len(ints) >= 7:
            seen.add(period)
            result.append({"period": period, "ints": ints})
    return result


def _prior_year(period: str) -> str:
    """"YYYY-MM" の1年前を返す。"""
    y, m = int(period[:4]), int(period[5:7])
    return f"{y - 1}-{m:02d}"


def ingest_monthly(
    warehouse,
    master,
    *,
    artifacts: Path,
    store_limit: int | None = None,
    dry_run: bool = False,
) -> int:
    """月別日別売上推移から各店の月次「売上・客数」を（前年ぶんも含めて）取り込む。

    画面は1店ずつ直近12ヶ月を表示。前年実績・客数前年実績の列を使い、
    前年の同月ぶんも同時に書くので、1スクレイプで約24ヶ月が揃う。
    """
    from datetime import date as _date
    from datetime import datetime, timezone

    from ..model import (
        GRAIN_MONTH,
        KIND_FINAL,
        METRIC_COVERS,
        METRIC_SALES,
        ActualRow,
    )
    from .fw_budget import _click_search, _combo_options, _select_combo

    source = "fw_uriage_suii"
    ingested_at = datetime.now(timezone.utc)
    active_by_code = {s.store_code: s for s in master.active}
    collected: list[ActualRow] = []

    def _month_date(period: str) -> _date:
        return _date(int(period[:4]), int(period[5:7]), 1)

    with fw_session(artifacts) as session:
        _open_uriage_suii(session)
        options = _combo_options(session)
        print(f"[売上推移] 店舗コンボボックス {len(options)}件")
        targets = []
        for opt in options:
            code = opt["value"].lstrip("0")
            store = active_by_code.get(code) or master.find_by_name(opt["name"])
            if store and store.active:
                targets.append((opt["value"], store))
        print(f"[売上推移] マスタと一致した稼働店 {len(targets)}件")
        if store_limit:
            targets = targets[:store_limit]

        for value, store in targets:
            name = store.store_name
            if not _select_combo(session, value):
                print(f"[売上推移] 店舗選択に失敗: {name} ({value})")
                continue
            _click_search(session)
            time.sleep(1.2)
            grid = _extract_month_grid(session)
            if not grid:
                session.snapshot(f"nogrid_{store.store_code}")
                print(f"  {store.store_code} {name[:14]} グリッドが読めませんでした")
                continue
            n_before = len(collected)
            for month in grid:
                ints = month["ints"]
                period = month["period"]
                if len(ints) <= _INT_COVERS_PRIOR:
                    continue

                def _add(metric: str, period_str: str, val: int) -> None:
                    if val <= 0:
                        return
                    collected.append(
                        ActualRow(
                            store_code=store.store_code,
                            date=_month_date(period_str),
                            grain=GRAIN_MONTH,
                            metric=metric,
                            value=float(val),
                            kind=KIND_FINAL,
                            source=source,
                            ingested_at=ingested_at,
                        )
                    )

                _add(METRIC_SALES, period, ints[_INT_SALES])
                _add(METRIC_COVERS, period, ints[_INT_COVERS])
                prior = _prior_year(period)
                _add(METRIC_SALES, prior, ints[_INT_SALES_PRIOR])
                _add(METRIC_COVERS, prior, ints[_INT_COVERS_PRIOR])

            months = [m["period"] for m in grid]
            span = f"{months[-1]}〜{months[0]}" if months else "-"
            sample = grid[0]
            print(
                f"  {store.store_code} {name[:14]} {len(grid)}ヶ月 {span} "
                f"（例 {sample['period']}: 売上 {sample['ints'][_INT_SALES]:,} / "
                f"客数 {sample['ints'][_INT_COVERS]:,}） +{len(collected) - n_before}行"
            )

    print(f"[売上推移] 収集 {len(collected)} 行")
    if dry_run:
        print("[売上推移] dry-run のため書き込みはしません")
        return 0
    warehouse.ensure_schema()
    loaded = warehouse.replace_actuals(collected)
    print(f"[売上推移] warehouse へ {loaded} 件 書き込みました")
    return 0


def probe(artifacts: Path) -> int:
    """日別実績入力に入り、店舗を選び検索して、グリッド構造を吸い出す。"""
    with fw_session(artifacts) as session:
        _open_daily(session)
        items = session.dump_clickables("daily_screen")
        print(f"[daily] 日別実績入力の操作要素 {len(items)} 件")

        options = _combo_options(session)
        print(f"[daily] 店舗コンボボックス {len(options)}件")
        if options:
            first = options[0]
            print(f"[daily] 先頭店舗を選ぶ: {first['value']} {first['name']}")
            _select_combo(session, first["value"])
        _click_search(session)
        time.sleep(1.0)
        session.snapshot("after_search")
        print(f"[daily] 表示中の月: {_read_month(session)}")

        info = session.page.evaluate(
            """() => ({
            url: location.href,
            tables: document.querySelectorAll('table').length,
            inputs: [...document.querySelectorAll('input')].filter(
                i => i.offsetParent && !['button','checkbox','radio','submit'].includes(i.type)).length,
            hits: [...document.querySelectorAll('*')].filter(
                el => el.children.length === 0 &&
                /売上|客数|客単価|日付|合計/.test(el.innerText || ''))
                .slice(0, 20).map(el => el.tagName.toLowerCase() + ':' + (el.innerText || '').replace(/\\s+/g,' ').trim().slice(0, 16)),
        })"""
        )
        print(f"[daily]   url={info['url']}")
        print(f"[daily]   table={info['tables']} input={info['inputs']}")
        print(f"[daily]   売上/客数などのラベル: {info['hits']}")
        _dump_grid_tables(session)
        _dump_report_rows(session)
        _dump_inputs_grouped(session)
    print(f"\n成果物: {artifacts}")
    return 0
