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
        print("[report] クリック要素テキスト一覧:")
        for it in items:
            t = " ".join((it.get("text") or "").split())
            if t:
                print("   -", t[:40])
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
    end_month: str | None = None,
    store_filter: str | None = None,
) -> int:
    """月別日別売上推移から各店の月次「売上・客数」を（前年ぶんも含めて）取り込む。

    画面は1店ずつ直近12ヶ月を表示。前年実績・客数前年実績の列を使い、
    前年の同月ぶんも同時に書くので、1スクレイプで約24ヶ月が揃う。
    end_month（YYYY-MM。カンマ区切りで複数可）を渡すと『対象月』を設定し、
    その月を末尾とする12ヶ月＋前年を引く（過去年のバックフィル用。冪等キーが
    月単位なので既存月と共存する）。store_filter（店コード or 店名の一部）を
    渡すと1店だけを対象にする＝1店=1ランで回せる。
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
    # None は「対象月を触らない＝直近12ヶ月」。複数指定すると1セッションで順に引く。
    anchors: list[str | None] = [m.strip() for m in (end_month or "").split(",") if m.strip()]
    if not anchors:
        anchors = [None]
    failures: list[str] = []
    btn_hint: list[int] = []       # 対象月の再照会に効いた『検 索』ボタン番号
    dead_anchors: set[str] = set()  # 2店続けて反映できなかった対象月は以降とばす
    miss_streak: dict[str, int] = {}

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
        if store_filter:
            key = store_filter.strip()
            targets = [
                (v, st)
                for v, st in targets
                if st.store_code == key.lstrip("0") or key in st.store_name
            ]
            if not targets:
                raise FWError(f"店舗が見つかりません: {store_filter}")
        if store_limit:
            targets = targets[:store_limit]
        print(f"[売上推移] 対象 {len(targets)}店 × 対象月 {[a or '直近' for a in anchors]}")

        for value, store in targets:
            name = store.store_name
            for anchor in anchors:
                if anchor and anchor in dead_anchors:
                    continue
                # 対象月は店を選び直すと戻ることがあるので、毎回 選択→検索→対象月 の順に。
                if not _select_combo(session, value):
                    print(f"[売上推移] 店舗選択に失敗: {name} ({value})")
                    failures.append(f"{store.store_code}/{anchor or '直近'}(選択失敗)")
                    continue
                _click_search(session)
                time.sleep(1.2)
                if anchor:
                    if not _apply_uriage_month(session, anchor, hint=btn_hint):
                        latest = _uriage_latest_month(session)
                        print(
                            f"  {store.store_code} {name[:14]} 対象月={anchor} を反映できず"
                            f"（画面の最新月={latest}）→ この月はとばします"
                        )
                        failures.append(f"{store.store_code}/{anchor}")
                        miss_streak[anchor] = miss_streak.get(anchor, 0) + 1
                        if miss_streak[anchor] >= 2:
                            dead_anchors.add(anchor)
                            print(f"[売上推移] 対象月={anchor} は画面が受け付けません。以降とばします")
                        continue
                    miss_streak[anchor] = 0
                grid = _extract_month_grid(session)
                if not grid:
                    session.snapshot(f"nogrid_{store.store_code}")
                    print(f"  {store.store_code} {name[:14]} グリッドが読めませんでした")
                    failures.append(f"{store.store_code}/{anchor or '直近'}(グリッド無)")
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
                    f"  {store.store_code} {name[:14]} [{anchor or '直近'}] {len(grid)}ヶ月 {span} "
                    f"（例 {sample['period']}: 売上 {sample['ints'][_INT_SALES]:,} / "
                    f"客数 {sample['ints'][_INT_COVERS]:,}） +{len(collected) - n_before}行"
                )

    if failures:
        print(f"[売上推移] 取れなかった 店/対象月: {failures}")
    print(f"[売上推移] 収集 {len(collected)} 行")
    if dry_run:
        print("[売上推移] dry-run のため書き込みはしません")
        return 0
    warehouse.ensure_schema()
    # 店を絞って流すことがあるので、消す範囲にも店を含める（他店を巻き添えにしない）。
    loaded = warehouse.replace_actuals(collected, scope_stores=True)
    print(f"[売上推移] warehouse へ {loaded} 件 書き込みました")
    return 0


def probe_uriage_suii(artifacts: Path, month: str = "2024-03") -> int:
    """月別日別売上推移に「過去期間セレクタ」があるかを調べる診断。
    1店を選んで既定グリッドの月レンジを見たあと、日付レンジ入力/年セレクトを
    列挙し、対象月(既定2024-03)へ移動を試みてグリッドが遡れるかを印字する。
    取得可否をこの1回で確定させる（DB書き込みはしない）。"""
    from .fw_budget import _click_search, _combo_options, _select_combo

    d_from, d_to = _month_bounds(month)
    with fw_session(artifacts) as session:
        _open_uriage_suii(session)
        options = _combo_options(session)
        print(f"[売上推移probe] 店舗コンボ {len(options)}件")
        if options:
            _select_combo(session, options[0]["value"])
            _click_search(session)
            time.sleep(1.2)
        before = [m["period"] for m in _extract_month_grid(session)]
        span_b = f"{before[-1]}〜{before[0]}" if before else "-"
        print(f"[売上推移probe] 既定グリッド {len(before)}ヶ月 span={span_b}")

        # 画面上の入力/セレクトを列挙（期間・年の手掛かりを探す）
        controls = session.page.evaluate(
            r"""() => {
                const out = {inputs: [], selects: []};
                for (const i of document.querySelectorAll('input')) {
                    if (!i.offsetParent) continue;
                    out.inputs.push({type: i.type||'', value: (i.value||'').slice(0,20),
                                     name: i.name||'', ph: i.placeholder||''});
                }
                for (const s of document.querySelectorAll('select')) {
                    if (!s.offsetParent) continue;
                    const opts = [...s.options].slice(0,20).map(o => (o.textContent||'').trim());
                    out.selects.push({name: s.name||'', opts});
                }
                return out;
            }"""
        )
        print(f"[売上推移probe] inputs={len(controls['inputs'])} selects={len(controls['selects'])}")
        for i in controls["inputs"][:20]:
            print(f"   input type={i['type']} value='{i['value']}' name='{i['name']}' ph='{i['ph']}'")
        for s in controls["selects"][:12]:
            print(f"   select name='{s['name']}' opts={s['opts']}")

        # 対象月フィールド(YYYY年MM月)を設定 → どのボタンで再照会されるかを総当りで特定。
        buttons = session.page.evaluate(
            r"""() => {
                const out = [];
                const nodes = document.querySelectorAll("button, input[type=button], input[type=submit], a");
                for (const b of nodes) {
                    if (!b.offsetParent) continue;
                    const t = (b.tagName === 'INPUT' ? (b.value||'') : (b.innerText||'')).replace(/\s+/g,' ').trim();
                    if (t) out.push(t.slice(0, 16));
                }
                return [...new Set(out)];
            }"""
        )
        print(f"[売上推移probe] ボタン {len(buttons)}件: {buttons[:30]}")
        target_year = month[:4]
        for label in ["検索", "検 索", "表示する", "表示", "再表示", "更新", "集計", "実行"]:
            set_ok = _set_uriage_month(session, month)
            pressed = session.page.evaluate(
                r"""(label) => {
                    const nodes = document.querySelectorAll("button, input[type=button], input[type=submit], a");
                    for (const b of nodes) {
                        if (!b.offsetParent) continue;
                        const t = (b.tagName === 'INPUT' ? (b.value||'') : (b.innerText||'')).replace(/\s+/g,' ').trim();
                        if (t === label) { b.click(); return true; }
                    }
                    return false;
                }""",
                label,
            )
            if not pressed:
                continue
            time.sleep(1.8)
            after = [m["period"] for m in _extract_month_grid(session)]
            span_a = f"{after[-1]}〜{after[0]}" if after else "-"
            reached = any(p.startswith(target_year) for p in after)
            print(f"[売上推移probe] set={set_ok} 押下『{label}』→ {len(after)}ヶ月 span={span_a} / {target_year}到達={reached}")
            if reached:
                print(f"[売上推移probe] ★『{label}』で対象月が効く（この手順でバックフィル可能）")
                break
        session.snapshot("uriage_probe")
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


def _set_uriage_month(session, month: str) -> bool:
    """月別日別売上推移の『対象月』入力を設定する（値は〈YYYY年MM月〉表示・placeholder=YYYYMM）。
    その月を末尾とする直近12ヶ月＋前年が引ける。best-effort。"""
    y, m = month[:4], month[5:7]
    kanji = f"{y}年{m}月"
    yyyymm = f"{y}{m}"
    return bool(
        session.page.evaluate(
            r"""([kanji, yyyymm]) => {
        const set = (el, v) => {
            el.focus(); el.value = v;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', bubbles: true}));
            el.blur();
        };
        const ins = [...document.querySelectorAll('input')].filter(i => i.offsetParent);
        let t = ins.find(i => /^\d{4}年\s*\d{1,2}月$/.test((i.value || '').trim()));
        const kanjiField = !!t;
        if (!t) t = ins.find(i => (i.placeholder || '').toUpperCase().includes('YYYYMM'));
        if (!t) return false;
        set(t, kanjiField ? kanji : yyyymm);
        return true;
    }""",
            [kanji, yyyymm],
        )
    )


def _click_uriage_search_nth(session, idx: int) -> bool:
    """『検 索』（表記ゆれでスペース混じり）ボタンの idx 番目を押す。

    画面には同じ字面のボタンが複数あることがあり、先頭が対象月の再照会とは
    限らない。押した結果グリッドが動いたかは呼び出し側で確かめる。
    """
    return bool(
        session.page.evaluate(
            r"""(idx) => {
        const nodes = document.querySelectorAll("button, input[type=button], input[type=submit], a");
        const hits = [];
        for (const b of nodes) {
            if (!b.offsetParent) continue;
            const t = (b.tagName === 'INPUT' ? (b.value || '') : (b.innerText || '')).replace(/\s+/g, '');
            if (t === '検索') hits.push(b);
        }
        if (idx >= hits.length) return false;
        hits[idx].click();
        return true;
    }""",
            idx,
        )
    )


def _click_uriage_search(session) -> bool:
    """月別日別売上推移の『検 索』ボタン（間にスペース有り）を押す。対象月の反映に必須。"""
    return _click_uriage_search_nth(session, 0)


def _uriage_latest_month(session) -> str | None:
    """いま描かれているグリッドの最新月（YYYY-MM）。再描画の判定に使う。"""
    months = [m["period"] for m in _extract_month_grid(session)]
    return max(months) if months else None


def _apply_uriage_month(
    session, month: str, *, attempts: int = 2, hint: list[int] | None = None
) -> bool:
    """『対象月』を入れて再照会し、グリッドの最新月がその月になるまで待つ。

    「その年の行があるか」では既定グリッド（直近12ヶ月）と見分けが付かない
    （例: 対象月2025-08 のとき既定にも2025年の行がある）。最新月そのものが
    一致することを反映の合図にする。ボタン候補を順に押し、実際にグリッドが
    動いたものだけを採用する（押下＝反映ではない）。hint に効いた番号を残すと
    2店目以降は一発で当たる。
    """
    order = list(range(4))
    if hint:
        idx0 = hint[0]
        if idx0 in order:
            order.remove(idx0)
            order.insert(0, idx0)
    for _ in range(attempts):
        if not _set_uriage_month(session, month):
            time.sleep(1.0)
            continue
        for idx in order:
            if not _click_uriage_search_nth(session, idx):
                continue
            for _ in range(6):
                time.sleep(1.0)
                if _uriage_latest_month(session) == month:
                    if hint is not None:
                        hint[:] = [idx]
                    return True
            # 効かないボタンだった。対象月が戻っている場合に備えて入れ直す。
            _set_uriage_month(session, month)
    return False


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


# 時間帯グリッドの整数列 [組数0, 客数1, 売上2, 組単価3, 客単価4, 坪売上5, …]。
_HOUR_SPP = 4  # 客単価の位置


def probe_hourly_store(
    artifacts: Path,
    *,
    store: str,
    ranges: list[tuple[str, str, str]],
) -> int:
    """1店舗・複数の任意期間で 時間帯別売上 を引き、時間帯×(客数・売上・客単価)を印字する診断。

    施策前後の比較（例: 電子タバコ許可・空調改善の前後で、時間帯別の集客／客単価が
    どう動いたか）に使う。ranges は (ラベル, YYYY/MM/DD_from, YYYY/MM/DD_to) の並び。
    1回のログインで店を選び、各レンジで日付を入れ直して検索→グリッドを読む。
    """
    import sys

    sys.stdout.reconfigure(line_buffering=True)
    from .fw_budget import _click_search, _combo_options, _select_combo

    with fw_session(artifacts) as session:
        _open_hourly(session)
        options = _combo_options(session)
        hit = next((o for o in options if store in o["name"]), None)
        if hit is None:
            cand = [o["name"][:18] for o in options[:14]]
            print(f"[時間帯probe] コンボに『{store}』一致なし。候補先頭: {cand}")
            return 1
        print(f"[時間帯probe] 対象店: {hit['name']}（value={hit['value']}）／{len(ranges)}レンジ")
        for label, d_from, d_to in ranges:
            if not _select_combo(session, hit["value"]):
                print(f"[時間帯probe] {label}: 店舗選択に失敗")
                continue
            if not _set_date_range(session, d_from, d_to):
                print(f"[時間帯probe] {label}: 日付設定に失敗 {d_from}〜{d_to}")
            _click_search(session)
            time.sleep(1.6)
            grid = _extract_hour_grid(session)
            if not grid:
                session.snapshot(f"nohour_probe_{label}")
                print(f"=== {label} {d_from}〜{d_to} === グリッドなし")
                continue
            tot_c = tot_s = 0
            lines = []
            for band in grid:
                ints = band["ints"]
                covers = ints[_HOUR_COVERS] if len(ints) > _HOUR_COVERS else 0
                sales = ints[_HOUR_SALES] if len(ints) > _HOUR_SALES else 0
                spp = ints[_HOUR_SPP] if len(ints) > _HOUR_SPP else (
                    round(sales / covers) if covers else 0
                )
                tot_c += covers
                tot_s += sales
                lines.append(f"  {band['hour']:>2}時  客数{covers:>5}  売上{sales:>8}  客単価{spp:>5}")
            avg = round(tot_s / tot_c) if tot_c else 0
            print(f"=== {label} {d_from}〜{d_to} ===（{len(grid)}帯）")
            for ln in lines:
                print(ln)
            print(f"  －－ 合計 客数{tot_c} 売上{tot_s} 客単価{avg}")
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


def probe_abc_dom(artifacts: Path, *, store: str) -> int:
    """1069/1137 で分類ラジオが切替らない原因を突き止めるDOM診断。

    ABCを開き1店選択したうえで、全 input[type=radio] の name/id/value/checked/
    可視/座標/ラベル文言を列挙し、『部門/グループ/全商品』を含む要素の outerHTML も出す。
    ラジオの実体・イベント結線が他店とどう違うかを見て、確実な切替方法を決める。
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
        _select_date_preset(session, _ABC_PRESET_LASTMONTH)
        d_from, d_to = _month_bounds("2026-07")
        _set_date_range(session, d_from, d_to)
        hit = _abc_open_store_modal_and_select_one(session, store)
        print(f"[ABC-DOM] 選択店: {hit}")
        radios = session.page.evaluate(
            r"""() => { const clip=s=>(s||'').replace(/\s+/g,' ').trim();
            const out=[];
            for(const i of document.querySelectorAll('input[type=radio]')){
              let lab='';
              if(i.id){const l=document.querySelector('label[for="'+CSS.escape(i.id)+'"]'); if(l) lab=clip(l.textContent);}
              if(!lab){let n=i.parentElement; for(let k=0;k<3&&n;k++){const t=clip(n.innerText); if(t){lab=t.slice(0,30);break;} n=n.parentElement;}}
              const r=i.getBoundingClientRect();
              out.push({name:i.name,id:i.id,value:i.value,checked:i.checked,vis:!!i.offsetParent,x:Math.round(r.x),y:Math.round(r.y),lab});
            } return out; }"""
        )
        print(f"[ABC-DOM] radios {len(radios)}件:")
        for r in radios[:50]:
            print("   ", r)
        html = session.page.evaluate(
            r"""() => { const clip=s=>(s||'').replace(/\s+/g,' ').trim();
            const want=new Set(['部門','グループ','全商品','メニュー']);
            const nodes=[...document.querySelectorAll('*')].filter(n=>{
              const own=[...n.childNodes].filter(c=>c.nodeType===3).map(c=>clip(c.textContent)).join('');
              return want.has(own);});
            return nodes.slice(0,8).map(n=>{const p=n.closest('label,td,li,div')||n; return p.outerHTML.replace(/\s+/g,' ').slice(0,500);}); }"""
        )
        for h in html:
            print("   [分類HTML]", h)
        session.snapshot("abc_dom_probe")
    return 0


def report_monthly_coverage(
    warehouse,
    master,
    *,
    date_from: str = "2024-01",
    date_to: str = "2026-08",
    metric: str | None = None,
) -> int:
    """指定指標（既定=月次売上）が店×月でどこまで埋まっているかを出す。

    「どの店のどの月が無いのか」を1回のDB照会で一覧にする。FWログイン不要。
    バックフィルの前後で回して、埋まったか・どこが穴かを確かめるための道具。
    metric に dept_sales / product_sales を渡すと、ABCの取りこぼし月を洗い出せる
    （FWのグリッドは稀に埋まりきる前に読まれ、その月だけ0件になることがある）。
    """
    import sys as _sys
    from datetime import date as _date

    from ..db.warehouse import AggregateQuery
    from ..model import GRAIN_MONTH, METRIC_DEPT_SALES, METRIC_SALES

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    def _months(a: str, b: str) -> list[str]:
        y, m = int(a[:4]), int(a[5:7])
        ey, em = int(b[:4]), int(b[5:7])
        out = []
        while (y, m) <= (ey, em):
            out.append(f"{y}-{m:02d}")
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return out

    metric = metric or METRIC_SALES
    want = _months(date_from, date_to)
    d_from = _date(int(date_from[:4]), int(date_from[5:7]), 1)
    last_y, last_m = int(date_to[:4]), int(date_to[5:7])
    d_to = _date(last_y + (last_m == 12), 1 if last_m == 12 else last_m + 1, 1)

    have: dict[str, set[str]] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=d_from,
            date_to=d_to,
            grain=GRAIN_MONTH,
            metrics=[metric],
            store_codes=master.active_codes,
            group_by=("store_code", "date"),
        )
    ):
        if not row["value"]:
            continue
        have.setdefault(row["store_code"], set()).add(row["date"].strftime("%Y-%m"))

    # 「そもそもFWでは取れない」店は取り漏れではないので、理由を添えて別扱いにする。
    # ①FW未連動（実績が別POSにある） ②FWのABCに部門が無い（商品と部門が未紐付け）。
    pos_label = {"uleji": "uレジ管理", "dainy": "ダイニー管理アプリ"}

    def _cannot(st) -> str | None:
        """この店がこの指標をFWから取れない理由。取れるなら None。"""
        pos = getattr(st, "pos", "fw")
        if pos != "fw":
            return f"FW未連動（実績は{pos_label.get(pos, pos)}から）"
        if metric == METRIC_DEPT_SALES and not getattr(st, "abc_dept", True):
            return "FWのABCに部門が無い（商品と部門が未紐付け）"
        return None

    print(f"=== 月次カバレッジ [{metric}] {date_from}〜{date_to}（{len(want)}ヶ月） ===")
    full, partial, empty, other_pos = [], [], [], []
    for st in master.active:
        got = have.get(st.store_code, set())
        miss = [m for m in want if m not in got]
        head = f"  {st.store_code} {st.store_name[:16]:<16} {len(want) - len(miss):>2}/{len(want)}"
        cannot = _cannot(st)
        if cannot and not got:
            other_pos.append(st.store_code)
            print(f"― {head}  {cannot}")
        elif not miss:
            full.append(st.store_code)
            print(f"✓ {head}  すべて有り")
        elif len(miss) == len(want):
            empty.append(st.store_code)
            print(f"✗ {head}  データ無し")
        else:
            partial.append(st.store_code)
            shown = ",".join(miss[:14]) + (" …" if len(miss) > 14 else "")
            print(f"△ {head}  欠け: {shown}")

    # 月ごとに「何店ぶん入っているか」も出す。穴が月側か店側かの切り分け用。
    print("--- 月別に埋まっている店数 ---")
    n_active = len(master.active)
    for m in want:
        n = sum(1 for st in master.active if m in have.get(st.store_code, set()))
        bar = "■" * round(n / max(n_active, 1) * 20)
        print(f"  {m}  {n:>2}/{n_active}  {bar}")
    print(
        f"=== 完備 {len(full)}店 / 欠けあり {len(partial)}店 / 皆無 {len(empty)}店"
        f" / FWでは取れない {len(other_pos)}店 ==="
    )
    if empty:
        print(f"データ皆無の店（取れるはずなのに0件＝要調査）: {empty}")
    if other_pos:
        print(f"FWでは取れない店（理由は上の ― 行）: {other_pos}")
    return 0


def report_abc_coverage(warehouse, master, month: str | None = None) -> int:
    """店舗別ABC取込のカバレッジ確認。各稼働店の 商品数・部門数・バケット別売上を印字。

    23店を1店ずつ焼いた結果を1回のDB照会でまとめて確認する（どの店が未取得か・
    部門分類が妥当かをログで見る）。FWログイン不要。month は使わず全期間から拾う。
    """
    import sys as _sys
    from datetime import date as _date

    from ..db.warehouse import AggregateQuery
    from ..model import (
        GRAIN_MONTH,
        METRIC_DEPT_SALES,
        METRIC_PRODUCT_SALES,
        dept_bucket,
    )

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    d_from, d_to = _date(2024, 1, 1), _date(2027, 12, 31)
    # 商品数（store×product）
    prod_n: dict[str, int] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
            metrics=[METRIC_PRODUCT_SALES], store_codes=master.active_codes,
            group_by=("store_code", "product_name"),
        )
    ):
        prod_n[row["store_code"]] = prod_n.get(row["store_code"], 0) + 1
    # 部門（store×dept）→ バケット別売上
    dept_bkt: dict[str, dict[str, float]] = {}
    dept_n: dict[str, int] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
            metrics=[METRIC_DEPT_SALES], store_codes=master.active_codes,
            group_by=("store_code", "product_name"),
        )
    ):
        code = row["store_code"]
        dept_n[code] = dept_n.get(code, 0) + 1
        b = dept_bkt.setdefault(code, {})
        bk = dept_bucket(row["product_name"])
        b[bk] = b.get(bk, 0.0) + row["value"]

    print("=== 店舗別ABC カバレッジ（2026-07 想定） ===")
    order = ("コース", "ランチ", "アラカルト", "飲み放題", "食べ放題")
    have, miss = 0, []
    for s in master.active:
        code = s.store_code
        if code == "1151":
            continue  # ナガグツは対象外
        p, dn = prod_n.get(code, 0), dept_n.get(code, 0)
        if p and dn:
            have += 1
            bk = dept_bkt.get(code, {})
            parts = [f"{k}{int(bk[k]):,}" for k in order if bk.get(k)]
            print(f"  ✓ {code} {s.store_name[:16]:<16} 商品{p:>3} 部門{dn:>3} | " + " / ".join(parts))
        else:
            miss.append(code)
            print(f"  ✗ {code} {s.store_name[:16]:<16} 商品{p:>3} 部門{dn:>3}  ← 未取得/不足")
    print(f"=== 取得済み {have}店 / 未取得 {len(miss)}店: {miss} ===")
    return 0


def report_abc_detail(
    warehouse,
    master,
    *,
    month: str,
    store_filter: str | None = None,
    top_n: int = 20,
    items: str | None = None,
) -> int:
    """指定月の 部門一覧と売れ筋商品を、店ごとにそのまま印字する。FWログイン不要。

    施策台帳の bucket（効く部門）と items（商品名キーワード）に何を書けばよいかを
    決めるための道具。前年同月を指定すれば、去年その施策が実際どの部門・どの商品で
    立っていたかが分かる（例: 忘新年会なら 2025-12）。

    items にカンマ区切りのキーワードを渡すと、一致した商品だけを全店ぶん並べる
    （部門は出さない）。「忘新年会コース」が各店で実際どういう商品名なのかを
    一度に見るための形。
    """
    import sys as _sys
    from datetime import date as _date

    from ..db.warehouse import AggregateQuery
    from ..model import (
        GRAIN_MONTH,
        METRIC_DEPT_SALES,
        METRIC_PRODUCT_SALES,
        dept_bucket,
    )

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    y, m = int(month[:4]), int(month[5:7])
    d_from = _date(y, m, 1)
    d_to = _date(y + (m == 12), 1 if m == 12 else m + 1, 1)

    codes = list(master.active_codes)
    # ワークフローの入力は空欄だと既定値（1店）に化けるので、「全店」で明示的に外せるようにする。
    if store_filter in ("all", "全店", "*"):
        store_filter = None
    if store_filter:
        want = {s.store_code for s in master.active if store_filter in (s.store_code, s.store_name)}
        if not want:
            want = {s.store_code for s in master.active if store_filter in s.store_name}
        if not want:
            print(f"[ABC明細] 店舗『{store_filter}』をマスタで解決できません。終了。")
            return 1
        codes = sorted(want)

    depts: dict[str, list[tuple[str, float]]] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
            metrics=[METRIC_DEPT_SALES], store_codes=codes,
            group_by=("store_code", "product_name"),
        )
    ):
        depts.setdefault(row["store_code"], []).append((row["product_name"], row["value"]))

    prods: dict[str, list[tuple[str, float]]] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
            metrics=[METRIC_PRODUCT_SALES], store_codes=codes,
            group_by=("store_code", "product_name"),
        )
    ):
        prods.setdefault(row["store_code"], []).append((row["product_name"], row["value"]))

    keywords = [k.strip() for k in (items or "").split(",") if k.strip()]
    if keywords:
        print(f"=== ABC明細 {month} 商品名一致 {keywords} ===")
        hit_any = False
        for st in master.active:
            if st.store_code not in codes:
                continue
            p_all = sorted(prods.get(st.store_code, []), key=lambda x: x[1], reverse=True)
            hits = [(n, v) for n, v in p_all if any(k in n for k in keywords)]
            if not hits:
                continue
            hit_any = True
            print(f"\n── {st.store_code} {st.store_name} ──")
            for name, v in hits:
                print(f"    {int(v):>10,}  {name}")
        if not hit_any:
            print("  一致する商品はありませんでした。")
        return 0

    print(f"=== ABC明細 {month} （部門と売れ筋上位{top_n}品） ===")
    for st in master.active:
        if st.store_code not in codes:
            continue
        d = sorted(depts.get(st.store_code, []), key=lambda x: x[1], reverse=True)
        p = sorted(prods.get(st.store_code, []), key=lambda x: x[1], reverse=True)
        if not d and not p:
            continue
        print(f"\n── {st.store_code} {st.store_name} ──")
        if d:
            total = sum(v for _, v in d) or 1.0
            print(f"  [部門] {len(d)}件 合計{int(total):,}")
            for name, v in d:
                print(f"    {v / total * 100:5.1f}%  {int(v):>10,}  {name}  → {dept_bucket(name)}")
        else:
            print("  [部門] なし")
        if p:
            print(f"  [商品] 上位{min(top_n, len(p))}／{len(p)}品")
            for name, v in p[:top_n]:
                print(f"    {int(v):>10,}  {name}")
    return 0


def _expand_months(spec: str) -> list[str]:
    """月の指定を展開する。"2025-12" / "2025-11,2025-12" / "2024-09..2026-07"。

    範囲（..）は両端を含む。バックフィルで23ヶ月などを1行で渡せるようにするため。
    """
    out: list[str] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ".." in chunk:
            a, b = (x.strip() for x in chunk.split("..", 1))
            y, m = int(a[:4]), int(a[5:7])
            ey, em = int(b[:4]), int(b[5:7])
            while (y, m) <= (ey, em):
                out.append(f"{y}-{m:02d}")
                m += 1
                if m == 13:
                    y, m = y + 1, 1
        else:
            out.append(chunk)
    # 重複は落として時系列に並べる（同じ月を二度焼かない）
    return sorted(set(out))


def ingest_abc_store(
    warehouse,
    master,
    *,
    artifacts: Path,
    store: str,
    month: str | None = None,
    top_n: int = _ABC_TOP_N,
    dry_run: bool = False,
    budget_minutes: float = 70.0,
) -> int:
    """1店舗の商品ABC（売れ筋・上位）＋部門内訳（ランチ/ドリンク等）を取り込む。

    店舗選択モーダルで1店だけを選び、分類=全商品で商品別売上の上位、分類=部門で
    部門別の数量・売上（原価率）を取得する。実店舗コードに焼くが、店ごとに
    source=fw_abc_<code> で分けるので、店を1つずつ流しても互いに上書きしない
    （replace_actuals の冪等キーは source×grain×date）。store は店コードでも店名でも可。

    month は複数指定できる（"2025-11,2025-12" / "2024-09..2026-07"）。FW ABC は
    過去月も引けるので、過去分をまとめて遡れる。1店1ログインで月を回すため、
    23ヶ月でもログインは1回で済む。

    書き込みは月ごとに行う。冪等キーが source×grain×date で source は店ごとに
    分かれているため、1ヶ月ぶんだけ差し替えても他の月・他の店に触らない。
    途中で落ちても、そこまでの月は残る（以前は最後にまとめて書いていたため、
    23ヶ月まわした挙句にジョブがタイムアウトすると全部消えていた）。
    budget_minutes を超えたら、残りの月を告げて打ち切る。取れた月は残るので、
    残りの月だけを指定して流し直せばよい。
    """
    import re as _re
    import sys as _sys
    from datetime import date as _date
    from datetime import datetime, timezone

    from ..model import (
        GRAIN_MONTH,
        KIND_FINAL,
        METRIC_DEPT_QTY,
        METRIC_DEPT_SALES,
        METRIC_PRODUCT_SALES,
        ActualRow,
        dept_bucket,
    )

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    if not month:
        today = datetime.now(timezone.utc)
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        month = f"{y}-{m:02d}"
    months = _expand_months(month)
    if not months:
        print("[ABC店] 対象月が空です。終了。")
        return 1

    # store（コード or 名前）→ 実店舗を解決。モーダルの左リストは店名で一致させる。
    st = None
    try:
        st = master.by_code(store)
    except Exception:  # noqa: BLE001
        st = master.find_by_name(store) or next(
            (s for s in master.active if store in s.store_name), None
        )
    if st is None:
        print(f"[ABC店] 店舗『{store}』をマスタで解決できません。終了。")
        return 1
    code, name = st.store_code, st.store_name
    source = f"fw_abc_{code}"
    ingested_at = datetime.now(timezone.utc)
    # 月ごとに書くので、collected は「その月ぶん」。書けた件数は total_loaded に積む。
    collected: list[ActualRow] = []
    total_loaded = 0
    written_months: list[str] = []

    def _flush(month_: str) -> None:
        """その月ぶんを warehouse に書く。冪等キーが source×grain×date で source は
        店ごとに分かれているため、1ヶ月だけ差し替えても他の月・他の店に触らない。

        指標も削除範囲に入れる（scope_metrics）。商品は取れたが部門が取れなかった月に、
        既に入っている前回の部門を消してしまわないため。取れなかった指標は行が0件なので
        削除の対象にも入らず、前の値がそのまま残る。"""
        nonlocal total_loaded
        if dry_run or not collected:
            return
        total_loaded += warehouse.replace_actuals(collected, scope_metrics=True)
        written_months.append(month_)

    hdr = _re.compile(r"^(.+?)\s\|\s(\d+\.\d+)%\s\|\s([\d,]+)\s\|\s([\d,]+)")
    num = lambda s: int(s.replace(",", ""))  # noqa: E731

    def _clean_dept(cells: list[str]):
        m = hdr.match(" | ".join(c for c in cells[:6] if c is not None))
        if not m:
            return None
        dn = m.group(1).strip()
        # 全商品行（"商品CD | 商品名 | 単価 | 原価"）は名前に '|' を含む→部門ではない
        return m if "|" not in dn else None

    def _countable_dept(cells: list[str]) -> bool:
        """後段で実際に数える部門行かどうか。グリッドの受け入れ判定に使う。

        「部門らしい行が1つでもあるか」で受け入れると、合計行しか出ていない
        グリッドを『取れた』と見なして部門0件のまま抜けてしまう（1111/1151/1168 で
        再試行のログすら出ずに0件になっていた）。数えられる行が出るまで粘る。"""
        m = _clean_dept(cells)
        if not m:
            return False
        dname = m.group(1).strip()
        if dname in _ABC_TOTAL_NAMES or dname in ("部門", "部門名", "分類"):
            return False
        return num(m.group(3)) > 0 or num(m.group(4)) > 0

    t0 = time.time()
    print(f"[ABC店] {code} {name} / 対象月 {len(months)}件 {months[0]}〜{months[-1]} 上位{top_n}品")
    # 前月と部門合計が完全一致したら、日付が反映されず同じグリッドを読んだ疑いが濃い。
    # （〜2千万円の合計が偶然そろうことは無い）。売上推移で同種の取りこぼしをやったので、
    # 「取れたつもり」を検知できるようにしておく。
    seen_totals: dict[int, str] = {}
    suspect: list[str] = []
    empty: list[str] = []
    # 新しい月から古い月へ降順で回す。データのある月に先に当たるので、
    # 「一度データが出た後に空月が続く＝開店前」と判断して打ち切れる。
    months = sorted(months, reverse=True)
    seen_any = False
    empty_streak = 0
    # 部門が取れるはずの店で、商品は取れたのに部門が0件だった月。読み取り失敗の疑い。
    no_dept: list[str] = []

    with fw_session(artifacts) as session:
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass
        _open_abc(session)
        # 日付欄を2つ出すために先月プリセットを当ててから、対象月のレンジを入れる。
        # 店舗選択より「先に」日付を入れること。順序を逆にすると、店によっては
        # グリッドが全月0品で返る（1137・1728以降の新しい店で確認。同じ店・同じ月でも
        # probe の順＝日付→店舗選択 なら取れる）。
        _select_date_preset(session, _ABC_PRESET_LASTMONTH)
        _set_date_range(session, *_month_bounds(months[0]))
        hit = _abc_open_store_modal_and_select_one(session, name)
        if hit is None:
            print(f"[ABC店] 店舗選択に失敗（{name}）。スナップショットを保存し 0件終了。")
            session.snapshot(f"abc_store_ingest_nostore_{code}")
            return 0

        if not dry_run:
            warehouse.ensure_schema()
        deadline = t0 + budget_minutes * 60
        skipped: list[str] = []
        for i, month in enumerate(months):
            if time.time() > deadline:
                skipped = months[i:]
                print(
                    f"[ABC店] {code} 時間切れ（{budget_minutes:.0f}分）。"
                    f"残り{len(skipped)}ヶ月は取らずに終わります: {skipped[0]}〜{skipped[-1]}"
                )
                break
            collected.clear()
            d_from, d_to = _month_bounds(month)
            rep_date = _date(int(month[:4]), int(month[5:7]), 1)
            _set_date_range(session, d_from, d_to)  # _month_bounds は既に YYYY/MM/DD

            # --- 分類=全商品：売れ筋 上位 ---
            _abc_click_radio(session.page, "全商品")
            _abc_search_and_rows(session)  # グリッド充填まで粘る
            products = _extract_product_grid(session)
            products.sort(
                key=lambda p: p["ints"][_ABC_SALES] if len(p["ints"]) > _ABC_SALES else 0,
                reverse=True,
            )
            n_prod = 0
            for prod in products[:top_n]:
                ints = prod["ints"]
                if len(ints) <= _ABC_SALES:
                    continue
                sales = ints[_ABC_SALES]
                if sales <= 0:
                    continue
                collected.append(
                    ActualRow(
                        store_code=code,
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
                n_prod += 1
            top = products[0]["name"][:16] if products else "-"

            # --- 分類=部門：ランチ/ドリンク等の内訳（数量・売上・原価率） ---
            # 部門グリッドは負荷時に埋まりきらず0件になる／部門ラジオに切替らず全商品の
            # ままになることがある（1069/1137）。「きれいな部門行」が出るまで
            # 先頭へスクロール→部門クリック→検索を粘る。
            # まず分類=部門で粘り、それでも切替らない店（1069/1137）は 分類=グループ
            # （フード/ドリンク/コース…の粗い区分・最も安定して出る）へフォールバックする。
            drows: list[list[str]] = []
            used_level = "部門"
            # FW側で商品に部門が紐付いていない店（stores.yaml の abc_dept: false）は
            # 何度切り替えてもきれいな部門行が出ない。1ヶ月あたり2区分×3回の粘りを
            # 空振りし続けるだけなので、最初から飛ばす。
            levels = ("部門", "グループ") if getattr(st, "abc_dept", True) else ()
            if not levels:
                used_level = "部門なし"
            elif not products:
                # 部門はその月の商品の集計なので、商品が1品も無い月に部門だけ
                # 出ることはない。空月で部門の切替を粘るのは丸ごと無駄。
                levels = ()
                used_level = "商品なし"
            for level in levels:
                for dtry in range(3):
                    try:
                        session.page.mouse.wheel(0, -3000)  # 条件パネルを可視域へ
                    except Exception:  # noqa: BLE001
                        pass
                    _abc_click_radio(session.page, level)
                    time.sleep(1)
                    drows = _abc_search_and_rows(session)
                    if any(_countable_dept(c) for c in drows):
                        used_level = level
                        break
                    print(f"[ABC店] {code} {month} {level}グリッド未確定（{dtry + 1}回目）。再切替。")
                    time.sleep(2)
                if any(_countable_dept(c) for c in drows):
                    break
                if level == "部門":
                    print(f"[ABC店] {code} {month} 部門が切替らず。分類=グループへフォールバック。")

            n_dept, dept_total = 0, 0
            for cells in drows:
                m = _clean_dept(cells)
                if not m:
                    continue
                dname = m.group(1).strip()
                if dname in _ABC_TOTAL_NAMES or dname in ("部門", "部門名", "分類"):
                    continue
                cost_rate = m.group(2)  # 原価率 "25.25"
                qty = num(m.group(3))
                sales = num(m.group(4))
                if sales <= 0 and qty <= 0:
                    continue
                collected.append(
                    ActualRow(
                        store_code=code, date=rep_date, grain=GRAIN_MONTH,
                        metric=METRIC_DEPT_SALES, value=float(sales),
                        product_name=dname[:80], product_category=cost_rate,
                        kind=KIND_FINAL, source=source, ingested_at=ingested_at,
                    )
                )
                collected.append(
                    ActualRow(
                        store_code=code, date=rep_date, grain=GRAIN_MONTH,
                        metric=METRIC_DEPT_QTY, value=float(qty),
                        product_name=dname[:80], product_category=cost_rate,
                        kind=KIND_FINAL, source=source, ingested_at=ingested_at,
                    )
                )
                n_dept += 1
                dept_total += sales

            if n_dept == 0 and n_prod == 0:
                empty.append(month)
                empty_streak += 1
            else:
                seen_any = True
                empty_streak = 0
                if n_dept == 0 and getattr(st, "abc_dept", True):
                    no_dept.append(month)
            # 同じ合計の月が二度出たら、日付が効かず同じグリッドを読んでいる疑い。
            dup = seen_totals.get(dept_total) if dept_total else None
            if dup:
                suspect.append(f"{month}={dup}")
            elif dept_total:
                seen_totals[dept_total] = month
            mark = " ⚠同額" if dup else ""
            _flush(month)
            print(
                f"[ABC店] {code} {month} [{used_level}] 商品{len(products)}品→{n_prod}行"
                f"（1位 {top}） 部門{n_dept}件 合計{dept_total:,}{mark}"
                f" 累計{total_loaded}行 (+{time.time() - t0:.0f}s)"
            )
            # 降順で回しているので、データのある月より古い側で空月が続いたら開店前。
            # これ以上さかのぼっても出ないので止める。
            if seen_any and empty_streak >= 3 and months[i + 1 :]:
                print(
                    f"[ABC店] {code} 開店前と判断して打ち切り"
                    f"（残り{len(months[i + 1:])}ヶ月は取りません）"
                )
                break

    if empty:
        print(f"[ABC店] {code} データが無かった月: {empty}")
    if no_dept:
        # 部門が取れるはずの店なのに0件＝その月の読み取りが失敗している。
        # 既存の部門は消していない（scope_metrics）ので、この月だけ流し直せばよい。
        print(f"[ABC店] ⚠ {code} 商品は取れたが部門が0件の月: {no_dept}")
        print(f"[ABC店] 入れ直し: --abc-store {code} --month {','.join(no_dept)}")
    if suspect:
        # 取り込みは止めない（同額でも本当に同額な可能性は残る）が、必ず目に付くよう出す。
        print(f"[ABC店] ⚠ {code} 部門合計が他の月と一致: {suspect}")
        print("[ABC店] ⚠ 日付が反映されていない疑い。query.yml の dept_sales で月別に検算すること。")
    if dry_run:
        print("[ABC店] dry-run のため書き込みはしません")
        return 0
    print(
        f"[ABC店] warehouse へ {total_loaded} 件 書き込みました"
        f"（source={source} / {len(written_months)}ヶ月ぶん）"
    )
    if skipped:
        # 取れた月は既に入っている。残りだけ流し直せばよいので、そのまま貼れる形で出す。
        print(f"[ABC店] 取り残し: --abc-store {code} --month {','.join(skipped)}")
        return 1
    return 0


def _abc_click_radio(page, label: str) -> bool:
    """条件パネルのラジオ/ラベル（全商品・部門・グループ・メニュー等）を実クリックする。

    商品グリッドを読んだ後は条件パネルが画面外へスクロールしていることがあり、
    その状態だと is_visible=False でクリックを取りこぼす（1069/1137 で部門に切替らず
    全商品のままだった原因）。クリック前に必ず可視域へスクロールする。
    """
    # FWの分類ラジオは Bootstrap のボタン型:
    #   <label class="btn btn-gray"><input type=radio value="部門" name="出力分類部門1">部門</label>
    # Angularの切替は LABEL のクリックに結線されており、input を .check() しても UIは
    # 変わらない（1069/1137 が切替らなかった真因＝input を check して True を返し、効く
    # ラベルクリックに進まなかった）。value で対象ラベルを一意に掴んでクリックする。
    for sel in (
        f'label:has(input[type="radio"][value="{label}"])',
        f'label:has(input[type="radio"][name^="出力分類{label}"])',
        f'label:has(input[type="radio"][name^="分類{label}"])',
    ):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                try:
                    el.scroll_into_view_if_needed(timeout=1500)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    el.click(timeout=2000)
                    return True
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
    loc = page.get_by_text(label, exact=True)
    for i in range(min(loc.count(), 12)):
        try:
            el = loc.nth(i)
            try:
                el.scroll_into_view_if_needed(timeout=1500)
            except Exception:  # noqa: BLE001
                pass
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
    # 空白差（全角/半角）や店名表記ゆれに強くする: まず素の部分一致、
    # 駄目なら空白除去で部分一致、それも駄目なら店名の識別断片で照合。
    _nospace = lambda s: (s or "").replace(" ", "").replace("　", "")  # noqa: E731
    hit = next((n for n in names if store_name in n), None)
    if hit is None:
        tgt = _nospace(store_name)
        hit = next((n for n in names if tgt in _nospace(n)), None)
    if hit is None:
        # 識別しやすい断片（末尾の「〇〇店」やブランド後半）でゆるく照合
        frag = _nospace(store_name)[-4:]
        hit = next((n for n in names if frag and frag in _nospace(n)), None)
    if hit is None:
        print(f"[ABCprobe] 左リストに『{store_name}』一致なし。候補先頭: {names[:12]}")
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
        # ランチ部門・沼パスタが下位（行60以降）に埋もれるため、全行を出しつつ
        # ランチ関連キーワード一致行を別途ハイライトする。
        KW = ("スープ", "沼", "ランチ", "パスタ", "完熟", "ペペロン", "こくうま", "クリーム", "禁断")
        for level in levels:
            ok = _abc_click_radio(session.page, level)
            print(f"[ABCprobe] 分類ラジオ『{level}』クリック={ok}")
            rows = _abc_search_and_rows(session)
            print(f"[ABCprobe] === 分類={level} 視覚行（先頭200/計{len(rows)}） ===")
            for cells in rows[:200]:
                print("   ", " | ".join(cells[:14]))
            hits = [c for c in rows if any(k in " ".join(c) for k in KW)]
            if hits:
                print(f"[ABCprobe] --- {level}: ランチ関連キーワード一致 {len(hits)}行 ---")
                for cells in hits:
                    print("   *", " | ".join(cells[:14]))
        session.snapshot("abc_store_probe")
    return 0


MENU_HOURLY_MENU = ("販売管理", "店舗業務", "時間帯別メニュー出数")


def probe_menu_hourly(
    artifacts: Path,
    *,
    store: str,
    ranges: list[tuple[str, str, str]],
    dump_rows: int = 45,
) -> int:
    """時間帯別メニュー出数（商品×時間帯の出数マトリクス）を1店・複数期間で吸い出す診断。

    深夜(22時〜)の総出数を得て、時間帯別売上の客数・客単価と合わせて
    『深夜の1人あたり品数・1品単価』を分解するための素材。まずは行（合計行・
    見出しの時間帯ラベル・商品行）をそのままダンプして横並びの列構造を把握する。
    """
    import sys

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    from .fw_budget import _click_search, _combo_options, _select_combo

    with fw_session(artifacts) as session:
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass
        _open_menu(session, MENU_HOURLY_MENU)
        options = _combo_options(session)
        hit = next((o for o in options if store in o["name"]), None)
        if hit is None:
            print(f"[出数probe] コンボに『{store}』なし。候補: {[o['name'][:16] for o in options[:12]]}")
            return 1
        print(f"[出数probe] 対象店: {hit['name']}（value={hit['value']}）／{len(ranges)}レンジ")
        for label, d_from, d_to in ranges:
            _select_combo(session, hit["value"])
            _set_date_range(session, d_from, d_to)
            _click_search(session)
            # 合計行（時間帯別の総出数）が埋まるまで粘る（読み込み中を避ける）
            rows: list[list[str]] = []
            for _ in range(10):
                time.sleep(1.5)
                rows = _visual_rows(session)
                tot = next((c for c in rows if c and c[0].strip() == "合計"
                            and len([x for x in c if x.strip()]) > 6), None)
                if tot:
                    break
            print(f"=== {label} {d_from}〜{d_to}（視覚行 {len(rows)}） ===")
            for cells in rows[:dump_rows]:
                line = " | ".join(c for c in cells[:80] if c is not None)
                print("   ", line[:500])
    return 0


def probe_abc_totals(
    artifacts: Path,
    *,
    store: str,
    ranges: list[tuple[str, str, str]],
) -> int:
    """1店・複数期間で ABC(分類=グループ) の グループ合計＋総合計（数量・売上）を印字。

    施策前後で『一人あたりの注文品数（出品数）』が上がったかを見るための素材。
    フード/ドリンク/コースの各数量と総合計を期間ごとに出す。客数（時間帯別プローブの
    合計）で割れば 品数/客 が出る。ranges は (ラベル, YYYY/MM/DD_from, YYYY/MM/DD_to)。
    """
    import re
    import sys

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    hdr = re.compile(r"^(.+?)\s\|\s(\d+\.\d+)%\s\|\s([\d,]+)\s\|\s([\d,]+)")
    num = lambda s: int(s.replace(",", ""))  # noqa: E731
    want = ("フード", "ドリンク", "コース", "合計")
    with fw_session(artifacts) as session:
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass
        _open_abc(session)
        _select_date_preset(session, _ABC_PRESET_LASTMONTH)
        hit = None
        for label, d_from, d_to in ranges:
            _set_date_range(session, d_from, d_to)
            if hit is None:
                hit = _abc_open_store_modal_and_select_one(session, store)
                if hit is None:
                    print("[ABC合計probe] 店舗選択に失敗。終了。")
                    return 1
                print(f"[ABC合計probe] 対象店: {hit}")
            print(f"=== {label} {d_from}〜{d_to} ===")
            _abc_click_radio(session.page, "グループ")
            rows = _abc_search_and_rows(session)
            for cells in rows:
                m = hdr.match(" | ".join(cells[:6]))
                if not m:
                    continue
                name = m.group(1).strip()
                if any(w in name for w in want):
                    print(f"  {name:<12} 数量{num(m.group(3)):>6}  売上{num(m.group(4)):>10}")
            # 部門で 飲み放題(¥0)・ハッピーアワーの数量も拾う（有料1杯単価の算出用）
            _abc_click_radio(session.page, "部門")
            drows = _abc_search_and_rows(session)
            for cells in drows:
                m = hdr.match(" | ".join(cells[:6]))
                if m and any(w in m.group(1) for w in ("飲み放題", "ハッピーアワー")):
                    print(f"  [部門] {m.group(1).strip():<16} 数量{num(m.group(3)):>6}  売上{num(m.group(4)):>10}")
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
