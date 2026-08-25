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


# EJS TreeGrid は固定列とスクロール列を別テーブルに描くため、DOMの葉を
# 「画面上の縦位置(top)」でまとめて1つの視覚行に復元する。月次・時間帯別で共用。
_VISUAL_ROWS_JS = r"""() => {
    const clip = s => (s || '').replace(/\s+/g, ' ').trim();
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
    const byTop = new Map();
    for (const lf of leaves) {
        if (!byTop.has(lf.top)) byTop.set(lf.top, []);
        byTop.get(lf.top).push(lf);
    }
    const out = [];
    for (const top of [...byTop.keys()].sort((a, b) => a - b)) {
        out.push(byTop.get(top).sort((a, b) => a.left - b.left).map(c => c.txt));
    }
    return out;
}"""

_INT_RE = re.compile(r"^-?[\d,]+$")


def _visual_rows(session) -> list[list[str]]:
    """グリッドを視覚行（セルの左→右配列）の並びに復元して返す。"""
    return session.page.evaluate(_VISUAL_ROWS_JS)


def _row_ints(cells: list[str]) -> list[int]:
    """行のセルから整数（カンマ・符号可、％や小数は除く）だけを左→右で拾う。"""
    out: list[int] = []
    for c in cells:
        if _INT_RE.match(c) and any(ch.isdigit() for ch in c):
            out.append(int(c.replace(",", "")))
    return out


def _extract_month_grid(session) -> list[dict]:
    """月別日別売上推移のグリッドを視覚行に復元して返す。

    各要素は {"period": "YYYY-MM", "ints": [整数のみ左→右]}。
    期間セル（YYYY年MM月）を含む視覚行だけを対象にするので、
    ヘッダ・メニュー・合計行（期間を持たない）は自然に除外される。
    """
    rows = _visual_rows(session)
    period_re = re.compile(r"(20\d{2})年\s*(\d{1,2})月")
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
        ints = _row_ints(cells)
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


# ── 時間帯別売上（販売管理→店舗業務）からの時間帯プロファイル取り込み ──────
#
# 画面（URL 末尾 jknburpr）は EJS TreeGrid で、選んだ期間（日付 from〜to）を
# 時間帯別に集計する。列: 時間帯 組数 客数 売上 構成比 組単価 客単価 坪売上
#   回転率 人時売上 人時生産性。既定は当日（データなし）なので、対象月の
# 1日〜末日を日付に入れて検索する。時間帯ラベルの後ろの整数を左→右で拾うと
# [組数, 客数, 売上, 組単価, 客単価, 坪売上, 人時売上, 人時生産性]（％・小数は除く）。
# 客数=ints[1], 売上=ints[2]。月の代表日（1日）に grain=hour で焼く。
HOURLY_MENU = ("販売管理", "店舗業務", "時間帯別売上")

_HOUR_SALES = 2  # 時間帯ラベル後の整数列での売上位置
_HOUR_COVERS = 1  # 同・客数位置


def _open_hourly(session) -> None:
    _open_menu(session, HOURLY_MENU)


def _set_date_range(session, d_from: str, d_to: str) -> bool:
    """画面の日付レンジ（from|to、YYYY/MM/DD）を設定する。best-effort。"""
    return bool(
        session.page.evaluate(
            r"""([f, t]) => {
        const set = (el, v) => {
            el.focus(); el.value = v;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new KeyboardEvent('keyup', {key: ' ', bubbles: true}));
        };
        const re = /^\d{4}\/\d{1,2}\/\d{1,2}$/;
        const ins = [...document.querySelectorAll('input')]
            .filter(i => i.offsetParent && re.test((i.value || '').trim()));
        if (ins.length >= 2) { set(ins[0], f); set(ins[1], t); return true; }
        if (ins.length === 1) { set(ins[0], f); return true; }
        return false;
    }""",
            [d_from, d_to],
        )
    )


def _extract_hour_grid(session) -> list[dict]:
    """時間帯別売上のグリッドを視覚行に復元し、時間帯行だけ返す。

    各要素は {"hour": 0-23, "ints": [ラベルを除く整数を左→右]}。
    時間帯ラベルは各行の先頭セルにある裸の数字（"10"=10時。"10:00"/"10時"も可）。
    先頭セルが 0〜23 の数字で、続くセルに整数が3つ以上ある行だけを1帯として採る。
    見出し（時間帯/組数…）・曜日選択・合計などは先頭セルが数字でないため除外される。
    """
    head_re = re.compile(r"^(\d{1,2})(?:\s*[:：時].*)?$")
    result: list[dict] = []
    seen: set[int] = set()
    for cells in _visual_rows(session):
        if not cells:
            continue
        m = head_re.match(cells[0].strip())
        if not m:
            continue
        hour = int(m.group(1))
        if not (0 <= hour <= 23) or hour in seen:
            continue
        # ラベル（先頭セル）を除いた整数列。[組数, 客数, 売上, 組単価, 客単価, …]
        ints = _row_ints(cells[1:])
        if len(ints) >= 3:
            seen.add(hour)
            result.append({"hour": hour, "ints": ints})
    return result


def _month_bounds(period: str) -> tuple[str, str]:
    """"YYYY-MM" → ("YYYY/MM/01", "YYYY/MM/末日")。"""
    import calendar

    y, m = int(period[:4]), int(period[5:7])
    last = calendar.monthrange(y, m)[1]
    return f"{y}/{m:02d}/01", f"{y}/{m:02d}/{last:02d}"


def ingest_hourly(
    warehouse,
    master,
    *,
    artifacts: Path,
    month: str | None = None,
    store_limit: int | None = None,
    dry_run: bool = False,
) -> int:
    """時間帯別売上から各店の「時間帯×売上・客数」を取り込む（対象月の集計）。

    対象月（既定は前月）の1日〜末日を日付レンジに入れて検索し、時間帯別の
    売上・客数を grain=hour で、その月の1日を代表日として焼く。
    """
    from datetime import date as _date
    from datetime import datetime, timezone

    from ..model import (
        GRAIN_HOUR,
        KIND_FINAL,
        METRIC_COVERS,
        METRIC_SALES,
        ActualRow,
    )
    from .fw_budget import _click_search, _combo_options, _select_combo

    # 対象月（既定は前月）。今月は締め前で時間帯データが薄いため。
    if not month:
        today = datetime.now(timezone.utc)
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        month = f"{y}-{m:02d}"
    d_from, d_to = _month_bounds(month)
    rep_date = _date(int(month[:4]), int(month[5:7]), 1)

    source = "fw_hourly"
    ingested_at = datetime.now(timezone.utc)
    active_by_code = {s.store_code: s for s in master.active}
    collected: list[ActualRow] = []

    with fw_session(artifacts) as session:
        _open_hourly(session)
        options = _combo_options(session)
        print(f"[時間帯別] 店舗コンボボックス {len(options)}件 / 対象月 {month}（{d_from}〜{d_to}）")
        targets = []
        for opt in options:
            code = opt["value"].lstrip("0")
            store = active_by_code.get(code) or master.find_by_name(opt["name"])
            if store and store.active:
                targets.append((opt["value"], store))
        print(f"[時間帯別] マスタと一致した稼働店 {len(targets)}件")
        if store_limit:
            targets = targets[:store_limit]

        for ti, (value, store) in enumerate(targets):
            name = store.store_name
            if not _select_combo(session, value):
                print(f"[時間帯別] 店舗選択に失敗: {name} ({value})")
                continue
            if not _set_date_range(session, d_from, d_to):
                print(f"[時間帯別] 日付レンジ設定に失敗: {name}")
            _click_search(session)
            time.sleep(1.2)
            grid = _extract_hour_grid(session)
            if ti == 0:
                # 最初の1店は生の視覚行も出して、ラベル・列の並びを確認できるようにする
                print("[時間帯別] 先頭店の視覚行（先頭12行・診断用）:")
                for cells in _visual_rows(session)[:12]:
                    print("   ", " | ".join(cells[:14]))
            if not grid:
                session.snapshot(f"nohour_{store.store_code}")
                print(f"  {store.store_code} {name[:14]} 時間帯グリッドが読めませんでした")
                continue
            n_before = len(collected)
            for band in grid:
                ints = band["ints"]
                if len(ints) <= _HOUR_SALES:
                    continue
                sales = ints[_HOUR_SALES]
                covers = ints[_HOUR_COVERS]
                for metric, val in ((METRIC_SALES, sales), (METRIC_COVERS, covers)):
                    if val > 0:
                        collected.append(
                            ActualRow(
                                store_code=store.store_code,
                                date=rep_date,
                                grain=GRAIN_HOUR,
                                metric=metric,
                                value=float(val),
                                hour=band["hour"],
                                kind=KIND_FINAL,
                                source=source,
                                ingested_at=ingested_at,
                            )
                        )
            hours = sorted({b["hour"] for b in grid})
            hspan = f"{hours[0]}〜{hours[-1]}時" if hours else "-"
            print(
                f"  {store.store_code} {name[:14]} {len(grid)}帯 {hspan} +{len(collected) - n_before}行"
            )

    print(f"[時間帯別] 収集 {len(collected)} 行")
    if dry_run:
        print("[時間帯別] dry-run のため書き込みはしません")
        return 0
    warehouse.ensure_schema()
    loaded = warehouse.replace_actuals(collected)
    print(f"[時間帯別] warehouse へ {loaded} 件 書き込みました")
    return 0


# ── ABC分析（販売管理→店舗業務）からの商品別売上取り込み ──────────────────
#
# 画面（URL 末尾 abc…）は EJS TreeGrid で、選んだ期間を商品別に集計する。
# 列: 商品CD 商品名 販売単価 原価 原価率 販売数量 売上金額 原価金額 粗利金額
#     売上構成比 累計構成比 粗利貢献率 ランク(A/B/C)。時間帯別と同じく日付レンジ駆動。
# 商品名（最初の非数字セル）を起点に、続く整数列 [販売単価,原価,販売数量,売上金額,…]
# から 販売数量=ints[2], 売上金額=ints[3] を拾う。行末の A/B/C をランクとする。
# おすすめ料理・売れ筋の把握に使う。全商品は多いので売上上位だけ取り込む。
ABC_MENU = ("販売管理", "店舗業務", "ABC分析")

_ABC_QTY = 2  # 商品名の後ろの整数列での販売数量位置
_ABC_SALES = 3  # 同・売上金額位置
_ABC_TOP_N = 30  # 1店あたり取り込む売上上位の商品数


def _open_abc(session) -> None:
    _open_menu(session, ABC_MENU)


def _extract_product_grid(session) -> list[dict]:
    """ABC分析のグリッドを視覚行に復元し、商品行だけ返す。

    各要素は {"name": 商品名, "rank": "A"/"B"/"C"/None, "ints": [名前より後ろの整数]}。
    商品名（先頭の非数字・非ランクの文字セル）を起点にし、続く整数が4つ以上ある行を採る。
    見出し・合計・データなしは自然に除外される。
    """
    rank_re = re.compile(r"^[ABC]$")
    result: list[dict] = []
    seen: set[str] = set()
    for cells in _visual_rows(session):
        name_idx = None
        for i, c in enumerate(cells):
            s = c.strip()
            if not s:
                continue
            if _INT_RE.match(s):  # 数字（商品CD・値）はスキップ
                continue
            if rank_re.match(s):  # 単独の A/B/C はランク
                continue
            if len(s) >= 2:  # 商品名らしい文字列
                name_idx = i
                break
        if name_idx is None:
            continue
        name = cells[name_idx].strip()
        if name in seen or name in ("商品名",):
            continue
        ints = _row_ints(cells[name_idx + 1:])
        if len(ints) < 4:
            continue
        rank = None
        for c in reversed(cells):
            if rank_re.match(c.strip()):
                rank = c.strip()
                break
        seen.add(name)
        result.append({"name": name, "rank": rank, "ints": ints})
    return result


def ingest_abc(
    warehouse,
    master,
    *,
    artifacts: Path,
    month: str | None = None,
    store_limit: int | None = None,
    top_n: int = _ABC_TOP_N,
    dry_run: bool = False,
) -> int:
    """ABC分析から各店の商品別売上（上位）を取り込む（対象月の集計）。

    対象月（既定は前月）の1日〜末日を日付レンジに入れて検索し、売上上位の商品を
    metric=product_sales / grain=month（その月の1日）で焼く。ランクは product_category に持つ。
    """
    from datetime import date as _date
    from datetime import datetime, timezone

    from ..model import (
        GRAIN_MONTH,
        KIND_FINAL,
        METRIC_PRODUCT_SALES,
        ActualRow,
    )
    from .fw_budget import _click_search, _combo_options, _select_combo

    if not month:
        today = datetime.now(timezone.utc)
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        month = f"{y}-{m:02d}"
    d_from, d_to = _month_bounds(month)
    rep_date = _date(int(month[:4]), int(month[5:7]), 1)

    source = "fw_abc"
    ingested_at = datetime.now(timezone.utc)
    active_by_code = {s.store_code: s for s in master.active}
    collected: list[ActualRow] = []

    with fw_session(artifacts) as session:
        _open_abc(session)
        options = _combo_options(session)
        print(f"[ABC] 店舗コンボボックス {len(options)}件 / 対象月 {month}（{d_from}〜{d_to}）上位{top_n}品")
        if len(options) < 20:
            # 店舗コンボが少ない＝別セレクタの可能性。実体を診断出力する。
            print(f"[ABC] 候補（診断）: {[(o['value'], o['name'][:14]) for o in options[:12]]}")
            combos = session.page.evaluate(
                r"""() => {
                const out = [];
                for (const w of document.querySelectorAll(
                        'store-combo-box, app-combobox, .combobox, ng-select, select')) {
                    if (!w.offsetParent && w.tagName !== 'SELECT') continue;
                    const opts = w.querySelectorAll('li.option, option, .ng-option');
                    out.push({
                        tag: w.tagName.toLowerCase(),
                        cls: (w.className || '').toString().slice(0, 40),
                        opts: opts.length,
                        sample: [...opts].slice(0, 3).map(o =>
                            (o.getAttribute && o.getAttribute('title')) || o.textContent.trim().slice(0, 12)),
                    });
                }
                return out;
            }"""
            )
            print(f"[ABC] コンボ系要素（診断）: {combos}")
            session.dump_clickables("abc_selector")
        targets = []
        for opt in options:
            code = opt["value"].lstrip("0")
            store = active_by_code.get(code) or master.find_by_name(opt["name"])
            if store and store.active:
                targets.append((opt["value"], store))
        print(f"[ABC] マスタと一致した稼働店 {len(targets)}件")
        if store_limit:
            targets = targets[:store_limit]

        for ti, (value, store) in enumerate(targets):
            name = store.store_name
            if not _select_combo(session, value):
                print(f"[ABC] 店舗選択に失敗: {name} ({value})")
                continue
            if not _set_date_range(session, d_from, d_to):
                print(f"[ABC] 日付レンジ設定に失敗: {name}")
            _click_search(session)
            time.sleep(1.2)
            products = _extract_product_grid(session)
            if ti == 0:
                print("[ABC] 先頭店の視覚行（先頭14行・診断用）:")
                for cells in _visual_rows(session)[:14]:
                    print("   ", " | ".join(cells[:14]))
            if not products:
                session.snapshot(f"noabc_{store.store_code}")
                print(f"  {store.store_code} {name[:14]} 商品グリッドが読めませんでした")
                continue
            # 売上上位だけに絞る（画面はランク順だが念のため売上で並べ直す）
            products.sort(key=lambda p: p["ints"][_ABC_SALES] if len(p["ints"]) > _ABC_SALES else 0, reverse=True)
            n_before = len(collected)
            for prod in products[:top_n]:
                ints = prod["ints"]
                if len(ints) <= _ABC_SALES:
                    continue
                sales = ints[_ABC_SALES]
                if sales <= 0:
                    continue
                collected.append(
                    ActualRow(
                        store_code=store.store_code,
                        date=rep_date,
                        grain=GRAIN_MONTH,
                        metric=METRIC_PRODUCT_SALES,
                        value=float(sales),
                        product_name=prod["name"][:80],
                        product_category=prod["rank"],
                        kind=KIND_FINAL,
                        source=source,
                        ingested_at=ingested_at,
                    )
                )
            top = products[0]
            print(
                f"  {store.store_code} {name[:14]} {len(products)}品 "
                f"（1位 {top['name'][:16]} 売上 {top['ints'][_ABC_SALES]:,}）"
                f" +{len(collected) - n_before}行"
            )

    print(f"[ABC] 収集 {len(collected)} 行")
    if dry_run:
        print("[ABC] dry-run のため書き込みはしません")
        return 0
    warehouse.ensure_schema()
    loaded = warehouse.replace_actuals(collected)
    print(f"[ABC] warehouse へ {loaded} 件 書き込みました")
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
