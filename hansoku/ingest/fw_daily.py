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

# 実グリッド（商品CD|商品名|販売単価|原価|原価率|販売数量|売上金額|原価金額|…|ランク）で
# 確認: 商品名の後ろの「整数のみ」列は [単価0, 数量1, 売上2, 原価金額3, 粗利4]。
# 原価は 100.00・原価率は 25.25% で整数判定から外れるため、この並びで安定する。
_ABC_QTY = 1  # 商品名の後ろの整数列での販売数量位置
_ABC_SALES = 2  # 同・売上金額位置
# 合計・総計・小計は商品ではないので商品グリッドから除外する
_ABC_TOTAL_NAMES = {"合計", "総計", "小計", "合 計", "総 計", "小 計", "総合計"}
_ABC_TOP_N = 40  # 取り込む売上上位の商品数
# 店舗選択で追加する上限（稼働店は約24）。全体は 60 秒の予算と 9 秒の既定タイムアウトで
# 抑えるので、重い日は途中まででも打ち切って集計に進む。
_ABC_MAX_STORES = 24
# 全店（グループ全体）の売れ筋を入れる擬似店舗コード。実店舗と混ざらない。
ABC_GROUP_CODE = "_group"


def _open_abc(session) -> None:
    _open_menu(session, ABC_MENU)


# ABC分析の期間は「日付プリセット」コンボ（datefield-combo）が駆動する。
# 値: 0前日 1先週 2今週 3直近7日 4直近30日 5月初から今日 6先月。
_ABC_PRESET_LASTMONTH = "6"  # 先月


def _select_date_preset(session, value: str) -> bool:
    """ABC分析の日付プリセット（datefield-combo）で value を選ぶ。"""
    page = session.page
    page.evaluate(
        """() => {
        const w = document.querySelector('app-combobox.datefield-combo');
        const btn = w && w.querySelector('.dropdown-btn');
        if (btn) btn.click();
    }"""
    )
    time.sleep(0.4)
    ok = page.evaluate(
        """(v) => {
        const w = document.querySelector('app-combobox.datefield-combo');
        const li = w && w.querySelector(`li.option[value="${v}"]`);
        if (li) { li.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); return true; }
        return false;
    }""",
        value,
    )
    time.sleep(0.4)
    return bool(ok)


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _abc_button_click(page, *labels: str) -> str | None:
    """page 上の可視ボタン/リンクのうち labels を含む最初を、Playwrightの実クリックで押す。"""
    for lab in labels:
        try:
            loc = page.get_by_role("button", name=re.compile(lab))
            if loc.count() == 0:
                loc = page.locator(
                    f"button:has-text('{lab}'), a:has-text('{lab}'), "
                    f"input[type=button][value*='{lab}'], input[type=submit][value*='{lab}']"
                )
            if loc.count() > 0:
                loc.first.click(timeout=3000, force=True)
                return lab
        except Exception:
            continue
    return None


def _abc_modal_present(page) -> bool:
    """店舗選択の画面が出ているか（「決定する」ボタンの有無で判定）。"""
    try:
        return bool(
            page.evaluate(
                r"""() => [...document.querySelectorAll('button,a,input[type=button],input[type=submit]')]
              .some(b=>b.offsetParent && /決定する/.test((b.innerText||b.value||'')))"""
            )
        )
    except Exception:
        return False


def _abc_modal_dump(page) -> None:
    """店舗選択モーダルの内部構造（入力・セレクト・左リストの行）を吸い出す診断。"""
    try:
        info = page.evaluate(
            r"""() => {
            const clip=s=>(s||'').replace(/\s+/g,' ').trim();
            const vis=e=>e && e.offsetParent!==null;
            const out={inputs:[],selects:[],lists:[],rows:[]};
            // 可視の input（type/value/placeholder/近傍ラベル）
            for(const i of document.querySelectorAll('input')){
              if(!vis(i)) continue;
              let lab='';
              let n=i;
              for(let k=0;k<4&&n;k++){ n=n.parentElement; if(!n)break;
                const t=clip(n.innerText); if(t){lab=t.slice(0,30);break;} }
              out.inputs.push([i.type||'', clip(i.value).slice(0,20), i.placeholder||'', lab]);
              if(out.inputs.length>=20) break;
            }
            // 可視の select（options 数と先頭のいくつか）
            for(const s of document.querySelectorAll('select')){
              if(!vis(s)) continue;
              const opts=[...s.options].map(o=>clip(o.text).slice(0,14)).slice(0,6);
              out.selects.push([s.options.length, opts]);
              if(out.selects.length>=12) break;
            }
            // 「表示数」を含む要素を起点に、左リストのコンテナ構造を探す
            const anchor=[...document.querySelectorAll('*')].find(e=>e.children.length===0 && /表示数/.test(clip(e.innerText)) && vis(e));
            if(anchor){
              let n=anchor;
              for(let k=0;k<6&&n;k++){ n=n.parentElement; if(!n)break;
                // このコンテナ内で「行っぽい」子（クリック可能な葉）を数える
                const leaves=[...n.querySelectorAll('li,tr,div[role=option],div[class*=row],div[class*=item],option')].filter(vis);
                if(leaves.length>=5){
                  out.lists.push([n.tagName+'.'+(n.className||'').slice(0,30), leaves.length,
                    leaves[0].tagName+'.'+(leaves[0].className||'').slice(0,40)]);
                  for(const r of leaves.slice(0,8)) out.rows.push(clip(r.innerText).slice(0,24));
                  break;
                }
              }
            }
            return out;
        }"""
        )
        print(f"[ABC][dump] inputs={info.get('inputs')}")
        print(f"[ABC][dump] selects={info.get('selects')}")
        print(f"[ABC][dump] lists={info.get('lists')}")
        print(f"[ABC][dump] rows={info.get('rows')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[ABC][dump] 失敗: {str(exc)[:80]}")


def _abc_left_option_names(page, limit: int = 3) -> list[str]:
    """左リスト（127店）の可視オプション名を先頭から limit 件返す。"""
    try:
        return page.evaluate(
            r"""(lim) => {
            const clip=s=>(s||'').replace(/\s+/g,' ').trim();
            const vis=e=>e && e.offsetParent!==null;
            const opts=[...document.querySelectorAll('option')].filter(vis)
                .map(o=>clip(o.textContent)).filter(t=>t.length>=2 && !/表示数|選択数/.test(t));
            return [...new Set(opts)].slice(0,lim);
        }""",
            limit,
        )
    except Exception:
        return []


def _abc_open_store_modal_and_select_all(session, master=None):
    """ABCの店舗選択（同一ページのモーダル）を開き、稼働店を選ぶ。

    window.open は「信頼されたユーザー操作」でしか開かないため、JSのdispatchでは
    駄目でPlaywrightの実クリックで押す。全店（＝127店ぜんぶ）はFWのABCでデータなしに
    なるので、左リストからマスタ上「稼働中」の店だけを実クリックで選び 追加、最後に
    決定する。選んだ店舗コードの一覧と、確定した本体ページを返す（失敗時 (None, [])）。
    """
    page = session.page
    ctx = page.context
    popup = None
    # 可視の「店舗選択」を探す（Angularは非表示のテンプレ複製も持つので可視のみ狙う）
    cand = page.get_by_text("店舗選択", exact=True)
    trigger = None
    for i in range(min(cand.count(), 10)):
        try:
            if cand.nth(i).is_visible():
                trigger = cand.nth(i)
                break
        except Exception:
            continue
    if trigger is None:
        vis = page.locator("*:text-is('店舗選択') >> visible=true")
        if vis.count() > 0:
            trigger = vis.first
    if trigger is None:
        print("[ABC] 可視の『店舗選択』が見つかりませんでした。保留。")
        return None, []
    # 実クリック（別窓が開くならそれを捕まえる）
    try:
        with ctx.expect_page(timeout=5000) as pinfo:
            trigger.click(timeout=4000)
        popup = pinfo.value
        popup.wait_for_load_state("domcontentloaded")
        print(f"[ABC] 別ウィンドウ捕捉: {popup.url}")
    except Exception as exc:  # noqa: BLE001 — 別窓でなく同一ページのモーダルかもしれない
        print(f"[ABC] 別窓は開かず（同一ページのモーダルを見る）: {str(exc)[:70]}")
    target = popup or page
    time.sleep(1.5)
    if not _abc_modal_present(target):
        print("[ABC] 店舗選択の画面（決定する）が出ませんでした。保留。")
        return None, []

    def counts():
        try:
            return target.evaluate(
                r"""() => { const clip=s=>(s||'').replace(/\s+/g,' ').trim();
                const out=[]; for(const el of document.querySelectorAll('*')){
                  if(el.children.length) continue; const t=clip(el.innerText);
                  if(/表示数|選択数|項目/.test(t)) out.push(t.slice(0,18)); }
                return [...new Set(out)].slice(0,10); }"""
            )
        except Exception:
            return []

    print("[ABC] 店舗選択の画面が出た。")
    print(f"[ABC] 開いた直後 counts={counts()}")
    # 左リスト（127店）から、マスタ上「稼働中」の店だけを実クリックで選び 追加。
    names = _abc_left_option_names(target, 300)
    # 稼働中に解決できる店だけを先に確定（重複コードは1回だけ）。
    matches: list[tuple[str, str]] = []
    seen_codes: set[str] = set()
    for nm in names:
        store = master.find_by_name(nm) if master is not None else None
        if store is None or not getattr(store, "active", False):
            continue
        if store.store_code in seen_codes:
            continue
        seen_codes.add(store.store_code)
        matches.append((nm, store.store_code))
    max_add = _ABC_MAX_STORES
    print(
        f"[ABC] 左リスト {len(names)}店 / 稼働解決 {len(matches)}店。"
        f"先頭{max_add}店まで実クリックで追加する。"
    )
    added_codes: list[str] = []
    added_names: list[str] = []
    skipped_active: list[str] = []
    budget_s = 60.0  # 選択全体の上限（超えたら打ち切って集計に進む）
    start = time.time()
    for nm, code in matches:
        if len(added_codes) >= max_add:
            break
        if time.time() - start > budget_s:
            print(f"[ABC] 選択が上限{budget_s:.0f}秒に達したので打ち切り（{len(added_codes)}店で集計へ）。")
            break
        t_i = time.time()
        loc = target.get_by_text(nm, exact=True)
        clicked = False
        try:
            n = min(loc.count(), 8)
        except Exception:
            n = 0
        for i in range(n):
            try:
                el = loc.nth(i)
                if el.is_visible():
                    el.click(timeout=1200)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            skipped_active.append(nm)
            continue
        _abc_button_click(target, "追加")  # ハイライトを右へ移す
        time.sleep(0.15)
        added_codes.append(code)
        added_names.append(nm)
        print(f"[ABC] +{nm}（{time.time() - t_i:.1f}s / 累計{time.time() - start:.0f}s）")
    print(f"[ABC] 稼働店 追加: {len(added_codes)}店 {added_names[:8]}{'…' if len(added_names) > 8 else ''}")
    if skipped_active:
        print(f"[ABC] 追加できなかった稼働候補: {skipped_active[:8]}")
    print(f"[ABC] 追加後 counts={counts()}")
    print(f"[ABC] 決定する: {_abc_button_click(target, '決定する')}")
    time.sleep(1.5)
    return page, added_codes


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
        if name in seen or name in ("商品名",) or name in _ABC_TOTAL_NAMES:
            continue
        ints = _row_ints(cells[name_idx + 1:])
        if len(ints) < 3:  # 少なくとも [単価,数量,売上] は要る
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
    from .fw_budget import _click_search

    if not month:
        today = datetime.now(timezone.utc)
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        month = f"{y}-{m:02d}"
    d_from, d_to = _month_bounds(month)
    rep_date = _date(int(month[:4]), int(month[5:7]), 1)

    # stdout を行バッファにして、各 print が実時刻でログに出るようにする
    # （既定のブロックバッファだと全部終了時にまとめて出て、どこで詰まったか分からない）。
    import sys as _sys

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    source = "fw_abc"
    ingested_at = datetime.now(timezone.utc)
    collected: list[ActualRow] = []

    # FWのABC分析は「全店」だと商品行が出ない（＝店舗選択が必須）。店舗選択モーダルで
    # マスタ上「稼働中」の店を実クリックで選び 追加→決定 すると、その集合の売れ筋商品が
    # 1グリッドに集計される。これをグループ全体の売れ筋（おすすめ料理の検討材料）として
    # 擬似店舗コード _group に焼く。1回の検索で済み、店舗別に回すより堅い。
    t0 = time.time()
    print("[ABC] fw_session を開く…")
    with fw_session(artifacts) as session:
        print(f"[ABC] ログイン完了 (+{time.time() - t0:.0f}s)。ABC分析メニューへ。")
        # FWが重い日でも各Playwright操作が既定30秒×多数で長引かないよう、既定の
        # 操作/ナビゲーションのタイムアウトを短めに固定する（明示timeout付きの
        # クリックはそのまま）。重い日は速く失敗させ、runを安く保つ。
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass
        _open_abc(session)
        print(f"[ABC] 稼働店ぶんを取り込む / 対象月 {month}（{d_from}〜{d_to}）上位{top_n}品 (+{time.time() - t0:.0f}s)")
        # 日付プリセット『先月』を先に。次に店舗選択モーダルで稼働店を選ぶ。
        preset_ok = _select_date_preset(session, _ABC_PRESET_LASTMONTH)
        _set_date_range(session, d_from, d_to)
        print(f"[ABC] 日付プリセット『先月』選択: {preset_ok} (+{time.time() - t0:.0f}s)")
        page_ret, picked_codes = _abc_open_store_modal_and_select_all(session, master)
        print(f"[ABC] 店舗選択おわり (+{time.time() - t0:.0f}s)")
        if page_ret is None:
            print("[ABC] 店舗選択に失敗。0件で無害終了。")
            picked_codes = []
        # 多数店の集計はFW側がぶれる（同条件でも出る時と『データなし』の時がある）。
        # 検索→最大40秒ポーリングを最大3回まで繰り返し、出るまで粘る。
        products: list[dict] = []
        for attempt in range(3):
            pressed = _abc_button_click(
                session.page, "検索", "検 索", "実行", "表示する", "表示", "再表示", "更新", "集計"
            )
            print(f"[ABC] 検索{attempt + 1}回目: {pressed} (+{time.time() - t0:.0f}s)")
            for i in range(20):  # 1回につき最大40秒
                time.sleep(2)
                products = _extract_product_grid(session)
                if products:
                    print(f"[ABC] グリッド充填を検出（{attempt + 1}回目・検索から約{(i + 1) * 2}秒）")
                    break
                if (i + 1) % 5 == 0:
                    print(f"[ABC] …グリッド待ち {(i + 1) * 2}秒（{attempt + 1}回目）")
            if products:
                break
            print(f"[ABC] {attempt + 1}回目はデータなし。再検索する。")
            time.sleep(3)
        print("[ABC] 視覚行（先頭14行・診断用）:")
        for cells in _visual_rows(session)[:14]:
            print("   ", " | ".join(cells[:14]))
        if not products:
            session.snapshot("noabc_group")
            print(f"[ABC] データなし（選択 {len(picked_codes)}店）。スナップショットを保存。")
        else:
            products.sort(
                key=lambda p: p["ints"][_ABC_SALES] if len(p["ints"]) > _ABC_SALES else 0,
                reverse=True,
            )
            for prod in products[:top_n]:
                ints = prod["ints"]
                if len(ints) <= _ABC_SALES:
                    continue
                sales = ints[_ABC_SALES]
                if sales <= 0:
                    continue
                collected.append(
                    ActualRow(
                        store_code=ABC_GROUP_CODE,
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
                f"[ABC] 稼働{len(picked_codes)}店 {len(products)}品 "
                f"（1位 {top['name'][:20]} 売上 {top['ints'][_ABC_SALES]:,}）"
                f" → {len(collected)}行"
            )

    print(f"[ABC] 収集 {len(collected)} 行")
    if dry_run:
        print("[ABC] dry-run のため書き込みはしません")
        return 0
    warehouse.ensure_schema()
    loaded = warehouse.replace_actuals(collected)
    print(f"[ABC] warehouse へ {loaded} 件 書き込みました")
    return 0


def _abc_click_radio(page, label: str) -> bool:
    """条件パネルのラジオ/ラベル（全商品・部門・グループ・メニュー等）を実クリックする。"""
    loc = page.get_by_text(label, exact=True)
    for i in range(min(loc.count(), 12)):
        try:
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=2000)
                return True
        except Exception:
            continue
    return False


def _abc_open_store_modal_and_select_one(session, store_name: str):
    """ABCの店舗選択モーダルを開き、store_name を含む1店だけを信頼クリックで選ぶ。"""
    page = session.page
    ctx = page.context
    cand = page.get_by_text("店舗選択", exact=True)
    trigger = None
    for i in range(min(cand.count(), 10)):
        try:
            if cand.nth(i).is_visible():
                trigger = cand.nth(i)
                break
        except Exception:
            continue
    if trigger is None:
        print("[ABCprobe] 可視の『店舗選択』が見つかりません")
        return None
    popup = None
    try:
        with ctx.expect_page(timeout=5000) as pinfo:
            trigger.click(timeout=4000)
        popup = pinfo.value
        popup.wait_for_load_state("domcontentloaded")
    except Exception:
        popup = None
    target = popup or page
    time.sleep(1.2)
    if not _abc_modal_present(target):
        print("[ABCprobe] 店舗選択モーダルが出ませんでした")
        return None
    names = _abc_left_option_names(target, 400)
    hit = next((n for n in names if store_name in n), None)
    if hit is None:
        print(f"[ABCprobe] 左リストに『{store_name}』一致なし。候補先頭: {names[:8]}")
        return None
    loc = target.get_by_text(hit, exact=True)
    clicked = False
    for i in range(min(loc.count(), 12)):
        try:
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=2000)
                clicked = True
                break
        except Exception:
            continue
    _abc_button_click(target, "追加")
    time.sleep(0.4)
    _abc_button_click(target, "決定する")
    time.sleep(1.2)
    print(f"[ABCprobe] 選択店: {hit}（clicked={clicked}）")
    return hit


def _abc_search_and_rows(session, tries: int = 2, waits: int = 20) -> list[list[str]]:
    """検索を押してグリッドが埋まるまで粘り、視覚行を返す（ぶれ対策のリトライ付き）。"""
    for _ in range(tries):
        _abc_button_click(session.page, "検索", "検 索", "実行", "表示する", "表示", "再表示", "更新")
        for _i in range(waits):
            time.sleep(2)
            if _extract_product_grid(session):
                return _visual_rows(session)
        # まだなら次のトライで再検索
    return _visual_rows(session)


def probe_abc_store(
    artifacts: Path,
    *,
    store: str,
    d_from: str,
    d_to: str,
    levels: tuple[str, ...] = ("全商品", "部門", "グループ", "メニュー"),
) -> int:
    """1店舗・任意期間で ABC を引き、各『分類』レベルの視覚行を吸い出す診断。

    d_from/d_to は YYYY/MM/DD。ランチ/日替わりランチが FW のどの分類・どの商品名で
    見えるかを1回で把握するために、分類ラジオを順に切り替えてグリッドを出す。
    """
    import sys as _sys

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    with fw_session(artifacts) as session:
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass
        _open_abc(session)
        print(f"[ABCprobe] 店舗={store} 期間={d_from}〜{d_to}")
        # 日付欄を確実に2つ出すため先月プリセット→任意レンジで上書き
        _select_date_preset(session, _ABC_PRESET_LASTMONTH)
        _set_date_range(session, d_from, d_to)
        hit = _abc_open_store_modal_and_select_one(session, store)
        if hit is None:
            print("[ABCprobe] 店舗選択に失敗。終了。")
            session.snapshot("abc_store_probe_nostore")
            return 0
        # 条件パネルの可視ラジオを列挙（分類の実体を掴む）
        radios = session.page.evaluate(
            r"""() => { const clip=s=>(s||'').replace(/\s+/g,' ').trim();
            const out=[]; for(const i of document.querySelectorAll('input[type=radio]')){
              if(!i.offsetParent) continue; let lab='';
              let n=i; for(let k=0;k<3&&n;k++){ n=n.parentElement; if(!n)break;
                const t=clip(n.innerText); if(t){lab=t.slice(0,24);break;} }
              out.push(clip(i.value)||lab); }
            return [...new Set(out)].slice(0,30); }"""
        )
        print(f"[ABCprobe] 条件ラジオ: {radios}")
        for level in levels:
            ok = _abc_click_radio(session.page, level)
            print(f"[ABCprobe] 分類ラジオ『{level}』クリック={ok}")
            rows = _abc_search_and_rows(session)
            print(f"[ABCprobe] === 分類={level} 視覚行（先頭60） ===")
            for cells in rows[:60]:
                print("   ", " | ".join(cells[:14]))
        session.snapshot("abc_store_probe")
    return 0


def _abc_item_qty(session, keyword: str) -> int:
    """現在のABCグリッドから、商品名に keyword を含む行の販売数量を返す（無ければ0）。"""
    for p in _extract_product_grid(session):
        if keyword in p["name"]:
            ints = p["ints"]
            return ints[_ABC_QTY] if len(ints) > _ABC_QTY else 0
    return 0


def analyze_lunch(
    artifacts: Path,
    *,
    store: str = "ぎふや 天満橋店",
    item: str = "冷やし鶏",
    month: str = "08",
    end_day: int = 25,
    detect_lo: int = 1,
    detect_hi: int = 25,
) -> int:
    """新ランチの効果を測る診断。1ログインで 開始日検出→直近→前年 を回す。

    開始日 = item（新商品）が最初に売れた日。2026年内の累積販売数量>0 の最小日を
    二分探索で特定。次に 部門分類で 直近(2026/月/開始日..end_day) と
    前年(2025/同日付) のグリッドを出し、ランチ部門・合計・新2品の行を印字する。
    数値の集計（1日平均・構成比・前年比）は印字結果から人手で組む。
    """
    import re as _re
    import sys as _sys

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    prev_month = f"{int(month) - 1:02d}" if int(month) > 1 else "12"

    def d(y: int, mm: str, dd: int) -> str:
        return f"{y}/{mm}/{dd:02d}"

    with fw_session(artifacts) as session:
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass
        _open_abc(session)
        _select_date_preset(session, _ABC_PRESET_LASTMONTH)
        hit = _abc_open_store_modal_and_select_one(session, store)
        print(f"[LUNCH] 店舗={hit}")
        if hit is None:
            session.snapshot("lunch_nostore")
            return 0
        _abc_click_radio(session.page, "部門")

        def item_qty_upto(dd: int) -> int:
            _set_date_range(session, d(2026, month, 1), d(2026, month, dd))
            _abc_search_and_rows(session)
            q = _abc_item_qty(session, item)
            print(f"[LUNCH] 2026/{month}/01..{dd:02d} 『{item}』数量={q}")
            return q

        start_day = None
        if item_qty_upto(detect_hi) <= 0:
            print(f"[LUNCH] 『{item}』は{month}月{detect_hi}日までに販売なし。開始日は検出できず。")
        else:
            lo, hi = detect_lo, detect_hi
            while lo < hi:
                mid = (lo + hi) // 2
                if item_qty_upto(mid) > 0:
                    hi = mid
                else:
                    lo = mid + 1
            start_day = lo
            print(f"[LUNCH] ★開始日 = 2026/{month}/{start_day:02d}")
        eff_start = start_day or 1
        days = end_day - eff_start + 1
        print(f"[LUNCH] 直近=2026/{month}/{eff_start:02d}..{end_day:02d}（{days}日）／前年=2025 同日付")

        def date_fields() -> list[str]:
            try:
                return session.page.evaluate(
                    r"""() => { const re=/^\d{4}\/\d{1,2}\/\d{1,2}$/;
                    return [...document.querySelectorAll('input')]
                      .filter(i=>i.offsetParent && re.test((i.value||'').trim()))
                      .map(i=>i.value.trim()); }"""
                )
            except Exception:
                return []

        kw = _re.compile("ランチ|合計|冷やし鶏|ミックスフライ")
        # 直近(新ランチ)＝2026/当月/開始日..end、前年＝2025/同日付、直前(旧ランチ)＝2026/前月/同日付
        periods = (
            (2026, month, "直近(新)"),
            (2025, month, "前年"),
            (2026, prev_month, "直前(旧)"),
        )
        want = f"{eff_start:02d}"
        for y, mm, label in periods:
            # 過去月はFWのABCが返ってこないことがある（月次実績には売上あり＝flaky）。
            # 日付を毎回入れ直して検索を最大4回、期間行が目的の年月を映すまで粘る。
            rows: list[list[str]] = []
            period_row, nodata, applied = "?", True, []
            for attempt in range(4):
                _set_date_range(session, d(y, mm, eff_start), d(y, mm, end_day))
                time.sleep(0.5)
                applied = date_fields()
                rows = _abc_search_and_rows(session, tries=1, waits=14)
                period_row = next(
                    (" | ".join(c[:6]) for c in rows if c and c[0].strip() == "期間"), "?"
                )
                nodata = any("データなし" in c for row in rows for c in row) or not _extract_product_grid(session)
                ok_period = f"{y}/{mm}/{want}" in period_row.replace(" ", "") or f"{y}/{int(mm)}/{eff_start}" in period_row.replace(" ", "")
                print(f"[LUNCH] {label} 試行{attempt + 1} 日付欄={applied} 期間={period_row} データなし={nodata}")
                if not nodata and ok_period:
                    break
                time.sleep(2)
            print(f"[LUNCH] ==== {label} {y}/{mm}/{eff_start:02d}..{end_day:02d} 部門グリッド（関連行）／データなし={nodata} ====")
            for cells in rows:
                line = " | ".join(cells[:14])
                if kw.search(line):
                    print("   ", line)
        session.snapshot("lunch_analyze")
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
