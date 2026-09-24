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
            items = session.dump_clickables(f"failed_{label}")
            # ⚠️ **成果物だけに残しても読めない。** 実行環境から
            # artifacts を落とせないことがあり（blob storage が 403）、
            # そうなると「進めませんでした」しか分からず、正しいラベルを
            # 当て推量で探すことになる。画面の選択肢はログにも出す。
            print(f"[menu] 「{label}」が見つかりません。画面にあるもの {len(items)}件:")
            for it in items:
                t = " ".join((it.get("text") or "").split())
                if t:
                    print(f"    - {t[:40]}")
            raise FWError(f"「{label}」に進めませんでした")
        session.snapshot(f"opened_{label}")


def _wait_for_grid(session, *, timeout: float = 60.0, quiet: float = 3.0) -> int:
    """表が描き終わるまで待ち、読めた行数を返す。

    画面によっては検索の直後は空で、**しばらくしてから出てくる**
    （分析用コード設定がそう）。固定の sleep で読むと空の画面を
    「これが正」として記録してしまい、次のランをまるごと無駄にする。
    行数が増えなくなってから読む。
    """
    start = time.monotonic()
    last, stable_since = -1, start
    while time.monotonic() - start < timeout:
        n = session.page.evaluate(
            """() => [...document.querySelectorAll('table')]
                 .filter(t => t.offsetParent)
                 .reduce((a, t) => a + t.querySelectorAll('tr').length, 0)"""
        )
        if n != last:
            last, stable_since = n, time.monotonic()
        elif n > 0 and time.monotonic() - stable_since >= quiet:
            break
        time.sleep(0.5)
    print(f"[probe] 表の行 {last}行で落ち着いた（{time.monotonic() - start:.0f}秒待った）")
    return last


def report_probe(artifacts: Path, path_str: str) -> int:
    """カンマ区切りのメニューパスを順にクリックして開き、店舗を選び検索して
    グリッド構造を吸い出す。日別売上/客数がどの画面にあるかを特定する診断。
    例: "損益管理,実績管理業務,月別日別実績"
    """
    labels = [s.strip() for s in path_str.split(",") if s.strip()]
    last_label = labels[-1] if labels else path_str
    with fw_session(artifacts) as session:
        # 1段ずつ押して、**そのたびに画面の項目を出す**。
        # まとめて `_open_menu` で開くと、失敗した段の画面しか見られない。
        # 同じ名前の項目が2か所にあると（販売管理はナビとマスタ管理の下の
        # 両方にある）、狙いと違うほうを押しても気づけない。位置(x,y)まで
        # 出すのは、どちらを押したのかを見分けるため。
        for i, label in enumerate(labels, 1):
            ok = session.click_text(label)
            items = session.dump_clickables(f"step{i}_{label}")
            print(f"[probe] {i}. 「{label}」 {'押せた' if ok else '押せなかった'}"
                  f" → いま画面にある項目 {len(items)}件")
            for it in items:
                t = " ".join((it.get("text") or "").split())
                if t:
                    print(f"    {it.get('tag',''):<6} x={it.get('x',0):>4} y={it.get('y',0):>4}"
                          f"  {t[:38]}")
            if not ok:
                session.snapshot(f"missing_{label}")
                raise FWError(f"「{label}」に進めませんでした")
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
        # 固定待ちにしない。描き終わるまで待たないと空を正として記録する。
        _wait_for_grid(session)
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
    # 指標も範囲に含める。含めないと、売上が取れて客数が取れなかった月に、
    # 売上行が (店, 月) を削除範囲へ引き込んで既存の客数を消してしまう。
    loaded = warehouse.replace_actuals(collected, scope_stores=True, scope_metrics=True)
    print(f"[売上推移] warehouse へ {loaded} 件 書き込みました")
    if not collected:
        print("::error::[売上推移] 1件も取り込めませんでした")
        return 1
    if failures:
        print(f"::error::[売上推移] 取れなかった 店/対象月 {len(failures)}件")
        return 1
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
    # 当月（月途中）は終端日を今日までに詰める。末日（未来日）を投げると当月の
    # グリッドが出ない/別挙動になる店があるため、9/16実行なら 9/16 で止める。
    _today = datetime.now(timezone.utc).date()
    if (_today.year, _today.month) == (int(month[:4]), int(month[5:7])):
        d_to = _today.strftime("%Y/%m/%d")
    rep_date = _date(int(month[:4]), int(month[5:7]), 1)

    source = "fw_hourly"
    ingested_at = datetime.now(timezone.utc)
    active_by_code = {s.store_code: s for s in master.active}
    collected: list[ActualRow] = []
    skipped: list[str] = []

    # まず1セッションでコンボ候補→対象店リストを作る。
    with fw_session(artifacts) as session:
        _open_hourly(session)
        options = _combo_options(session)
        print(f"[時間帯別] 店舗コンボボックス {len(options)}件 / 対象月 {month}（{d_from}〜{d_to}）")
        targets = []
        skipped_pos = []
        for opt in options:
            code = opt["value"].lstrip("0")
            store = active_by_code.get(code) or master.find_by_name(opt["name"])
            if not (store and store.active):
                continue
            # FW未連動の店（pos=uレジ/ダイニー）はFWにデータが無いので対象外。掘っても
            # 毎回「グリッド無し」で失敗するだけ（例 1766 ぎふや福岡天神＝uレジ）。
            if store.pos != "fw":
                skipped_pos.append(f"{store.store_code}:{store.pos}")
                continue
            targets.append((opt["value"], store))
    if skipped_pos:
        print(f"[時間帯別] FW未連動でスキップ {len(skipped_pos)}件: {', '.join(skipped_pos)}")
    print(f"[時間帯別] マスタと一致した稼働店 {len(targets)}件")
    if store_limit:
        targets = targets[:store_limit]

    # 【真因】同一セッションで店を切り替え続けると、2店目以降でグリッドが再描画され
    # なくなることがある（実測：バッチ4でも一部の店が「グリッド無し」で落ちる。単店
    # プローブは常に満額読める＝データはあり、1店1セッションなら確実）。
    # 対策：1店＝1セッション（BATCH=1）。検索が1セッション1回になるので取りこぼさない。
    # 実行頻度は低い（月次の更新）ので再ログインのコストは許容する。
    BATCH = 1
    diag_done = False
    for bi in range(0, len(targets), BATCH):
        chunk = targets[bi:bi + BATCH]
        with fw_session(artifacts) as session:
            _open_hourly(session)
            _combo_options(session)  # コンボが描画されるまで待つ（選択の空振り防止）
            time.sleep(1.0)          # 新セッション直後はコンボ確定に少し猶予を持たせる
            for value, store in chunk:
                name = store.store_name
                # 店舗選択は新セッション直後にまれに空振りする（バッチ先頭が丸ごと
                # 失敗する事象＝一過性で、走行ごとに落ちる店が変わる）。数回まで粘る。
                sel = False
                for _sa in range(3):
                    if _select_combo(session, value):
                        sel = True
                        break
                    time.sleep(1.5)
                if not sel:
                    print(f"[時間帯別] 店舗選択に失敗: {name} ({value})")
                    skipped.append(f"{store.store_code}:店舗選択")
                    continue
                if not _set_date_range(session, d_from, d_to):
                    # 日付が効いていなければ別期間のグリッドを読むことになる。続行しない。
                    print(f"[時間帯別] 日付レンジ設定に失敗: {name}")
                    skipped.append(f"{store.store_code}:日付レンジ")
                    continue
                # 描画待ち：検索後、行数が伸び止まる（=描画完了）までポーリング。
                _click_search(session)
                best: list[dict] = []
                stable = 0
                for _ in range(10):  # 最大 ~15秒
                    time.sleep(1.5)
                    g = _extract_hour_grid(session)
                    if len(g) > len(best):
                        best = g
                        stable = 0
                    elif g and len(g) == len(best):
                        stable += 1
                        if stable >= 2:  # 2回連続で同数＝描画完了
                            break
                if not diag_done:
                    diag_done = True
                    print("[時間帯別] 先頭店の視覚行（先頭12行・診断用）:")
                    for cells in _visual_rows(session)[:12]:
                        print("   ", " | ".join(cells[:14]))
                if not best:
                    session.snapshot(f"nohour_{store.store_code}")
                    print(f"  {store.store_code} {name[:14]} 時間帯グリッドが読めませんでした")
                    skipped.append(f"{store.store_code}:グリッド無し")
                    continue
                n_before = len(collected)
                for band in best:
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
                hours = sorted({b["hour"] for b in best})
                hspan = f"{hours[0]}〜{hours[-1]}時" if hours else "-"
                print(
                    f"  {store.store_code} {name[:14]} {len(best)}帯 {hspan}"
                    f" +{len(collected) - n_before}行"
                )

    print(f"[時間帯別] 収集 {len(collected)} 行")
    if skipped:
        print(f"[時間帯別] ⚠ 取れなかった店 {len(skipped)}件: {', '.join(skipped)}")
    if dry_run:
        print("[時間帯別] dry-run のため書き込みはしません")
        return 1 if skipped else 0
    warehouse.ensure_schema()
    # 取れた店・取れた指標だけを入れ替える。店を含めないと取りこぼした店の
    # 既存データが巻き添えで消える。指標を含めないと、ある帯で売上は取れて
    # 客数が0だったとき（val > 0 でしか行を作らない）既存の客数が消える。
    loaded = warehouse.replace_actuals(collected, scope_stores=True, scope_metrics=True)
    print(f"[時間帯別] warehouse へ {loaded} 件 書き込みました")
    if not collected:
        print("::error::[時間帯別] 1件も取り込めませんでした")
        return 1
    if skipped:
        print(f"::error::[時間帯別] {len(skipped)}店ぶん取りこぼしました")
        return 1
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
            # 描画待ち：行数が伸び止まるまでポーリング（単発待ちだと取りこぼす）
            grid = []
            best = []
            stable = 0
            for _ in range(12):
                time.sleep(1.5)
                g = _extract_hour_grid(session)
                if len(g) > len(best):
                    best = g
                    stable = 0
                elif g and len(g) == len(best):
                    stable += 1
                    if stable >= 2:
                        break
            grid = best
            if not grid:
                session.snapshot(f"nohour_probe_{label}")
                print(f"=== {label} {d_from}〜{d_to} === グリッドなし。画面の視覚行（先頭18行・診断）:")
                for cells in _visual_rows(session)[:18]:
                    print("   ", " | ".join((c or "") for c in cells[:14]))
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
_ABC_COST = 3  # 同・原価金額位置（列: 単価0 数量1 売上2 原価金額3 粗利4）
_ABC_GROSS = 4  # 同・粗利金額位置
# 合計・総計・小計は商品ではないので商品グリッドから除外する
_ABC_TOTAL_NAMES = {"合計", "総計", "小計", "合 計", "総 計", "小 計", "総合計"}

# ABCの視覚行から「部門行」を見分ける。取込とprobeで同じ判定を使う
# （別々に書いていたら片方だけ直して食い違った）。
_ABC_DEPT_ROW = re.compile(r"^(.+?)\s\|\s(\d+\.\d+)%\s\|\s([\d,]+)\s\|\s([\d,]+)")


def _abc_dept_match(cells: list[str]):
    """視覚行が部門行の形なら正規表現マッチを返す。商品行なら None。

    全商品行（"商品CD | 商品名 | 単価 | 原価 | 原価率% | 数量"）も
    数字の並びは似ているが、部門名にあたる部分に ' | ' が入るので弾ける。
    """
    m = _ABC_DEPT_ROW.match(" | ".join(c for c in cells[:6] if c is not None))
    if not m:
        return None
    return m if "|" not in m.group(1) else None


def _abc_countable_dept(cells: list[str]) -> bool:
    """後段で実際に数える部門行か（合計・見出しでなく、数量か売上が正）。

    「部門らしい行が1つでもあるか」で受け入れると、合計行しか出ていない
    グリッドを『取れた』と見なして部門0件のまま抜ける（1111/1151/1168 で
    再試行のログすら出ずに0件になっていた）。数えられる行で判定する。
    """
    m = _abc_dept_match(cells)
    if not m:
        return False
    name = m.group(1).strip()
    if name in _ABC_TOTAL_NAMES or name in ("部門", "部門名", "分類"):
        return False
    return int(m.group(3).replace(",", "")) > 0 or int(m.group(4).replace(",", "")) > 0
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


# 商品行から「商品名」にあたるセルを選ぶ。ランク(A/B/C)・商品CD・値は名前ではない。
_ABC_RANK_RE = re.compile(r"^[ABC]$")
# 合計行はラベルのセルが空のことがあり、その場合は次の原価率セル（例 "25.57%"）を
# 商品名として拾ってしまう。実際 1151 の 2025-02 に「25.57%」という商品が
# ¥2,378,250 で入っていた。数値・パーセントだけの文字列は商品名ではない。
_ABC_NUMLIKE_RE = re.compile(r"^[\d,]+(\.\d+)?%?$")


def _abc_product_name_index(cells: list[str]) -> int | None:
    """商品名にあたるセルの位置。見つからなければ None。"""
    for i, c in enumerate(cells):
        t = (c or "").strip()
        if not t:
            continue
        if _INT_RE.match(t):  # 数字（商品CD・値）はスキップ
            continue
        if _ABC_RANK_RE.match(t):  # 単独の A/B/C はランク
            continue
        if _ABC_NUMLIKE_RE.match(t):  # "25.57%" や "1,234" は値であって商品名ではない
            continue
        if len(t) >= 2:  # 商品名らしい文字列
            return i
    return None


def _extract_product_grid(session) -> list[dict]:
    """ABC分析のグリッドを視覚行に復元し、商品行だけ返す。

    各要素は {"name": 商品名, "rank": "A"/"B"/"C"/None, "ints": [名前より後ろの整数]}。
    商品名（先頭の非数字・非ランクの文字セル）を起点にし、続く整数が4つ以上ある行を採る。
    見出し・合計・データなしは自然に除外される。
    """
    result: list[dict] = []
    seen: set[str] = set()
    for cells in _visual_rows(session):
        name_idx = _abc_product_name_index(cells)
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
            if _ABC_RANK_RE.match(c.strip()):
                rank = c.strip()
                break
        seen.add(name)
        result.append({"name": name, "rank": rank, "ints": ints})
    return result


# 「NN:区分名」形式のグループ見出し行（例 "20:テイクアウトジェラート"）。分類=グループ／
# メニューで各商品の上に出る小計行。商品ではないので取り込まず、直後の商品行に
# その所属グループとして貼る。全角コロンも許す。
_ABC_GROUP_RE = re.compile(r"^\s*\d+\s*[:：]\s*\S")


def _extract_product_grid_grouped(session) -> list[dict]:
    """分類=グループ／メニューのグリッドを、各商品に所属グループを付けて返す。

    各要素は {"name", "rank", "ints", "group"}。group は直前に現れた「NN:区分名」見出し
    （例 "20:テイクアウトジェラート"）。全商品グリッドには内訳（0円の選択商品）が出ないため、
    サンデー/テイクアウトジェラート等の“素の風味名”を正しい区分へ束ねるのに使う。"""
    result: list[dict] = []
    seen: set[tuple[str | None, str]] = set()
    group: str | None = None
    for cells in _visual_rows(session):
        name_idx = _abc_product_name_index(cells)
        if name_idx is None:
            continue
        name = cells[name_idx].strip()
        if _ABC_GROUP_RE.match(name):  # 区分見出し行。商品ではない。
            group = name
            continue
        if name in ("商品名",) or name in _ABC_TOTAL_NAMES:
            continue
        ints = _row_ints(cells[name_idx + 1:])
        if len(ints) < 3:  # [単価,数量,売上] は要る
            continue
        key = (group, name)
        if key in seen:
            continue
        rank = None
        for c in reversed(cells):
            if _ABC_RANK_RE.match(c.strip()):
                rank = c.strip()
                break
        seen.add(key)
        result.append({"name": name, "rank": rank, "ints": ints, "group": group})
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
        METRIC_PRODUCT_COST,
        METRIC_PRODUCT_GROSS,
        METRIC_PRODUCT_QTY,
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
            # 「無害」ではない。ここで抜けると全店ぶん0件のまま緑で終わる。
            print("::error::[ABC] 店舗選択に失敗しました")
            return 1
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
                # 販売点数（何個売れたか）。売上と並べて出すため同じ商品名で持つ。
                if len(ints) > _ABC_QTY and ints[_ABC_QTY] > 0:
                    collected.append(
                        ActualRow(
                            store_code=ABC_GROUP_CODE,
                            date=rep_date,
                            grain=GRAIN_MONTH,
                            metric=METRIC_PRODUCT_QTY,
                            value=float(ints[_ABC_QTY]),
                            product_name=prod["name"][:80],
                            product_category=prod["rank"],
                            kind=KIND_FINAL,
                            source=source,
                            ingested_at=ingested_at,
                        )
                    )
                # 原価金額・粗利金額（ABCグリッド由来）。売上・点数と同じ商品名で並べる。
                for _m, _i in ((METRIC_PRODUCT_COST, _ABC_COST), (METRIC_PRODUCT_GROSS, _ABC_GROSS)):
                    if len(ints) > _i:
                        collected.append(
                            ActualRow(
                                store_code=ABC_GROUP_CODE,
                                date=rep_date,
                                grain=GRAIN_MONTH,
                                metric=_m,
                                value=float(ints[_i]),
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


def coverage_in_scope(*, cannot: str | None, has_any: bool) -> bool:
    """この店をカバレッジの分母に数えるか。

    FWから取れない店（1766 のような別POS連動）でも、**別の口から実績が
    入っていればふつうに数える**。ここを「FWから取れないなら外す」だけで
    決めると、店別の行は ✓ なのに月別の分母からは外れ、
    『完備 23店』と『22/22』が食い違う（2026-09 で実際そうなった）。
    """
    return cannot is None or has_any


def report_monthly_coverage(
    warehouse,
    master,
    *,
    date_from: str = "2024-01",
    date_to: str = "2026-08",
    metric: str | None = None,
    grain: str | None = None,
) -> int:
    """指定指標（既定=月次売上）が店×月でどこまで埋まっているかを出す。

    「どの店のどの月が無いのか」を1回のDB照会で一覧にする。FWログイン不要。
    バックフィルの前後で回して、埋まったか・どこが穴かを確かめるための道具。
    metric に dept_sales / product_sales を渡すと、ABCの取りこぼし月を洗い出せる
    （FWのグリッドは稀に埋まりきる前に読まれ、その月だけ0件になることがある）。
    grain=hour を渡すと時間帯別（fw_hourly, 代表日=月初）の店×月カバレッジを出せる。
    """
    import sys as _sys
    from datetime import date as _date

    from ..db.warehouse import AggregateQuery
    from ..model import GRAIN_HOUR, GRAIN_MONTH, METRIC_DEPT_SALES, METRIC_SALES

    grain = grain or GRAIN_MONTH

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
            grain=grain,
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

    # 開業日 "YYYY-MM-DD" → 開店月 "YYYY-MM"。この月より前は営業しておらず、
    # 実績が無いのが正しい＝欠けではなく対象外(N/A)として数える。
    def _open_month(st) -> str | None:
        o = (getattr(st, "opened", "") or "").strip()
        return o[:7] if len(o) >= 7 else None

    def _expected(st) -> list[str]:
        om = _open_month(st)
        return [m for m in want if om is None or m >= om]

    print(f"=== 店×月カバレッジ [{metric}/{grain}] {date_from}〜{date_to}（{len(want)}ヶ月） ===")
    full, partial, empty, other_pos = [], [], [], []
    for st in master.active:
        got = have.get(st.store_code, set())
        exp = _expected(st)
        miss = [m for m in exp if m not in got]
        pre = len(want) - len(exp)  # 開店前で対象外の月数
        note = f"（開店前{pre}ヶ月は対象外）" if pre else ""
        head = f"  {st.store_code} {st.store_name[:16]:<16} {len(exp) - len(miss):>2}/{len(exp)}"
        cannot = _cannot(st)
        if not coverage_in_scope(cannot=cannot, has_any=bool(got)):
            other_pos.append(st.store_code)
            print(f"― {head}  {cannot}")
        elif not miss:
            full.append(st.store_code)
            print(f"✓ {head}  すべて有り{note}")
        elif len(miss) == len(exp):
            empty.append(st.store_code)
            print(f"✗ {head}  データ無し{note}")
        else:
            partial.append(st.store_code)
            shown = ",".join(miss[:14]) + (" …" if len(miss) > 14 else "")
            print(f"△ {head}  欠け: {shown}{note}")

    # 月ごとに「何店ぶん入っているか」も出す。穴が月側か店側かの切り分け用。
    # 分母はその月に営業していた店数（開店前の店は数えない）。
    print("--- 月別に埋まっている店数（分母=その月の営業店） ---")
    for m in want:
        open_here = [
            st for st in master.active
            if (_open_month(st) is None or m >= _open_month(st))
            # 分母の決め方は店別の行と**必ず同じ**にする。片方だけ
            # 「FWから取れない店は外す」にすると数が合わなくなる。
            and coverage_in_scope(cannot=_cannot(st), has_any=bool(have.get(st.store_code)))
        ]
        denom = len(open_here)
        n = sum(1 for st in open_here if m in have.get(st.store_code, set()))
        bar = "■" * round(n / max(denom, 1) * 20)
        print(f"  {m}  {n:>2}/{denom}  {bar}")
    print(
        f"=== 完備 {len(full)}店 / 欠けあり {len(partial)}店 / 皆無 {len(empty)}店"
        f" / FWでは取れない {len(other_pos)}店 ==="
    )
    if empty:
        print(f"データ皆無の店（取れるはずなのに0件＝要調査）: {empty}")
    if other_pos:
        print(f"FWでは取れない店（理由は上の ― 行）: {other_pos}")
    return 0


def report_data_audit(
    warehouse,
    master,
    *,
    date_from: str = "2024-09",
    date_to: str = "2026-08",
) -> int:
    """取り込み済みのデータそのものを疑って、おかしな行を洗い出す。FWログイン不要。

    「取れたつもり」は取込ログだけでは見つからないことが分かったので、貯まった側から
    調べる。実際に見つかった事故を型として持っている:
      - 合計行の原価率が商品名として入る（例: 商品名が "25.57%" で ¥2,378,250）
      - 月をまたいで数字が丸ごと同一（日付が反映されていない）
      - 部門合計が店の月次売上とかけ離れている（部分的にしか読めていない）
    """
    import sys as _sys
    from datetime import date as _date

    from ..db.warehouse import AggregateQuery
    from ..model import (
        GRAIN_MONTH,
        METRIC_DEPT_SALES,
        METRIC_PRODUCT_SALES,
        METRIC_SALES,
    )

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    y, m = int(date_from[:4]), int(date_from[5:7])
    d_from = _date(y, m, 1)
    ly, lm = int(date_to[:4]), int(date_to[5:7])
    d_to = _date(ly + (lm == 12), 1 if lm == 12 else lm + 1, 1)
    codes = list(master.active_codes)
    name_of = {s.store_code: s.store_name for s in master.active}

    def _pull(metric):
        out: dict[tuple[str, str], list[tuple[str, float]]] = {}
        for row in warehouse.aggregate(
            AggregateQuery(
                date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
                metrics=[metric], store_codes=codes,
                group_by=("store_code", "date", "product_name"),
            )
        ):
            key = (row["store_code"], row["date"].strftime("%Y-%m"))
            out.setdefault(key, []).append((row["product_name"] or "", row["value"]))
        return out

    prods = _pull(METRIC_PRODUCT_SALES)
    depts = _pull(METRIC_DEPT_SALES)

    sales: dict[tuple[str, str], float] = {}
    for row in warehouse.aggregate(
        AggregateQuery(
            date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
            metrics=[METRIC_SALES], store_codes=codes, group_by=("store_code", "date"),
        )
    ):
        sales[(row["store_code"], row["date"].strftime("%Y-%m"))] = row["value"]

    findings = 0

    # ① 商品名が値そのもの（合計行の取り違え）
    print("=== ① 商品名が数値・パーセントになっている行 ===")
    bad_name = []
    for (code, mm), items in sorted(prods.items()):
        for nm, v in items:
            if _ABC_NUMLIKE_RE.match(nm.strip()):
                bad_name.append((code, mm, nm, v))
    for code, mm, nm, v in sorted(bad_name, key=lambda x: -x[3]):
        print(f"  {code} {name_of.get(code, ''):<14} {mm}  『{nm}』 {int(v):,}")
    print(f"  → {len(bad_name)}件" if bad_name else "  → なし")
    findings += len(bad_name)

    # ② 月をまたいで数字が丸ごと同じ（日付が効いていない）
    print("\n=== ② 同じ店で、別の月なのに合計が完全一致 ===")
    dup = []
    for metric_name, table in (("商品", prods), ("部門", depts)):
        by_store: dict[str, dict[int, str]] = {}
        for (code, mm), items in sorted(table.items()):
            tot = int(sum(v for _, v in items))
            if not tot:
                continue
            seen = by_store.setdefault(code, {})
            if tot in seen:
                dup.append((code, metric_name, seen[tot], mm, tot))
            else:
                seen[tot] = mm
    for code, metric_name, a, b, tot in dup:
        print(f"  {code} {name_of.get(code, ''):<14} {metric_name} {a} と {b} が同額 {tot:,}")
    print(f"  → {len(dup)}件" if dup else "  → なし")
    findings += len(dup)

    # ③ 部門合計が店の月次売上とかけ離れている
    #    商品計上外（サービス料等）があるので 90〜100% を妥当とみなす。
    print("\n=== ③ 部門合計が月次売上と合わない（90%未満 or 105%超） ===")
    # 店によって、FWで商品に部門が紐付き始めた月が違う（1111/1151/1168 は 2026-07）。
    # その最初の月は月の途中から紐付くので部分的にしか出ず、取込の失敗ではない。
    # 何度入れ直しても同じ値になるので、赤にし続けても直しようがない。
    # 「部門を取り始めた月」は除外して、別枠で見えるようにする。
    first_dept_month = {}
    for (code, mm) in depts:
        if sum(v for _, v in depts[(code, mm)]):
            prev = first_dept_month.get(code)
            first_dept_month[code] = mm if prev is None else min(prev, mm)

    off = []
    starting = []
    for (code, mm), items in sorted(depts.items()):
        d_tot = sum(v for _, v in items)
        s_tot = sales.get((code, mm)) or 0
        if not d_tot or not s_tot:
            continue
        ratio = d_tot / s_tot * 100
        if ratio >= 90 and ratio <= 105:
            continue
        if first_dept_month.get(code) == mm:
            starting.append((code, mm, ratio, d_tot, s_tot))
        else:
            off.append((code, mm, ratio, d_tot, s_tot))
    for code, mm, ratio, d_tot, s_tot in sorted(off, key=lambda x: x[2]):
        print(
            f"  {code} {name_of.get(code, ''):<14} {mm}  部門{int(d_tot):>11,}"
            f" / 売上{int(s_tot):>11,} = {ratio:5.1f}%"
        )
    print(f"  → {len(off)}件" if off else "  → なし")
    findings += len(off)
    if starting:
        print("  （参考）部門を取り始めた月なので除外。以降の月が正常なら問題ない:")
        for code, mm, ratio, d_tot, s_tot in sorted(starting):
            print(
                f"    {code} {name_of.get(code, ''):<14} {mm}  部門{int(d_tot):>11,}"
                f" / 売上{int(s_tot):>11,} = {ratio:5.1f}%"
            )

    # ④ 商品が極端に少ない月（グリッドを読み切れていない疑い）
    print("\n=== ④ 商品が5品以下しか入っていない月（売上はある） ===")
    thin = []
    for (code, mm), items in sorted(prods.items()):
        if len(items) <= 5 and (sales.get((code, mm)) or 0) > 0:
            thin.append((code, mm, len(items), sales.get((code, mm)) or 0))
    for code, mm, n, s_tot in thin:
        print(f"  {code} {name_of.get(code, ''):<14} {mm}  商品{n}品 / 売上{int(s_tot):,}")
    print(f"  → {len(thin)}件" if thin else "  → なし")
    findings += len(thin)

    print(f"\n=== 要確認 合計 {findings}件（{date_from}〜{date_to}） ===")
    # 検算で1件でも引っかかったらジョブを赤くする。緑のまま放置させない。
    if findings:
        print(f"::error::[検算] 要確認 {findings}件")
        return 1
    return 0


# 同じ (店,月) を2つの口が持っていて、**差があって当たり前**の組み合わせ。
# fw_sheet は税抜・fw_uriage_suii は税込で、消費税ぶんだけ必ずずれる。
# SOURCE_PRIORITY が税込を採るので運用上は問題なく、毎回並べると
# **本当に見たい異常が埋もれる**（実際 1766 の異常が23件の中に紛れた）。
#
# ⚠️ 帯を外れたら既知として扱わない。「いつもの差」で片付けると、
# 税率でも説明がつかないずれを見逃す。
KNOWN_GAPS: dict[frozenset, tuple[float, float]] = {
    frozenset({"fw_sheet", "fw_uriage_suii"}): (7.0, 11.0),
}


def classify_gap(
    by_src: dict, gap_pct: float = 1.0, adopted: float | None = None
) -> tuple[str, float]:
    """1つの (店,月) を複数の口が持っているとき、どう扱うかを決める。

    返り値は種別と差(%):
      "zero"     … **片方だけ 0 で、採用されている値も 0。** 画面が 0 になる。最優先
      "zero_ok"  … 片方だけ 0 だが採用は実数。担当外の口が 0 を書いただけ。数だけ
      "known"    … 既知の差（税抜/税込）。数だけ数えて中身は並べない
      "check"    … 要確認
      "skip"     … 単独の口・両方0・差が小さい

    ``adopted`` は**集計が実際に採る値**（画面に出る値）。0 が混ざっていても、
    採用されているのが実数なら画面は正しい。1766 は FW未連動なので fw_sheet が
    0 を書くが、pos_sheet の実数を採るので**これは正常な形**。ここを毎回赤くすると
    既知の差を外した意味が無くなる。

    ⚠ **adopted が分からないときは鳴らす側に倒す。** 「採用が 0」は
    このセッションで実際に起きた事故（kind が第1キーで FW の「確定の 0」が
    実数を押しのけた）そのもので、見逃すと画面が黙って 0 になる。

    純粋な関数にしてあるのは、DBを立てずにここを固定するため。
    """
    if len(by_src) < 2:
        return "skip", 0.0
    lo, hi = min(by_src.values()), max(by_src.values())
    if lo <= 0 < hi:
        if adopted is not None and adopted > 0:
            return "zero_ok", 100.0
        return "zero", 100.0
    if not lo:
        return "skip", 0.0
    gap = (hi / lo - 1) * 100
    if gap < gap_pct:
        return "skip", gap
    band = KNOWN_GAPS.get(frozenset(by_src))
    if band and band[0] <= gap <= band[1]:
        return "known", gap
    return "check", gap


def report_source_audit(
    warehouse,
    master,
    *,
    metric: str = "sales",
    date_from: str = "2024-09",
    date_to: str = "2026-08",
    gap_pct: float = 1.0,
) -> int:
    """同じ (店, 月, 指標) を複数の取り込み口が書いていないかを調べる。FWログイン不要。

    月次の売上は fw_sheet（店長会シート・毎日）と fw_uriage_suii（月別日別売上推移・
    手動バックフィル）の両方が書き得る。入れ替えの範囲は (source, grain, date) なので
    両方の行が共存し、どちらが採用されるかは ingested_at 任せ。二つの定義が違えば
    （税込/税抜・純売上/総売上）、バックフィルを流した日から画面の数字が黙って変わる。

    月ごとに「どの source が何店ぶん書いているか」と、同じ (店,月) を複数 source が
    持っていて値が gap_pct% 以上ずれている件数を出す。
    """
    import sys as _sys

    from ..db.warehouse import AggregateQuery
    from ..model import GRAIN_MONTH

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    y, m = int(date_from[:4]), int(date_from[5:7])
    ly, lm = int(date_to[:4]), int(date_to[5:7])
    d_from = f"{y:04d}-{m:02d}-01"
    d_to = f"{ly + (lm == 12):04d}-{1 if lm == 12 else lm + 1:02d}-01"
    table = warehouse.table_name("f_actuals")
    rows = warehouse.query(
        f"""
        SELECT store_code, date, source, kind, MAX(value) AS value
        FROM {table}
        WHERE metric = :metric AND grain = :grain
          AND date >= :d_from AND date < :d_to
        GROUP BY store_code, date, source, kind
        ORDER BY date, store_code, source
        """,
        {"metric": metric, "grain": GRAIN_MONTH, "d_from": d_from, "d_to": d_to},
    )
    if not rows:
        print(f"[出どころ] {metric} の月次データがありません（{date_from}〜{date_to}）")
        return 0

    name_of = {s.store_code: s.store_name for s in master.active}
    # 月 → source → 店数、および 月 → 店 → {source: 値}
    per_month: dict[str, dict[str, int]] = {}
    per_cell: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        month = r["date"].strftime("%Y-%m")
        src = r["source"]
        per_month.setdefault(month, {})[src] = per_month.setdefault(month, {}).get(src, 0) + 1
        per_cell.setdefault((r["store_code"], month), {})[src] = float(r["value"])

    # **集計が実際に採る値**（＝画面に出る値）を引く。採用順の再実装ではなく、
    # 画面と同じ経路をそのまま使う。0 が混ざっていても採用が実数なら画面は正しい。
    adopted: dict[tuple[str, str], float] = {}
    try:
        from datetime import date as _date

        for row in warehouse.aggregate(
            AggregateQuery(
                date_from=_date(y, m, 1),
                date_to=_date(ly, lm, 28),
                grain=GRAIN_MONTH,
                metrics=[metric],
                group_by=("store_code", "date"),
            )
        ):
            adopted[(row["store_code"], row["date"].strftime("%Y-%m"))] = float(row["value"])
    except Exception as exc:  # noqa: BLE001
        # 引けなかったら**鳴らす側に倒す**（adopted なし＝全部 zero 扱い）。
        print(f"  （採用値を引けませんでした: {exc}。片方0はすべて要対応として出します）")

    print(f"=== 出どころ別 取り込み状況 metric={metric} / {date_from}〜{date_to} ===\n")
    print("月ごとの source（店数）:")
    prev_srcs: set[str] | None = None
    switches: list[str] = []
    for month in sorted(per_month):
        srcs = per_month[month]
        label = " / ".join(f"{s}:{n}店" for s, n in sorted(srcs.items()))
        mark = ""
        if prev_srcs is not None and set(srcs) != prev_srcs:
            mark = "  ← ここで出どころが変わっています"
            switches.append(month)
        print(f"  {month}  {label}{mark}")
        prev_srcs = set(srcs)

    # 同じ (店,月) を複数 source が持ち、値がずれているもの。
    # **既知の差と、片方が0のケースを分けて数える。**
    conflicts, known, zeros, zeros_ok = [], [], [], []
    bucket = {"zero": zeros, "zero_ok": zeros_ok, "known": known, "check": conflicts}
    for (code, month), by_src in sorted(per_cell.items()):
        kind_, gap = classify_gap(by_src, gap_pct, adopted.get((code, month)))
        if kind_ == "skip":
            continue
        bucket[kind_].append((code, month, by_src, gap))

    def _show(items, limit=40, show_gap=True):
        for code, month, by_src, gap in items[:limit]:
            detail = " / ".join(f"{s}={v:,.0f}" for s, v in sorted(by_src.items()))
            head = f"  差 {gap:.1f}%" if show_gap else ""
            print(f"  {code} {name_of.get(code, '')[:14]} {month}{head}  {detail}")
        if len(items) > limit:
            print(f"  … 他 {len(items) - limit}件")

    if known:
        # 数だけ出す。中身まで並べると本当に見たいものが埋もれる。
        pairs = " / ".join(sorted({"+".join(sorted(b)) for _, _, b, _ in known}))
        print(f"\n既知の差（{pairs}）: {len(known)}件 … 税抜と税込。SOURCE_PRIORITY で"
              "税込を採るので問題ない")

    if zeros_ok:
        # 担当外の口が 0 を書いただけ。採用は実数なので画面は正しい。
        # 例: 1766 は FW未連動で fw_sheet=0、pos_sheet の実数を採る。
        print(f"\n担当外の口が 0（採用は実数なので画面は正しい）: {len(zeros_ok)}件")

    print(f"\n⚠ 採用値が 0（画面が 0 になる）: {len(zeros)}件")
    _show(zeros, show_gap=False)

    print(f"\n要確認の食い違い（{gap_pct}% 以上・既知の差を除く）: {len(conflicts)}件")
    _show(conflicts)

    if switches:
        print(
            "\n⚠ 出どころが切り替わった月があります: "
            + ", ".join(switches)
            + "\n  定義（税込/税抜・純売上/総売上）が違えば、その境目をまたぐ前年比が"
            "まるごとずれます。"
        )
    if conflicts or zeros or switches:
        print(f"::error::[出どころ] 切替 {len(switches)}件 / 要確認 {len(conflicts)}件 / "
              f"採用が0 {len(zeros)}件"
              f"（既知の差 {len(known)}件・担当外の0 {len(zeros_ok)}件は除く）")
        return 1
    print(f"\n出どころは一貫しています。"
          f"（既知の差 {len(known)}件・担当外の0 {len(zeros_ok)}件は除く）")
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

    # 月に範囲（"2025-09..2026-08"）を渡せる。業態変更やメニュー刷新の前後で
    # 売れ筋がどう入れ替わったかを1回で並べるため。
    span = _expand_months(month) if (".." in month or "," in month) else [month]
    y, m = int(span[0][:4]), int(span[0][5:7])
    d_from = _date(y, m, 1)
    ly, lm = int(span[-1][:4]), int(span[-1][5:7])
    d_to = _date(ly + (lm == 12), 1 if lm == 12 else lm + 1, 1)

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

    if len(span) > 1 and "rotation" in (items or ""):
        # ローテーション地図: 品目区分ごとに、各商品が「どの月に出たか」を presence 文字列で
        # 並べる（●=出た/·=無し）。売価0円の内訳（TOジェラートの風味等）も点数で拾うので、
        # 季節ローテ（毎月入れ替わる限定品）と定番(GM=全期間●)が一目で分かる。
        from ..model import METRIC_PRODUCT_QTY as _MQ
        from ..web.export import classify_category as _classify
        from ..web.export import load_store_categories as _loadcats
        allrules = _loadcats()
        # (code,name,month)->sales / qty、(code,name)->group（点数行の "NN:" 見出し）
        sales_m: dict[tuple, float] = {}
        qty_m: dict[tuple, float] = {}
        grp: dict[tuple, str] = {}
        for row in warehouse.aggregate(AggregateQuery(
                date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
                metrics=[METRIC_PRODUCT_SALES], store_codes=codes,
                group_by=("store_code", "date", "product_name"))):
            sales_m[(row["store_code"], row["product_name"], row["date"].strftime("%Y-%m"))] = row["value"]
        for row in warehouse.aggregate(AggregateQuery(
                date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
                metrics=[_MQ], store_codes=codes,
                group_by=("store_code", "date", "product_name", "product_category"))):
            key = (row["store_code"], row["product_name"], row["date"].strftime("%Y-%m"))
            qty_m[key] = qty_m.get(key, 0.0) + (row["value"] or 0)
            cat = row.get("product_category")
            if cat and re.match(r"^\s*\d+\s*[:：]", str(cat)):
                grp[(row["store_code"], row["product_name"])] = str(cat)
        names_by_store: dict[str, set] = {}
        for (code_, name_, _mm) in list(sales_m) + list(qty_m):
            names_by_store.setdefault(code_, set()).add(name_)
        print(f"=== ローテーション地図 {span[0]}〜{span[-1]}（●=出た月 / 区分別） ===")
        for st in master.active:
            if st.store_code not in codes or st.store_code not in names_by_store:
                continue
            rules = allrules.get(st.store_code)
            print(f"\n── {st.store_code} {st.store_name} ──  月: {' '.join(mm for mm in span)}")
            # 区分→[(name, presence, salesΣ, qtyΣ, active月数)]
            bycat: dict[str, list] = {}
            for name_ in names_by_store[st.store_code]:
                g = grp.get((st.store_code, name_))
                cat = _classify(name_, rules, g) if rules else "-"
                pres = "".join(
                    "●" if (sales_m.get((st.store_code, name_, mm), 0) or qty_m.get((st.store_code, name_, mm), 0))
                    else "·" for mm in span)
                sΣ = sum(sales_m.get((st.store_code, name_, mm), 0) for mm in span)
                qΣ = sum(qty_m.get((st.store_code, name_, mm), 0) for mm in span)
                active = pres.count("●")
                bycat.setdefault(cat, []).append((name_, pres, sΣ, qΣ, active))
            order = [c["name"] for c in (rules.get("categories", []) if rules else [])] + [(rules or {}).get("other", "その他")]
            for cat in order:
                rows_ = bycat.get(cat)
                if not rows_:
                    continue
                # 全期間●=定番(GM)は末尾、抜けのある=ローテ/限定を上に。
                rows_.sort(key=lambda r: (r[4], -r[2]))
                print(f"  【{cat}】")
                for name_, pres, sΣ, qΣ, active in rows_:
                    tag = "GM" if active == len(span) else "限定"
                    print(f"    {pres}  {tag:<3} {name_[:28]:<28} 売上Σ{int(sΣ):>9,} 点数Σ{int(qΣ):>6,} ({active}/{len(span)}月)")
        return 0
    if len(span) > 1:
        # 月ごとの売れ筋を並べる（推移を見る形）。店は1つに絞って使うのが前提。
        by_month: dict[str, list[tuple[str, float]]] = {}
        for row in warehouse.aggregate(
            AggregateQuery(
                date_from=d_from, date_to=d_to, grain=GRAIN_MONTH,
                metrics=[METRIC_PRODUCT_SALES], store_codes=codes,
                group_by=("store_code", "date", "product_name"),
            )
        ):
            key = f"{row['store_code']}|{row['date'].strftime('%Y-%m')}"
            by_month.setdefault(key, []).append((row["product_name"], row["value"]))
        print(f"=== ABC明細 {span[0]}〜{span[-1]} 月ごとの売れ筋 上位{top_n}品 ===")
        for st in master.active:
            if st.store_code not in codes:
                continue
            months_here = [m for m in span if f"{st.store_code}|{m}" in by_month]
            if not months_here:
                continue
            print(f"\n── {st.store_code} {st.store_name} ──")
            for mm in months_here:
                items_m = sorted(by_month[f"{st.store_code}|{mm}"], key=lambda x: x[1], reverse=True)
                total_m = sum(v for _, v in items_m)
                head = " / ".join(f"{n}({int(v):,})" for n, v in items_m[:top_n])
                print(f"  {mm}  上位計{int(total_m):,}  {head}")
        return 0

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
        METRIC_PRODUCT_COST,
        METRIC_PRODUCT_GROSS,
        METRIC_PRODUCT_QTY,
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

    num = lambda s: int(s.replace(",", ""))  # noqa: E731
    # 部門行の見分けはモジュール共通のヘルパを使う（probeと同じ判定にする）。
    _clean_dept = _abc_dept_match
    _countable_dept = _abc_countable_dept

    t0 = time.time()
    print(f"[ABC店] {code} {name} / 対象月 {len(months)}件 {months[0]}〜{months[-1]} 上位{top_n}品")
    # 前月と部門合計が完全一致したら、日付が反映されず同じグリッドを読んだ疑いが濃い。
    # （〜2千万円の合計が偶然そろうことは無い）。売上推移で同種の取りこぼしをやったので、
    # 「取れたつもり」を検知できるようにしておく。
    seen_totals: dict[tuple[str, int], str] = {}
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
            _sales_of = lambda p: p["ints"][_ABC_SALES] if len(p["ints"]) > _ABC_SALES else 0
            _qty_of = lambda p: p["ints"][_ABC_QTY] if len(p["ints"]) > _ABC_QTY else 0
            products.sort(key=_sales_of, reverse=True)
            # 売れ筋は売上上位 top_n。0円の選択商品（内訳）はメニュー分類で所属グループ付きに
            # 拾う（下の分類=メニュー）。全商品グリッドには本来これらは出ないが、店/日により
            # 混じることがあるので zero_flat に控え、メニューが取れなかったときだけ点数を
            # 焼くフォールバックにする（＝二重計上を避けつつ、内訳が丸ごと欠けるのを防ぐ）。
            picked = list(products[:top_n])
            picked_names = {p["name"] for p in picked}
            zero_flat = [
                p for p in products
                if _sales_of(p) <= 0 and _qty_of(p) > 0 and p["name"] not in picked_names
            ]
            n_prod = 0
            for prod in picked:
                ints = prod["ints"]
                sales = _sales_of(prod)
                qty = _qty_of(prod)
                if sales <= 0 and qty <= 0:
                    continue
                if sales > 0:
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
                # 販売点数（何個売れたか）。売価0円の商品でも点数があれば残す。
                if qty > 0:
                    collected.append(
                        ActualRow(
                            store_code=code,
                            date=rep_date,
                            grain=GRAIN_MONTH,
                            metric=METRIC_PRODUCT_QTY,
                            value=float(qty),
                            product_name=prod["name"][:80],
                            product_category=prod["rank"],
                            kind=KIND_FINAL,
                            source=source,
                            ingested_at=ingested_at,
                        )
                    )
                # 原価金額・粗利金額（売上のある商品のみ。0円内訳は原価も無いので採らない）。
                if sales > 0:
                    for _m, _i in (
                        (METRIC_PRODUCT_COST, _ABC_COST),
                        (METRIC_PRODUCT_GROSS, _ABC_GROSS),
                    ):
                        if len(ints) > _i:
                            collected.append(
                                ActualRow(
                                    store_code=code,
                                    date=rep_date,
                                    grain=GRAIN_MONTH,
                                    metric=_m,
                                    value=float(ints[_i]),
                                    product_name=prod["name"][:80],
                                    product_category=prod["rank"],
                                    kind=KIND_FINAL,
                                    source=source,
                                    ingested_at=ingested_at,
                                )
                            )
                n_prod += 1
            top = products[0]["name"][:16] if products else "-"

            # --- 分類=メニュー：0円の選択商品（内訳）を所属グループ付きで拾う ---
            # 全商品グリッドには内訳（テイクアウトジェラートの風味選択、サンデーの風味、
            # フレンチトーストのトッピング等＝売価0円）が出ない。メニュー分類だと
            # 「NN:区分名」見出しの下に選択商品が並ぶので、そこから点数のある内訳だけを、
            # 所属グループ（例 "20:テイクアウトジェラート"）を product_category に載せて
            # 点数(METRIC_PRODUCT_QTY)として取り込む。全商品で採れた商品は二重に数えない。
            menu_rows: list[dict] = []
            try:
                # グループ→メニューの順に踏むと、区分見出しの下の内訳まで描かれやすい
                # （全商品→メニューの直行だと 60/98 しか描けず取りこぼす）。
                if _abc_click_radio(session.page, "グループ"):
                    _abc_search_and_rows(session)
                if _abc_click_radio(session.page, "メニュー"):
                    _abc_search_and_rows(session)
                    # 行数が伸び止まるまで粘り、最大件数の抽出を採る。描画がまだ途中の
                    # ことがあるので、同数が3回続くまで（＝安定するまで）待つ。
                    stable = 0
                    for _ in range(12):
                        cur = _extract_product_grid_grouped(session)
                        if len(cur) > len(menu_rows):
                            menu_rows = cur
                            stable = 0
                        else:
                            stable += 1
                            if stable >= 3:
                                break
                        time.sleep(1.2)
            except Exception as e:  # noqa: BLE001
                print(f"[ABC店] {code} {month} メニュー内訳の取得に失敗（無害）: {e}")
                menu_rows = []
            # 取り込んだ内訳のうち区分見出しが付いた件数（付かない＝分類が効かないので警告）。
            n_grouped = sum(1 for p in menu_rows if p.get("group"))
            print(f"[ABC店] {code} {month} メニュー内訳 抽出{len(menu_rows)}行 / 区分見出し付き{n_grouped}行")
            n_break = 0
            for prod in menu_rows:
                nm = prod["name"]
                if nm in picked_names:  # 全商品で採った商品は二重計上しない
                    continue
                ints = prod["ints"]
                q = ints[_ABC_QTY] if len(ints) > _ABC_QTY else 0
                s = ints[_ABC_SALES] if len(ints) > _ABC_SALES else 0
                if q <= 0 and s <= 0:
                    continue
                grp = (prod.get("group") or "")[:60] or None
                if nm in ("ピスタチオ", "リッチミルク"):  # TOジェラートの+50円風味の所属確認用
                    print(f"[ABC店] {code} {month} 内訳確認 {nm} → group={grp} sales={s} qty={q}")
                # 売価のある内訳（例: テイクアウトジェラートの +50円 風味 ピスタチオ/リッチミルク）は
                # 売上も焼く。区分見出しは点数行の product_category に載せるので、売上行は
                # ランク欄を空にする（見出し文字列をランクとして表示しないため）。
                if s > 0:
                    collected.append(
                        ActualRow(
                            store_code=code,
                            date=rep_date,
                            grain=GRAIN_MONTH,
                            metric=METRIC_PRODUCT_SALES,
                            value=float(s),
                            product_name=nm[:80],
                            product_category=None,
                            kind=KIND_FINAL,
                            source=source,
                            ingested_at=ingested_at,
                        )
                    )
                if q > 0:
                    collected.append(
                        ActualRow(
                            store_code=code,
                            date=rep_date,
                            grain=GRAIN_MONTH,
                            metric=METRIC_PRODUCT_QTY,
                            value=float(q),
                            product_name=nm[:80],
                            product_category=grp,  # ランクではなく FW区分見出しを載せる
                            kind=KIND_FINAL,
                            source=source,
                            ingested_at=ingested_at,
                        )
                    )
                picked_names.add(nm)
                n_break += 1
            if n_break:
                print(f"[ABC店] {code} {month} 0円内訳を {n_break}件 追加取込（メニュー分類）")
            elif zero_flat:
                # メニュー分類が取れなかった。全商品に混じっていた0円の点数だけでも焼く
                # （所属グループは付かないので商品名で区分される。従来挙動のフォールバック）。
                for p in zero_flat:
                    q = _qty_of(p)
                    if q <= 0 or p["name"] in picked_names:
                        continue
                    collected.append(
                        ActualRow(
                            store_code=code,
                            date=rep_date,
                            grain=GRAIN_MONTH,
                            metric=METRIC_PRODUCT_QTY,
                            value=float(q),
                            product_name=p["name"][:80],
                            product_category=None,
                            kind=KIND_FINAL,
                            source=source,
                            ingested_at=ingested_at,
                        )
                    )
                    picked_names.add(p["name"])
                    n_break += 1
                if n_break:
                    print(f"[ABC店] {code} {month} 0円内訳を {n_break}件 取込（全商品フォールバック）")

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
            # 部門合計と商品合計の両方で「他の月と完全一致」を見る。日付が効かず
            # 同じグリッドを読むと、月をまたいで数字がそっくり同じになる。
            # 部門だけ見ていたら、1151 の 2025-02 と 2025-03 が商品まで丸ごと
            # 同一だったのを取りこぼした。
            prod_total = sum(
                p["ints"][_ABC_SALES] for p in products[:top_n] if len(p["ints"]) > _ABC_SALES
            )
            dup = seen_totals.get(("dept", dept_total)) if dept_total else None
            dup_p = seen_totals.get(("prod", prod_total)) if prod_total else None
            if dup or dup_p:
                suspect.append(f"{month}={dup or dup_p}")
            if dept_total and not dup:
                seen_totals[("dept", dept_total)] = month
            if prod_total and not dup_p:
                seen_totals[("prod", prod_total)] = month
            mark = " ⚠同額" if (dup or dup_p) else ""
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
        print(f"[ABC店] ⚠ {code} 合計が他の月と一致（部門または商品）: {suspect}")
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
    if suspect:
        # 月が違うのに合計が一致するのは、日付が効かず前月のグリッドを読んだ可能性。
        # 静かに緑で終わらせない。
        print(f"::error::[ABC店] {code} 同額の月あり（日付未反映の疑い）: {','.join(suspect)}")
        return 1
    if no_dept:
        print(f"::error::[ABC店] {code} 部門が取れなかった月: {','.join(no_dept)}")
        return 1
    return 0


def ingest_abc_stores(
    warehouse,
    master,
    *,
    artifacts: Path,
    stores: list[str],
    month: str | None = None,
    dry_run: bool = False,
    per_store_budget_minutes: float = 20.0,
) -> int:
    """複数店の店舗別月次ABCを、1店ずつ「新しいFWセッション」で順に取り込む。

    店舗切替を同一セッション内で繰り返すとグリッド再描画が止まる事象があるため
    （時間帯別で確認済み）、店ごとに fw_session を開き直す＝1店=1ログイン。
    冪等キーは source=fw_abc_<code> で店ごとに分かれるので、途中で1店落ちても
    取れた店はそのまま残り、その店だけ流し直せばよい。1店で例外が出ても止めずに
    次の店へ進む（1店の失敗で全店を巻き添えにしない）。

    stores は店コード or 店名の配列。month は "2024-01..2024-08,2026-09" 等をそのまま
    各店の ingest_abc_store に渡す。戻り値は 0=全店成功 / 1=要確認の店あり。
    """
    import sys as _sys

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    rc = 0
    ok: list[str] = []
    warn: list[str] = []
    total = len(stores)
    print(f"[ABC全店] 対象 {total}店 / 月 {month} / 1店=1ログイン")
    for idx, store in enumerate(stores, 1):
        print(f"[ABC全店] === {idx}/{total} 店『{store}』開始 ===")
        try:
            r = ingest_abc_store(
                warehouse,
                master,
                artifacts=artifacts,
                store=store,
                month=month,
                dry_run=dry_run,
                budget_minutes=per_store_budget_minutes,
            )
        except Exception as e:  # noqa: BLE001
            print(f"::warning::[ABC全店] 店『{store}』で例外（続行）: {e}")
            r = 1
        if r == 0:
            ok.append(store)
        else:
            warn.append(store)
            rc = 1
        print(f"[ABC全店] --- {idx}/{total} 店『{store}』終了 rc={r} ---")
    print(f"[ABC全店] 完了 成功{len(ok)}店 / 要確認{len(warn)}店 / 全{total}店")
    if warn:
        print(f"[ABC全店] 要確認の店: {','.join(warn)}")
    return rc


def _abc_grouped_rows_stable(session) -> list[dict]:
    """今の日付レンジで、グループ→メニューの順に踏んでから内訳を全件（グループ付き）で採る。
    行数が3回連続で伸び止まるまで粘る（メニューは全商品より行数が多く描画が遅れるため）。"""
    if _abc_click_radio(session.page, "全商品"):
        _abc_search_and_rows(session)
    if _abc_click_radio(session.page, "グループ"):
        _abc_search_and_rows(session)
    rows: list[dict] = []
    if _abc_click_radio(session.page, "メニュー"):
        _abc_search_and_rows(session)
        stable = 0
        for _ in range(12):
            cur = _extract_product_grid_grouped(session)
            if len(cur) > len(rows):
                rows = cur
                stable = 0
            else:
                stable += 1
                if stable >= 3:
                    break
            time.sleep(1.2)
    return rows


SCHEDULE_PATH = Path(__file__).resolve().parents[2] / "config" / "schedule.yaml"


def ingest_abc_campaigns(
    warehouse,
    master,
    *,
    artifacts: Path,
    store: str | None = None,
    only_ids: str | None = None,
    dry_run: bool = False,
    budget_minutes: float = 25.0,
    skip_existing: bool = True,
) -> int:
    """登録済み販促の [start,end] レンジでABCを引き、その施策の“販売時期”実績（商品別 売上/点数）を
    施策id 紐づけで焼く。丸ごとの月ではなく、実際の販売期間の数字を施策詳細に出すための土台。

    保存: source=fw_abc_camp / grain=day / date=開始日 / product_category=施策id。
    月次(grain=month)とは分離されるので既存集計に影響しない。export が施策idごとに読み直す。
    対象は schedule.yaml のうち start と end があり bucket か items を持つ施策（店は解決できるもの）。
    """
    import sys as _sys
    from datetime import date as _date
    from datetime import datetime, timezone

    import yaml as _yaml

    import calendar as _cal

    from ..db.warehouse import AggregateQuery
    from ..model import (
        GRAIN_DAY,
        GRAIN_MONTH,
        KIND_FINAL,
        METRIC_PRODUCT_COST,
        METRIC_PRODUCT_GROSS,
        METRIC_PRODUCT_QTY,
        METRIC_PRODUCT_SALES,
        ActualRow,
    )
    from ..web.export import classify_category as _classify
    from ..web.export import load_store_categories as _loadcats

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    data = _yaml.safe_load(SCHEDULE_PATH.read_text(encoding="utf-8")) or {}
    allrules = _loadcats()
    # "" / all / *（および fw.yml の既定値 冷やし鶏）は「全施策」。それ以外はid絞り。
    raw_ids = (only_ids or "").strip()
    want_ids = (
        set()
        if raw_ids in ("", "all", "*", "冷やし鶏")
        else {s.strip() for s in raw_ids.split(",") if s.strip()}
    )

    def _resolve(code_or_name: str):
        try:
            return master.by_code(code_or_name)
        except Exception:  # noqa: BLE001
            return master.find_by_name(code_or_name) or next(
                (s for s in master.active if code_or_name in s.store_name), None
            )

    # 施策→対象店（単店に絞る。複数店の施策はそれぞれ別レンジ取込になるが、ルクア系は単店）。
    jobs: dict[str, list[dict]] = {}  # store_code -> [campaign,...]
    store_name: dict[str, str] = {}
    for c in data.get("campaigns") or []:
        cid = c.get("id")
        if want_ids and cid not in want_ids:
            continue
        start, end = c.get("start"), c.get("end")
        if not start or not end:
            continue
        if not (c.get("bucket") or c.get("items")):
            continue
        # 通年枠（例 ジェラートの常設枠 2024-2027）は discrete な“回”ではないので対象外。
        # 販売時期実績は個別の回（〜数ヶ月）に限る。
        try:
            _sd = _date(int(start[:4]), int(start[5:7]), int(start[8:10]))
            _ed = _date(int(end[:4]), int(end[5:7]), int(end[8:10]))
            if (_ed - _sd).days > 200:
                continue
        except Exception:  # noqa: BLE001
            pass
        for s in c.get("stores") or []:
            st = _resolve(str(s))
            if not st or not st.active:
                continue
            if store:
                sel = _resolve(store)
                if not sel or sel.store_code != st.store_code:
                    continue
            jobs.setdefault(st.store_code, []).append(c)
            store_name[st.store_code] = st.store_name

    if not jobs:
        print("[施策ABC] 対象施策がありません（start/end と bucket/items が要る）。終了。")
        return 0

    source = "fw_abc_camp"
    ingested_at = datetime.now(timezone.utc)
    t0 = time.time()
    total = 0
    today = _date.today()

    # 施策を (店, 施策) の平坦リストにする。店ごとにまとめ、その中は開始日順。
    flat: list[tuple[str, str, object, dict]] = []
    for code, camps in jobs.items():
        for c in sorted(camps, key=lambda x: str(x.get("start"))):
            flat.append((code, store_name[code], allrules.get(code), c))

    # すでに取込済みの施策id（過去の“回”は数字が確定するので二度取りしない）。
    # end が未来（進行中）の回は毎回取り直す。best-effort: 読めなければ全件取る。
    have_ids: set[str] = set()
    if skip_existing and flat:
        try:
            starts = [c["start"] for (_, _, _, c) in flat]
            dfrom = min(starts)
            dto = max(starts)
            q = AggregateQuery(
                date_from=_date(int(dfrom[:4]), int(dfrom[5:7]), int(dfrom[8:10])),
                date_to=_date(int(dto[:4]), int(dto[5:7]), int(dto[8:10])),
                grain=GRAIN_DAY,
                metrics=[METRIC_PRODUCT_SALES],
                sources=[source],
                store_codes=list(jobs.keys()),
                group_by=["product_category"],
            )
            for r in warehouse.aggregate(q):
                pc = r.get("product_category")
                if pc:
                    have_ids.add(str(pc))
        except Exception as e:  # noqa: BLE001
            print(f"[施策ABC] 既取込チェックは省略（{e}）。全件取り直す。")

    def _is_done(cid: str, end: str) -> bool:
        if cid not in have_ids:
            return False
        try:
            ed = _date(int(end[:4]), int(end[5:7]), int(end[8:10]))
        except Exception:  # noqa: BLE001
            return False
        return ed < today  # 終了済み（過去の回）だけスキップ。進行中は取り直す。

    todo = [(code, name, rules, c) for (code, name, rules, c) in flat
            if not _is_done(c["id"], c["end"])]
    skipped = len(flat) - len(todo)
    print(
        f"[施策ABC] 対象 {len(flat)}施策 / {len(jobs)}店"
        + (f"（うち取込済みで省略 {skipped}件、今回 {len(todo)}件）" if skipped else "")
    )
    if not todo:
        print("[施策ABC] 取込対象なし（すべて取込済み）。終了。")
        return 0

    if not dry_run:
        warehouse.ensure_schema()

    def _ym_minus(ym: str, k: int) -> str:
        idx = int(ym[:4]) * 12 + (int(ym[5:7]) - 1) - k
        return f"{idx // 12:04d}-{idx % 12 + 1:02d}"

    def _limited_checker(code_: str, start_ym: str):
        """おすすめジェラートと同じ半年ルック: 直近6か月ぶんの月次商品で毎月連続して
        出ていれば定番(GM)、どこかに抜けがあれば限定(=その回の販促商品)。月次履歴が
        浅い(4か月未満)ときは限定側に倒す（安全側＝拾う）。判定不能なら全部拾う。"""
        months = [_ym_minus(start_ym, k) for k in range(6)]
        lo, hi = min(months), max(months)
        try:
            rows_ = warehouse.aggregate(
                AggregateQuery(
                    date_from=_date(int(lo[:4]), int(lo[5:7]), 1),
                    date_to=_date(
                        int(hi[:4]), int(hi[5:7]), _cal.monthrange(int(hi[:4]), int(hi[5:7]))[1]
                    ),
                    grain=GRAIN_MONTH,
                    metrics=[METRIC_PRODUCT_SALES],
                    store_codes=[code_],
                    group_by=["date", "product_name"],
                )
            )
        except Exception:  # noqa: BLE001
            return lambda _n: True
        per_month: dict[str, set] = {}
        for r in rows_:
            per_month.setdefault(r["date"].strftime("%Y-%m"), set()).add(r["product_name"])
        data_months = [mm for mm in months if per_month.get(mm)]
        if len(data_months) < 4:
            return lambda _n: True
        return lambda nm: any(nm not in per_month.get(mm, set()) for mm in data_months)

    with fw_session(artifacts) as session:
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass
        deadline = t0 + budget_minutes * 60
        for code, name, rules, c in todo:
            if time.time() > deadline:
                print(f"[施策ABC] 時間切れ（{budget_minutes:.0f}分）。残りは次回。")
                break
            cid = c["id"]
            d_from = c["start"].replace("-", "/")
            d_to = c["end"].replace("-", "/")
            rep = _date(int(c["start"][:4]), int(c["start"][5:7]), int(c["start"][8:10]))
            # ── 施策ごとに ABC 画面を開き直す（＝毎回まっさらな状態から）。──
            # 同じ画面を使い回して日付+検索だけ変えると2施策目以降でグリッドが空になる
            # 事象があったため、1施策=1ログイン相当の“フレッシュ導線”で確実に引く。
            # （店選択も毎回やり直すので、単店・複数店どちらでも状態が混ざらない。）
            try:
                _open_abc(session)
                _select_date_preset(session, _ABC_PRESET_LASTMONTH)
                _set_date_range(session, d_from, d_to)
                hit = _abc_open_store_modal_and_select_one(session, name)
                if hit is None:
                    print(f"[施策ABC] 店舗選択に失敗（{name} / {cid}）。飛ばす。")
                    continue
                _abc_click_radio(session.page, "全商品")
                _abc_search_and_rows(session)
                rows = _extract_product_grid(session)
            except Exception as e:  # noqa: BLE001
                print(f"[施策ABC] {code} {cid} 取得中に例外: {e}。飛ばす。")
                continue
            bucket = c.get("bucket")
            kws = [k for k in (c.get("items") or []) if k]

            def _val_of(prod):
                ints = prod["ints"]
                sales = ints[_ABC_SALES] if len(ints) > _ABC_SALES else 0
                qty = ints[_ABC_QTY] if len(ints) > _ABC_QTY else 0
                # 原価金額・粗利金額（無ければ None＝データ無しとして書かない）
                cost = ints[_ABC_COST] if len(ints) > _ABC_COST else None
                gross = ints[_ABC_GROSS] if len(ints) > _ABC_GROSS else None
                return sales, qty, cost, gross

            # 対象商品だけ残す: items があれば商品名一致、無ければ bucket(区分)一致。
            picked: list[tuple[str, int, int, int | None, int | None]] = []
            for prod in rows:
                nm = prod["name"]
                sales, qty, cost, gross = _val_of(prod)
                if sales <= 0 and qty <= 0:
                    continue
                if kws:
                    if not any(k in nm for k in kws):
                        continue
                elif bucket:
                    if not rules or _classify(nm, rules, prod.get("group")) != bucket:
                        continue
                picked.append((nm, sales, qty, cost, gross))

            # items 指定なのに1件も当たらない回（キーワード表記ゆれ／未記入）は、
            # おすすめジェラートと同じ「半年ルック」で自動救済：販売期間中に出た bucket 商品の
            # うち“限定(直近6か月で抜けのある新顔)”を拾う。定番(GM)は拾わない。
            fallback = 0
            if kws and not picked and bucket and rules:
                is_lim = _limited_checker(code, c["start"][:7])
                cands: list[tuple[str, int, int, int | None, int | None]] = []
                for prod in rows:
                    nm = prod["name"]
                    sales, qty, cost, gross = _val_of(prod)
                    if sales <= 0 and qty <= 0:
                        continue
                    if _classify(nm, rules, prod.get("group")) != bucket:
                        continue
                    if is_lim(nm):
                        cands.append((nm, sales, qty, cost, gross))
                # 拾いすぎ防止：売上上位5品までに絞る（その回の主役だけ残す）。
                cands.sort(key=lambda x: x[1], reverse=True)
                for nm, sales, qty, cost, gross in cands[:5]:
                    picked.append((nm, sales, qty, cost, gross))
                    fallback += 1

            n = 0
            crows: list[ActualRow] = []
            for nm, sales, qty, cost, gross in picked:
                # 売上・点数は正のときだけ、原価は正のときだけ、粗利は売上が正なら符号問わず記録。
                emit: list[tuple[str, float]] = []
                if sales > 0:
                    emit.append((METRIC_PRODUCT_SALES, float(sales)))
                if qty > 0:
                    emit.append((METRIC_PRODUCT_QTY, float(qty)))
                if sales > 0 and cost is not None and cost > 0:
                    emit.append((METRIC_PRODUCT_COST, float(cost)))
                if sales > 0 and gross is not None:
                    emit.append((METRIC_PRODUCT_GROSS, float(gross)))
                for metric, val in emit:
                    crows.append(
                        ActualRow(
                            store_code=code,
                            date=rep,
                            grain=GRAIN_DAY,
                            metric=metric,
                            value=val,
                            product_name=nm[:80],
                            product_category=cid,  # 施策idで紐づける
                            kind=KIND_FINAL,
                            source=source,
                            ingested_at=ingested_at,
                        )
                    )
                n += 1
            tag = f"（半年ルック救済 {fallback}品）" if fallback else ""
            print(f"[施策ABC] {code} {cid} {c['start']}〜{c['end']} 対象{n}品 抽出{len(rows)}行{tag}")
            # 施策ごとにその場で書く。長時間スクレイプ中に Neon 接続が idle で切れて
            # 最後にまとめて書くと失敗するため（SSL closed）。切れていたら張り直して1回再試行。
            if not dry_run and crows:
                try:
                    total += warehouse.replace_actuals(crows, scope_stores=True, scope_metrics=True)
                except Exception as e:  # noqa: BLE001
                    print(f"[施策ABC] 書込リトライ（接続張り直し）: {e}")
                    try:
                        warehouse.close()
                    except Exception:  # noqa: BLE001
                        pass
                    total += warehouse.replace_actuals(crows, scope_stores=True, scope_metrics=True)
    print(f"[施策ABC] warehouse へ {total} 件 書き込みました（source={source}）")
    return 0


def find_gelato_switches(
    warehouse,
    master,
    *,
    artifacts: Path,
    store: str = "1160",
    from_month: str | None = None,
    to_month: str | None = None,
) -> int:
    """schedule.yaml の c<店>-gelato-* 各回について、販促フレーバーが最初に売れた日を
    FWのメニュー内訳(0円/日別)を当てて確定する。TOジェラート風味は0円で全商品に出ないため
    メニュー内訳を日レンジで引いて判定。まず「前月末に無い＆当月初旬に有る＝月初切替」を
    2プローブで確認し、月初でなければ二分探索で初売日を特定。結果を印字（scheduleは手で更新）。
    from_month/to_month（YYYY-MM）で対象回を絞れる＝実行を年ごと等に分割できる。
    """
    import calendar as _cal
    import sys as _sys
    from datetime import date as _date
    from datetime import timedelta as _td

    import yaml as _yaml

    try:
        _sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    data = _yaml.safe_load(SCHEDULE_PATH.read_text(encoding="utf-8")) or {}
    pre = f"c{store}-gelato-"
    ents = [
        c for c in (data.get("campaigns") or [])
        if str(c.get("id", "")).startswith(pre) and c.get("items")
    ]
    mkey = lambda c: str(c["start"])[:7]  # noqa: E731
    if from_month:
        ents = [c for c in ents if mkey(c) >= from_month]
    if to_month:
        ents = [c for c in ents if mkey(c) <= to_month]
    ents.sort(key=lambda c: str(c["start"]))
    if not ents:
        print("[ジェラート切替] 対象なし。終了。")
        return 0

    try:
        st = master.by_code(store)
        store_name = st.store_name
    except Exception:  # noqa: BLE001
        store_name = store

    def D(s: str) -> "_date":
        return _date(int(s[:4]), int(s[5:7]), int(s[8:10]))

    def S(d) -> str:
        return d.strftime("%Y/%m/%d")

    def monthend(y: int, m: int):
        return _date(y, m, _cal.monthrange(y, m)[1])

    print(f"[ジェラート切替] 対象 {len(ents)}回 / 店 {store_name}")
    with fw_session(artifacts) as session:
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass

        def present(d0, d1, kws):
            """[d0,d1] のメニュー内訳に kws のどれかが数量>0 で出るか（フレッシュ導線）。"""
            try:
                _open_abc(session)
                _select_date_preset(session, _ABC_PRESET_LASTMONTH)
                _set_date_range(session, S(d0), S(d1))
                if _abc_open_store_modal_and_select_one(session, store_name) is None:
                    print("  [警告] 店舗選択失敗")
                    return None
                rows = _abc_grouped_rows_stable(session)
            except Exception as e:  # noqa: BLE001
                print(f"  [警告] 取得例外: {e}")
                return None
            for r in rows:
                nm = r.get("name", "")
                ints = r.get("ints", [])
                q = ints[_ABC_QTY] if len(ints) > _ABC_QTY else 0
                if q > 0 and any(k in nm for k in kws):
                    return True
            return False

        for c in ents:
            cid = c["id"]
            kws = [k for k in (c.get("items") or []) if k]
            sd = D(c["start"])
            y, m = sd.year, sd.month
            m01 = _date(y, m, 1)
            mend = monthend(y, m)
            py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
            p20 = _date(py, pm, 20)
            pend = monthend(py, pm)

            pa = present(p20, pend, kws)
            if pa is None:
                print(f"[ジェラート切替] {cid} 判定不能（プローブ失敗）")
                continue
            prev_absent = not pa
            early = present(m01, _date(y, m, 3), kws)
            if early is None:
                print(f"[ジェラート切替] {cid} 判定不能（プローブ失敗）")
                continue
            if prev_absent and early:
                print(f"[ジェラート切替] {cid} 切替日 = {y:04d}-{m:02d}-01 ✅月初確定（前月末なし・初旬あり）")
                continue

            # 二分探索: base から [lo..hi] で present([base..D]) が True になる最小 D＝初売日。
            # base は探索の起点（固定）、[lo,hi] は初売日の候補域。
            # present(m01..m03)=False が確定しているので、月初なしケースは lo を m04 から始めて
            # プローブを節約する（base は m01 のまま＝窓の起点はずらさない）。
            if prev_absent:
                base, hi = m01, mend
                lo = _date(y, m, 4) if early is False else m01
            else:
                base, lo, hi = _date(py, pm, 1), _date(py, pm, 1), mend
            probes = 0
            while lo < hi and probes < 8:
                mid = lo + (hi - lo) // 2
                r = present(base, mid, kws)
                probes += 1
                if r is None:
                    break
                if r:
                    hi = mid
                else:
                    lo = mid + _td(days=1)
            note = "前月内" if not prev_absent else ("月途中" if lo > m01 else "月初")
            print(f"[ジェラート切替] {cid} 初売日 = {lo.isoformat()} 🔎探索（{note}・{probes}プローブ）")
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
        # TOジェラート内訳（0円の選択商品）が各分類でどう並ぶか掴むための診断。
        # 商品名にこれらを含む行は全行ダンプ（切り詰めない）。売価0×点数>0の
        # 「内訳（選択商品）」行も分類ごとに全件出し、取込元(全商品)に含まれるかを見る。
        KW = ("ジェラート", "TO", "サンデー", "シングル", "ダブル", "トリプル",
              "スクープ", "フレーバー", "選択")
        # グリッドが「部門」に切り替わったのか、全商品のままなのかを1行で判る形にする。
        dump_n = 40
        summary: list[str] = []
        for level in levels:
            ok = _abc_click_radio(session.page, level)
            print(f"[ABCprobe] 分類ラジオ『{level}』クリック={ok}")
            rows = _abc_search_and_rows(session)
            n_dept = sum(1 for c in rows if _abc_countable_dept(c))
            verdict = (
                f"数えられる{level}行 {n_dept}件"
                if n_dept
                else f"⚠ 数えられる{level}行が0件（グリッドが全商品のままの疑い）"
            )
            summary.append(f"  分類={level:<6} {verdict} / 視覚行 計{len(rows)} / ラジオclick={ok}")
            print(f"[ABCprobe] === 分類={level} 視覚行（先頭{dump_n}/計{len(rows)}） ===")
            for cells in rows[:dump_n]:
                print("   ", " | ".join(cells[:14]))
            # 0円内訳（選択商品）: グループ付きで抽出し、売価0×点数>0 を所属グループ付きで全件。
            # 素の風味名（ベリーマニア等）がどの区分見出しにぶら下がるかをここで確定する。
            gp = _extract_product_grid_grouped(session)
            zero_break = [
                (p["group"], p["name"], p["ints"][1])
                for p in gp
                if len(p["ints"]) >= 3 and p["ints"][0] == 0 and p["ints"][2] == 0 and p["ints"][1] > 0
            ]
            if zero_break:
                tot = sum(q for _, _, q in zero_break)
                print(f"[ABCprobe] --- {level}: 0円内訳(選択商品) {len(zero_break)}件 / 点数合計 {tot} ---")
                for grp, nm, q in zero_break:
                    print(f"    0円| [{grp}] {nm} | 点数 {q}")
            hits = [c for c in rows if any(k in " ".join(c) for k in KW)]
            if hits:
                print(f"[ABCprobe] --- {level}: ジェラート/サンデー関連 {len(hits)}行（全件） ---")
                for cells in hits:
                    print("   *", " | ".join(cells[:14]))
        # 判定はまとめて最後にもう一度出す。ログの末尾だけ見れば結論が分かるように。
        print(f"[ABCprobe] === 判定まとめ {store} {d_from}〜{d_to} ===")
        for line in summary:
            print(line)
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


# ── 分析用コード設定（マスタ管理→販売マスタ）─────────────────────────────
#
# 店長会資料は分析用コード1〜28の上に乗っている。アラカルトの数字が
#   アラカルト客数 = 総客数 －宴会 －ランチ －食べ飲み －ツアー －単品飲み放題 －テイクアウト
# という**引き算**で出るので、コードを付け忘れたメニューは消えずに
# **アラカルトに紛れ込む**。どこも空欄にならないので資料は完成して見える。
#
# ⚠️ この画面は**マスタを書き換えられる**。`登録` と `CSV取込` には
#    絶対に触らないこと。押してよいのは `CSV出力` だけ。
_NOTES: list[str] = []


ANALYSIS_CODE_MENU = ("マスタ管理", "販売マスタ", "分析用コード設定")


def probe_analysis_codes(artifacts: Path, master, store: str = "") -> int:
    """分析用コード設定の画面を1店だけ見る診断。DBにもFWにも書き込まない。

    見たいのは3つ。
      ① 130件ある店舗コンボのうち、うちの店がどの value で引けるか
      ② 店を選んだあと、何をすればグリッドに中身が出るのか
      ③ `CSV出力` が本当にダウンロードとして落ちてくるか、列は何か
    """
    from .fw_budget import _combo_options, _select_combo

    key = (store or "").strip()
    with fw_session(artifacts) as session:
        _watch_page(session)
        options = _open_and_wait_combo(session)
        print(f"[分析コード] 店舗コンボ {len(options)}件")
        if not options:
            print("::error::[分析コード] 店舗コンボが空のままでした")
            _dump_screen(session, "コンボが空")
            return 1

        # うちの店だけに絞る。FWのコードは0埋めなので lstrip して突き合わせる。
        active = {s.store_code: s for s in master.active}
        mine = []
        for opt in options:
            code = opt["value"].lstrip("0")
            st = active.get(code) or master.find_by_name(opt["name"])
            if st and st.active:
                mine.append((opt, st))
        print(f"[分析コード] マスタと一致した稼働店 {len(mine)}件")
        for opt, st in mine[:30]:
            print(f"    {opt['value']}\t{st.store_code}\t{st.store_name}")
        if not mine:
            print("::error::[分析コード] うちの店が1件も引けませんでした")
            return 1

        target = mine[0]
        if key:
            hit = [(o, s) for o, s in mine
                   if s.store_code == key.lstrip("0") or key in s.store_name]
            if not hit:
                print(f"::error::[分析コード] 店舗が見つかりません: {store}")
                return 1
            target = hit[0]
        opt, st = target
        print(f"[分析コード] 対象: {opt['value']} {st.store_code} {st.store_name}")

        if not _select_combo(session, opt["value"]):
            print("[分析コード] 店舗コンボから選べませんでした")
            _dump_screen(session, "コンボで選べなかった")
            return 1

        if not _commit_store(session):
            print("::error::[分析コード] 店を選んでも中身が出ませんでした")
            _dump_screen(session, "中身が出ないまま")
            return 1
        print("[分析コード] グリッドに中身が出た")

        data = _download_analysis_csv(session)
        if data is None:
            # ⚠️ 画面ダンプのあとに結論を置く。実行環境ではログの**末尾しか
            #    読めない**ので、先に出すとダンプに押し流されて見えない。
            print("")
            print("=== 結論 ===")
            print("  経路とグリッド表示までは通った。CSVのダウンロードだけが未完。")
            for line in _NOTES[-25:]:
                print(f"  {line}")
            return 1
        print(f"[分析コード] CSVを取得: {len(data)} バイト")
        _describe_analysis_csv(data)
        session.snapshot("analysis_probe_end")
    print(f"\n成果物: {artifacts}")
    return 0


def _open_and_wait_combo(session, tries: int = 3) -> list[dict]:
    """分析用コード設定を開き、店舗コンボが埋まるまで待つ。駄目なら開き直す。

    待つだけでは足りなかった。60秒待っても 0件のまま終わったランがある
    （直前のランの4分後で、FW側のセッションの影響とみられる）。
    **開き直して取り直す**。月次で回すものなので、1回の空振りで
    「対象の店が1件もありません」と言って終わるのは困る。
    """
    options: list[dict] = []
    for i in range(1, tries + 1):
        if i > 1:
            print(f"[分析コード] 店舗コンボが空。メニューを開き直す（{i}回目）")
            try:
                session.click_text("TOP")
                session.page.wait_for_timeout(3000)
            except Exception:  # noqa: BLE001
                pass
        _open_menu(session, ANALYSIS_CODE_MENU)
        options = _wait_combo_options(session, timeout=45.0)
        if options:
            return options
    return options


def _wait_combo_options(session, timeout: float = 60.0) -> list[dict]:
    """店舗コンボの選択肢が入るまで待ってから返す。

    メニューを開いた直後に読むと**空のことがある**（実測で 130件 → 0件 と
    ランによって割れた）。空で先に進むと「うちの店が1件も無い」という
    見当違いの結論になる。選択肢が出るまで待つ。
    """
    start = time.monotonic()
    options: list[dict] = []
    while time.monotonic() - start < timeout:
        options = _combo_options(session)
        if options:
            if time.monotonic() - start > 1.0:
                print(f"[分析コード] コンボの選択肢が出るまで {time.monotonic() - start:.0f}秒")
            return options
        time.sleep(1.0)
    return options


def _commit_store(session, timeout: float = 60.0) -> bool:
    """店を選んだあと、グリッドに中身が出るまで押して待つ。

    コンボに値を入れただけでは読み込みが走らない（欄には店名が入るのに
    『データなし』のまま）。**『店舗』はボタンではなく入力欄のラベル**で、
    これを押すと読み込みが走る、というのが実測。

    ⚠️ 完全一致で探す。この画面には『登録』が隣（x=990 y=112）にあり、
       押すとマスタを書き換えてしまう。
    """
    clicked = _click_exact(session, "店舗")
    if not clicked:
        print("[分析コード] 『店舗』が押せませんでした")
        return False
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if not _grid_is_empty(session):
            print(f"[分析コード] 『店舗』を押して {time.monotonic() - start:.0f}秒で出た")
            return True
        time.sleep(1.0)
    return False


def _download_analysis_csv(session, scope: str = "画面表示") -> bytes | None:
    """『CSV出力』→ ダイアログで範囲を選ぶ → 『ダウンロード』。

    `CSV出力` はその場で落ちてこない。**ダイアログが開く**（実測）。

        画面表示 / 全店 / 店舗選択   ←ラジオ
        ダウンロード | キャンセル

    既定は `画面表示`（いま出している1店ぶん）。`全店` は130店ぶんで、
    押しても5分でCSVが来なかった（押下自体は効いていて、ボタンが
    無効化される＝処理は走っている）。**まず軽いほうで経路を通す。**
    1店ずつでも、ログインは1回で23店まわせる。

    ⚠️ 押してよいのは `CSV出力` と `ダウンロード` だけ。すぐ隣に
       `CSV取込`(x=530) と `キャンセル`(x=652) があるので**完全一致**で探す。
       部分一致で `CSV出力` を探すと `CSV取込` に当たり、マスタを壊す。
    """
    if not (_click_real(session, "CSV出力") or _click_exact(session, "CSV出力")):
        print("[分析コード] 『CSV出力』が押せませんでした")
        return None
    # ダイアログが出るまで待つ（『ダウンロード』が見えたら出たとみなす）
    start = time.monotonic()
    while time.monotonic() - start < 30.0:
        if _has_exact(session, "ダウンロード"):
            break
        time.sleep(0.5)
    else:
        print("[分析コード] CSV出力のダイアログが出ませんでした")
        _dump_screen(session, "ダイアログが出ない")
        return None

    # ⚠️ JS の el.click() では**3つとも checked=false のまま**だった（実測）。
    #    合成イベントは isTrusted=false で、フレームワークが無視する。
    #    Playwright の本物のクリックで押し、選べたかを読み直す。
    picked = _check_radio_real(session, scope)
    _NOTES.append(f"範囲『{scope}』のラジオ: "
                  + ("見つからない" if picked is None else f"checked={picked}"))
    print(_NOTES[-1])
    if picked is False and _click_real(session, scope):
        picked = _check_radio_real(session, scope)
        _NOTES.append(f"ラベル経由で押し直した: checked={picked}")
        print(_NOTES[-1])
    time.sleep(0.8)
    states = session.page.evaluate(
        r"""() => [...document.querySelectorAll('input[type=radio]')]
              .filter(e => e.offsetParent)
              .map(e => `${e.value}=${e.checked}`)"""
    )
    _NOTES.append(f"ラジオの状態: {states}")
    print(_NOTES[-1])

    # ブラウザでは普通にCSVが落ちる（利用者に確認済み）。つまり押し方の問題。
    # 当て方を3通り順に試し、**どれが効いたかを残す**。効いた1つだけを
    # 本番に残せるように、毎回この記録を見る。
    #
    # 1) role=button で名前指定（いちばん素直）
    # 2) 画面に見えている座標を直接クリック（当たる場所が確実）
    # 3) JSのclick（合成イベント。効かない実績があるが最後の砦）
    #
    # ⚠️ 3通りとも『ダウンロード』の完全一致だけを狙う。すぐ隣に
    #    『キャンセル』(x=652) がある。
    # ⚠️ **待ちを縮めたのは私の検証ミス。** ラジオが選べるようになったのと
    #    同じ回に 180秒→45秒 に縮めたので、「全店が選ばれた状態で長く待つ」
    #    を一度も試していなかった。130店ぶんの生成に時間がかかるだけ、
    #    という可能性が残っている。ここは長く待つ。
    # ⚠️ **『ダウンロード』は disabled のことがある**（実測で disabled=True）。
    #    押せないボタンを押していたので何も起きなかった。覆われてもいないし
    #    座標も合っていた。有効になるまで待つ。画面には読み込み中を示す
    #    `IMG.waiting` も出ていたので、処理が終わるのを待つのが筋。
    if not _wait_enabled(session, "ダウンロード"):
        _NOTES.append("『ダウンロード』が有効にならなかった")
        print(_NOTES[-1])
        _dump_screen(session, "ダウンロードが有効にならない")
        return None

    try:
        with session.page.expect_download(timeout=300000) as dl:
            if not (_click_role_button(session, "ダウンロード")
                    or _click_at_text(session, "ダウンロード")
                    or _click_exact(session, "ダウンロード")):
                raise RuntimeError("『ダウンロード』が押せなかった")
        data = open(dl.value.path(), "rb").read()
        _NOTES.append(f"✅ 取得できた / 名前={dl.value.suggested_filename} / {len(data)}バイト")
        print(_NOTES[-1])
        return data
    except Exception as e:  # noqa: BLE001
        _NOTES.append(f"5分待っても来なかった: {type(e).__name__}")
        print(_NOTES[-1])

    # 押しても何も起きないなら、**そのボタンの正体をHTMLで見る**。
    # disabled なのか、別の要素に覆われているのか、onclick が付いているのか。
    # ここまで一度も見ていなかった。
    html = session.page.evaluate(
        r"""() => {
        const out = [];
        const norm = s => (s || '').replace(/\s/g, '');
        for (const el of document.querySelectorAll('button, a, input, div[role=button]')) {
            if (!el.offsetParent) continue;
            if (!['ダウンロード', 'キャンセル', '全店'].includes(norm(el.innerText || el.value))) continue;
            const r = el.getBoundingClientRect();
            const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
            out.push({
                html: el.outerHTML.replace(/\s+/g, ' ').slice(0, 300),
                disabled: !!el.disabled,
                onclick: !!el.onclick,
                pe: getComputedStyle(el).pointerEvents,
                // その座標で実際に前面にいる要素（覆われていないか）
                topmost: top ? (top.tagName + '.' + (top.className || '').toString().slice(0, 40)) : null,
                sameEl: top === el || (top && el.contains(top)),
            });
        }
        return out;
    }"""
    )
    print("――― ボタンの正体 ―――")
    for h in html:
        print(f"  disabled={h['disabled']} onclick={h['onclick']} pointer-events={h['pe']}")
        print(f"  前面の要素={h['topmost']} 自分自身か={h['sameEl']}")
        print(f"  {h['html']}")
    _NOTES.append(f"ボタンの正体: {[{k: v for k, v in h.items() if k != 'html'} for h in html]}")

def _watch_page(session) -> None:
    """画面が出す合図を拾う。**押したのに何も起きない**の原因を掴むため。

    Playwright は `alert` / `confirm` を既定で自動的に却下する。確認が
    出ていれば黙って消されて処理が止まる。受け入れる側に倒す（読み取りの
    画面で、押しているのは『ダウンロード』だけなので「はい」で困らない）。
    あわせてコンソールのエラー・失敗した通信・download事象も記録する。
    """
    page = session.page

    def _on_dialog(d):
        _NOTES.append(f"画面の確認ダイアログ: {d.type} / {d.message[:80]!r} → 受け入れる")
        print(_NOTES[-1])
        try:
            d.accept()
        except Exception:  # noqa: BLE001
            pass

    def _on_console(m):
        if m.type in ("error", "warning"):
            _NOTES.append(f"コンソール {m.type}: {m.text[:120]}")

    page.on("dialog", _on_dialog)
    page.on("console", _on_console)
    page.on("requestfailed", lambda r: _NOTES.append(f"通信が失敗: {r.url[:90]}"))
    page.on("download", lambda d: _NOTES.append(f"download事象: {d.suggested_filename}"))


def _wait_enabled(session, label: str, timeout: float = 90.0) -> bool:
    """そのボタンが**押せる状態になる**まで待つ。

    実測で『ダウンロード』は `disabled=True` だった。押せないボタンを
    押しても何も起きないのは当然で、原因を外（覆い・座標・イベントの
    信用度）に探して何回も無駄にした。**押す前に押せるか見る。**

    読み込み中を示す `IMG.waiting` が消えるのも一緒に待つ。
    """
    start = time.monotonic()
    last = None
    while time.monotonic() - start < timeout:
        st = session.page.evaluate(
            r"""(want) => {
            const norm = s => (s || '').replace(/\s/g, '');
            let found = null;
            for (const el of document.querySelectorAll('button, input[type=button], a')) {
                if (!el.offsetParent) continue;
                if (norm(el.innerText || el.value) !== want) continue;
                found = {disabled: !!el.disabled,
                         cls: (el.className || '').toString().slice(0, 40)};
                break;
            }
            const busy = [...document.querySelectorAll('img.waiting, .waiting, .loading')]
                .some(e => e.offsetParent !== null);
            return {found, busy};
        }""", label)
        cur = (st["found"] or {}).get("disabled"), st["busy"]
        if cur != last:
            print(f"[分析コード] 『{label}』 disabled={cur[0]} 読込中={cur[1]}"
                  f" （{time.monotonic() - start:.0f}秒）")
            last = cur
        if st["found"] and not st["found"]["disabled"] and not st["busy"]:
            _NOTES.append(f"『{label}』が押せる状態になった（{time.monotonic() - start:.0f}秒）")
            print(_NOTES[-1])
            return True
        time.sleep(1.0)
    _NOTES.append(f"『{label}』は {timeout:.0f}秒たっても disabled={last[0] if last else '?'}"
                  f" 読込中={last[1] if last else '?'}")
    print(_NOTES[-1])
    return False


def _click_role_button(session, name: str) -> bool:
    """role=button として名前で押す。Playwright の本物のクリック。"""
    try:
        loc = session.page.get_by_role("button", name=name, exact=True)
        if loc.count() == 0:
            return False
        loc.first.click(timeout=8000)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[分析コード] role=button『{name}』: {type(e).__name__}")
        return False


def _click_at_text(session, label: str) -> bool:
    """文字が完全一致する要素の**真ん中を座標でクリック**する。

    セレクタで掴めていても、実際に当たっているのが別の要素（覆っている
    透明な層など）のことがある。座標なら「画面で見えている場所」を押せる。
    """
    box = session.page.evaluate(
        r"""(want) => {
        for (const el of document.querySelectorAll('button, a, label, div[role=button], span, input')) {
            if (!el.offsetParent) continue;
            const t = ((el.innerText || el.value || '').replace(/\s/g, ''));
            if (t !== want) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) continue;
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
        return null;
    }""", label)
    if not box:
        return False
    try:
        session.page.mouse.click(box["x"], box["y"])
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[分析コード] 座標クリック『{label}』: {type(e).__name__}")
        return False


def _click_real(session, label: str) -> bool:
    """Playwright の**本物のクリック**で押す。

    JS の `el.click()` は `isTrusted=false` の合成イベントで、
    フレームワークのハンドラやダウンロードの経路が反応しないことがある。
    実測では、ラジオを JS で押しても3つとも `checked=false` のままで、
    その結果『ダウンロード』も何も起こさなかった。

    ⚠️ 完全一致。この画面は押してよいものと**マスタを書き換えるもの**が
       隣り合っている（CSV出力↔CSV取込、ダウンロード↔キャンセル）。
    """
    try:
        loc = session.page.get_by_text(label, exact=True)
        for i in range(min(loc.count(), 6)):
            el = loc.nth(i)
            if el.is_visible():
                el.click(timeout=8000)
                return True
    except Exception as e:  # noqa: BLE001
        print(f"[分析コード] 『{label}』の本物クリックに失敗: {type(e).__name__}")
    return False


def _check_radio_real(session, value: str) -> bool | None:
    """ラジオを本物のクリックで選び、選べたかを読み直して返す。

    見つからなければ None。**選べたことを読み直して確かめる**のは、
    「全店のつもりで別の範囲を落とす」という静かな取り違えを防ぐため。
    """
    sel = f'input[type=radio][value="{value}"]'
    try:
        loc = session.page.locator(sel)
        if loc.count() == 0:
            return None
        el = loc.first
        try:
            el.check(timeout=8000)
        except Exception:  # noqa: BLE001
            # 本体が隠れて label で操作する作りのこともある
            el.click(timeout=8000, force=True)
        return bool(el.is_checked())
    except Exception as e:  # noqa: BLE001
        print(f"[分析コード] ラジオ『{value}』を押せません: {type(e).__name__}")
        return None


def _click_exact(session, label: str) -> bool:
    """文字が**完全一致**する見えている要素を押す。

    この画面は `CSV出力`/`CSV取込`、`ダウンロード`/`キャンセル`、
    `店舗`/`登録` が隣り合っている。部分一致や近傍で押すと
    **マスタを書き換える側**に当たる。ここは必ず完全一致にすること。
    """
    return bool(session.page.evaluate(
        r"""(want) => {
        for (const el of document.querySelectorAll(
                'button, a, label, input[type=button], input[type=submit], div[role=button], span')) {
            if (!el.offsetParent) continue;
            const t = ((el.innerText || el.value || '').replace(/\s/g, ''));
            if (t === want) { el.click(); return true; }
        }
        return false;
    }""", label))


def _has_exact(session, label: str) -> bool:
    """完全一致で見えている要素があるか。"""
    return bool(session.page.evaluate(
        r"""(want) => [...document.querySelectorAll('button, a, label, input[type=button], div[role=button], span')]
              .some(el => el.offsetParent
                       && (el.innerText || el.value || '').replace(/\s/g, '') === want)""",
        label))


def _grid_is_empty(session) -> bool:
    """グリッドが「データなし」のままかを見る。

    行数だけでは分からない。**空でも見出しと『データなし』で19行ある**ので、
    `_wait_for_grid` は満足してしまう（実測でそうだった）。
    """
    try:
        return bool(session.page.evaluate(
            """() => /データなし|該当するデータ/.test(document.body.innerText || '')"""
        ))
    except Exception:  # noqa: BLE001
        return True


def _dump_screen(session, when: str) -> None:
    """その時点の押せるもの・入力欄・選択肢を位置つきで出す。

    分析用コード設定は当て推量が続いたので、**1回のランで見られるものは
    全部見る**。成果物は落とせない（blob storage が 403）ので標準出力に出す。
    """
    print(f"――― {when} ―――")
    try:
        info = session.page.evaluate(
            r"""() => {
            const clip = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 40);
            const vis = el => el.offsetParent !== null;
            const btns = [];
            for (const el of document.querySelectorAll(
                    'button, a, input[type=button], input[type=submit], div[role=button], label')) {
                if (!vis(el)) continue;
                const t = clip(el.innerText || el.value);
                if (!t) continue;
                const r = el.getBoundingClientRect();
                // 上部のナビ（TOP/販売管理/…）は毎回同じで、**肝心の行を
                // ログの末尾から押し出す**。実行環境ではログの末尾しか
                // 読めないので、画面本体だけに絞る。
                if (r.y < 60) continue;
                btns.push({t, tag: el.tagName.toLowerCase(),
                           x: Math.round(r.x), y: Math.round(r.y)});
            }
            const inputs = [];
            for (const el of document.querySelectorAll('input, select, textarea')) {
                if (!vis(el)) continue;
                const r = el.getBoundingClientRect();
                inputs.push({tag: el.tagName.toLowerCase(), type: el.type || '',
                             val: clip(el.value), ph: clip(el.placeholder),
                             name: clip(el.name), x: Math.round(r.x), y: Math.round(r.y)});
            }
            const dialogs = [...document.querySelectorAll(
                '[role=dialog], .modal, .dialog, .v-dialog, .popup')]
                .filter(vis).map(d => clip(d.innerText)).slice(0, 6);
            return {url: location.href, btns: btns.slice(0, 14),
                    inputs: inputs.slice(0, 12), dialogs,
                    empty: /データなし/.test(document.body.innerText || '')};
        }"""
        )
    except Exception as e:  # noqa: BLE001
        print(f"  画面を読めませんでした: {type(e).__name__}: {e}")
        return
    print(f"  url={info['url']}  データなし={info['empty']}")
    print(f"  押せるもの {len(info['btns'])}件:")
    for b in info["btns"]:
        print(f"    {b['tag']:<6} x={b['x']:>4} y={b['y']:>4}  {b['t']}")
    print(f"  入力欄 {len(info['inputs'])}件:")
    for i in info["inputs"]:
        print(f"    {i['tag']}/{i['type']:<8} x={i['x']:>4} y={i['y']:>4}"
              f"  値={i['val']!r} 名={i['name']!r} ヒント={i['ph']!r}")
    if info["dialogs"]:
        print(f"  ダイアログらしきもの {len(info['dialogs'])}件:")
        for d in info["dialogs"]:
            print(f"    {d}")


def analysis_code_summary(rows: list[list[str]]) -> dict | None:
    """CSVの行から「分析用コードの埋まり具合」を出す。

    **何を入力漏れとみなすかの判断はここだけ**にまとめ、印字と切り離す。
    店長会資料はコードの上に乗っていて、付け忘れたメニューは消えずに
    アラカルトに紛れ込むので、ここの判定を間違えると静かに嘘をつく。

    実測の見出し（`画面表示` で落としたCSV）:

        店舗コード | 店舗名 | メニューコード | 名称 | 標準税率10%込 |
        軽減税率8%込 | 税抜 | 原価 | 部門コード | 部門名称 |
        グループコード | グループ名称 | 消費税 | ユーザーコード | 分析用コード

    ⚠️ **列は名前で探すこと。** メニュー名を1列目と決め打ちしていたら、
       そこは `店舗名` で、漏れの一覧に店名が78個並んだ（実際に出した）。
       どの列も見出しの**完全一致**で引く。部分一致だとタイトル行の
       「分析用コード設定」に当たって全部ずれる。
    """
    def _norm(c: str | None) -> str:
        return "".join((c or "").split())

    head_i = head = None
    for i, row in enumerate(rows[:5]):
        if any(_norm(c) == "分析用コード" for c in row):
            head_i, head = i, row
            break
    if head is None:
        return None

    def _col(name: str) -> int | None:
        for j, c in enumerate(head):
            if _norm(c) == name:
                return j
        return None

    col = _col("分析用コード")
    name_col = _col("名称")
    store_col = _col("店舗コード")
    store_name_col = _col("店舗名")
    if name_col is None:                 # 見出しが変わったときの保険
        name_col = 1 if len(head) > 1 else 0

    body = [r for r in rows[head_i + 1:] if any((c or "").strip() for c in r)]

    def _at(r: list[str], j: int | None) -> str:
        return (r[j] or "").strip() if j is not None and len(r) > j else ""

    blank: list[dict] = []
    by_store: dict[str, int] = {}
    codes: set[str] = set()
    for r in body:
        code = _at(r, col)
        if code:
            codes.add(code)
            continue
        store = _at(r, store_col)
        blank.append({
            "store_code": store.lstrip("0"),
            "store_name": _at(r, store_name_col),
            "menu": _at(r, name_col),
        })
        by_store[store.lstrip("0")] = by_store.get(store.lstrip("0"), 0) + 1

    return {
        "header_row": head_i,
        "col": col,
        "name_col": name_col,
        "store_col": store_col,
        "total": len(body),
        "filled": len(body) - len(blank),
        "blank": blank,
        "by_store": by_store,
        "codes": sorted(codes, key=lambda c: (len(c), c)),
    }


def _describe_analysis_csv(data: bytes) -> None:
    """落ちてきたCSVの形だけを出す。

    ⚠️ **中身の金額は出さない。** この画面には単価と原価が載っていて、
    このリポジトリは公開。ログに出すのは列名・行数・コードの埋まり具合と、
    コードが空のメニュー名だけにする。
    """
    import csv as _csv
    import io as _io

    text, enc = None, None
    for cand in ("cp932", "utf-8-sig", "utf-8", "euc_jp"):
        try:
            text = data.decode(cand)
            enc = cand
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        print("[分析コード] 文字コードを判別できませんでした")
        return
    rows = list(_csv.reader(_io.StringIO(text)))
    print(f"[分析コード] 文字コード={enc} / {len(rows)}行")
    for i, r in enumerate(rows[:3]):
        print(f"    見出し候補 {i}: {r}")

    got = analysis_code_summary(rows)
    if got is None:
        print("[分析コード] 『分析用コード』の列が見出しに見つかりません")
        return
    print(f"[分析コード] 分析用コード={got['col']}列 / 名称={got['name_col']}列"
          f" / 店舗コード={got['store_col']}列")
    print(f"[分析コード] メニュー {got['total']}行 / コード有り {got['filled']}"
          f" / 空 {len(got['blank'])}")
    print(f"[分析コード] 使われているコード {len(got['codes'])}種: {got['codes']}")
    if got["by_store"]:
        print("[分析コード] 店ごとのコード未設定:")
        for code, n in sorted(got["by_store"].items(), key=lambda kv: -kv[1]):
            print(f"    {code}\t{n}件")
    for b in got["blank"][:15]:
        print(f"    コード空: {b['store_code']} {b['menu'][:30]}")


def audit_analysis_codes(
    artifacts: Path,
    master,
    warehouse=None,
    *,
    store_filter: str = "",
    store_limit: int | None = None,
    months: int = 3,
) -> int:
    """全店の分析用コードの入力漏れを調べ、あれば赤くする。FWには書き込まない。

    店長会資料はコード1〜28の上に乗っていて、アラカルトは

        アラカルト = 総数 －宴会 －ランチ －食べ飲み －ツアー －単品飲み放題 －テイクアウト

    という引き算で出る。**コードを付け忘れたメニューは消えずにアラカルトへ
    紛れ込む**ので、どこも空欄にならず資料は完成して見える。だから人が
    気づけない。ここで赤くするのが唯一の防波堤。

    ⚠️ `全店` でのCSV出力は130店ぶんで、5分待っても落ちてこなかった。
       1店ずつ `画面表示` で落とす。ログインは1回なので実行時間は許容範囲。
    ⚠️ この画面は『登録』『CSV取込』でマスタを書き換えられる。触らない。
    """
    from .fw_budget import _select_combo

    key = (store_filter or "").strip()
    per_store: list[dict] = []
    failures: list[str] = []

    with fw_session(artifacts) as session:
        _watch_page(session)
        options = _open_and_wait_combo(session)
        print(f"[分析コード] 店舗コンボ {len(options)}件")

        active = {s.store_code: s for s in master.active}
        targets, not_fw = [], []
        for opt in options:
            code = opt["value"].lstrip("0")
            st = active.get(code) or master.find_by_name(opt["name"])
            if not (st and st.active):
                continue
            # ⚠️ FW未連動の店（1766＝uレジ）はFWにメニューが無い。取れなくて
            #    当たり前なので、**取り漏れとして赤くしない**。黙って飛ばすのも
            #    違うので、理由を添えて別に数える。カバレッジと同じ扱い。
            if getattr(st, "pos", "fw") != "fw":
                not_fw.append(st)
                continue
            targets.append((opt, st))
        for st in not_fw:
            print(f"  ― {st.store_code} {st.store_name[:14]}  "
                  f"FW未連動（メニューはFWに無い）ので対象外")
        if key:
            targets = [(o, s) for o, s in targets
                       if s.store_code == key.lstrip("0") or key in s.store_name]
        if store_limit:
            targets = targets[:store_limit]
        print(f"[分析コード] 対象 {len(targets)}店")
        if not targets:
            print("::error::[分析コード] 対象の店が1件もありません")
            return 1

        for opt, st in targets:
            label = f"{st.store_code} {st.store_name[:14]}"
            try:
                if not _select_combo(session, opt["value"]):
                    raise RuntimeError("店舗コンボから選べない")
                if not _commit_store(session):
                    raise RuntimeError("グリッドに中身が出ない")
                data = _download_analysis_csv(session)
                if data is None:
                    raise RuntimeError("CSVが落ちてこない")
                got = analysis_code_summary(_read_csv_rows(data))
                if got is None:
                    raise RuntimeError("『分析用コード』の列が無い")
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {label}  {type(e).__name__}: {str(e)[:60]}")
                failures.append(st.store_code)
                continue
            per_store.append({"store": st, "got": got})
            n = len(got["blank"])
            mark = "⚠" if n else "✓"
            print(f"  {mark} {label}  メニュー {got['total']}件 / 未設定 {n}件")

    # 直近の商品別売上と突き合わせ、**実際に売れているもの**だけを赤にする。
    # `お冷`・`コピー`・`-` のような売れないメニューまで赤にすると、
    # 一覧が2701件になって誰も着手しない（実際そうなった）。
    sold, judged = _sold_menu_keys(
        warehouse, [p["store"].store_code for p in per_store], months)
    return _report_analysis_codes(per_store, failures, sold=sold, judged=judged)


def _read_csv_rows(data: bytes) -> list[list[str]]:
    """落ちてきたCSVを行の並びにする。文字コードは総当たりで決める。"""
    import csv as _csv
    import io as _io

    for cand in ("cp932", "utf-8-sig", "utf-8", "euc_jp"):
        try:
            return list(_csv.reader(_io.StringIO(data.decode(cand))))
        except UnicodeDecodeError:
            continue
    return []


def menu_key(name: str) -> str:
    """メニュー名の突き合わせキー。全角/半角・空白の揺れを吸収する。

    FWのABC側と分析用コード設定側で表記が微妙に違うことがあるので、
    店名の `store_key` と同じ考え方で寄せる。
    """
    import unicodedata

    s = unicodedata.normalize("NFKC", str(name or ""))
    return "".join(s.split()).casefold()


def _sold_menu_keys(warehouse, store_codes, months: int):
    """直近 months ヶ月に**売れた**メニューの鍵を店ごとに集める。

    返り値は (売れた鍵の集合, 判定できた店の集合)。

    ⚠️ **売上データが1件も無い店を「売れていない」と扱わない。** そうすると
       ABCが未取得なだけの店が全部「影響なし」になり、静かに見逃す。
       判定できた店だけを `judged` に入れ、それ以外は要確認として扱う。
    """
    if warehouse is None:
        return set(), set()
    from datetime import date as _date

    from ..db.warehouse import AggregateQuery
    from ..model import GRAIN_MONTH, METRIC_PRODUCT_SALES

    today = _date.today()
    y, m = today.year, today.month
    for _ in range(max(months, 1)):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    start = _date(y, m, 1)
    end = _date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)

    sold: set[tuple[str, str]] = set()
    judged: set[str] = set()
    try:
        for row in warehouse.aggregate(AggregateQuery(
            date_from=start, date_to=end, grain=GRAIN_MONTH,
            metrics=[METRIC_PRODUCT_SALES], store_codes=list(store_codes),
            group_by=("store_code", "product_name"),
        )):
            code = str(row.get("store_code") or "")
            judged.add(code)
            if (row.get("value") or 0) > 0 and row.get("product_name"):
                sold.add((code, menu_key(row["product_name"])))
    except Exception as e:  # noqa: BLE001
        print(f"[分析コード] 商品別売上を読めませんでした: {type(e).__name__}: {e}")
        return set(), set()
    print(f"[分析コード] 直近{months}ヶ月の商品別売上: {len(judged)}店 / {len(sold)}品")
    return sold, judged


def classify_blank(blank: dict, sold: set, judged: set) -> str:
    """コード未設定のメニュー1件を仕分ける。

    - "売れた"   … 直近に売上がある。**資料の数字が狂っている**ので要対応
    - "売れてない" … 売上が無い。アラカルトに紛れても影響しない
    - "判定不能" … その店の商品別売上が無く、判断できない。**見逃さないため
                   に赤の側に寄せる**（「売れてない」に倒すと静かに見逃す）
    """
    code = blank.get("store_code") or ""
    if code not in judged:
        return "判定不能"
    return "売れた" if (code, menu_key(blank.get("menu", ""))) in sold else "売れてない"


def _report_analysis_codes(per_store: list[dict], failures: list[str],
                           *, sold: set | None = None,
                           judged: set | None = None) -> int:
    """結果をまとめて出し、赤くするかを決める。

    **赤にするのは「売れているのにコードが無い」ものだけ。** 全部を赤に
    すると一覧が2701件になり、`お冷` や `コピー` が混じったまま誰も着手
    しない（実際そうなった）。売れていないメニューはアラカルトに紛れても
    影響しないので、数だけ出す。

    ⚠️ 商品別売上が無くて**判定できなかった店は赤の側**に寄せる。
       「売れてない」に倒すと、ABCが未取得なだけの店を静かに見逃す。

    ⚠️ **金額は出さない。** この画面には単価と原価が載っていて、この
       リポジトリは公開。出すのはメニュー名と件数だけにする。
    """
    sold = sold or set()
    judged = judged or set()
    print("")
    print("=== 分析用コードの入力漏れ ===")

    rows: list[tuple] = []          # (店, 要対応, 判定不能, 影響なし, 要対応の名前)
    for p in per_store:
        st, got = p["store"], p["got"]
        buckets: dict[str, list[str]] = {"売れた": [], "判定不能": [], "売れてない": []}
        for b in got["blank"]:
            buckets[classify_blank(b, sold, judged)].append(b["menu"])
        rows.append((st, got, buckets))

    need = [(st, got, bk) for st, got, bk in rows if bk["売れた"] or bk["判定不能"]]
    # 明細は上位5店だけ。全店ぶん出すとログの末尾が明細で埋まる。
    # 全体像は下の順位表で見る。
    ordered = sorted(need, key=lambda r: -(len(r[2]["売れた"]) + len(r[2]["判定不能"])))
    for st, got, bk in ordered[:5]:
        head = f"  ⚠ {st.store_code} {st.store_name[:16]:<16} {got['total']}件中 "
        parts = []
        if bk["売れた"]:
            parts.append(f"売れているのに未設定 {len(bk['売れた'])}件")
        if bk["判定不能"]:
            parts.append(f"判定不能 {len(bk['判定不能'])}件")
        if bk["売れてない"]:
            parts.append(f"（売れていない {len(bk['売れてない'])}件は影響なし）")
        print(head + " / ".join(parts))
        for nm in (bk["売れた"] or bk["判定不能"])[:8]:
            print(f"        {nm[:34]}")
        rest = len(bk["売れた"] or bk["判定不能"]) - 8
        if rest > 0:
            print(f"        …ほか {rest}件")

    clean = [r for r in rows if not (r[2]["売れた"] or r[2]["判定不能"])]
    if clean:
        harmless = sum(len(r[2]["売れてない"]) for r in clean)
        note = f"（売れていない未設定 {harmless}件は影響なし）" if harmless else ""
        print(f"  ✓ 対応不要 {len(clean)}店: "
              f"{', '.join(r[0].store_code for r in clean)} {note}")

    # ⚠️ **順位表は明細のあとに置く。** 実行環境ではログの末尾しか読めず、
    #    明細（1店あたり最大9行）が長いので、先に出すと上位の店が
    #    押し出されて見えない。実際に1375件中244件ぶんしか読めなかった。
    ranked = [(st, len(bk["売れた"]), len(bk["判定不能"]), len(bk["売れてない"]))
              for st, _, bk in rows]
    ranked.sort(key=lambda r: (-r[1], -r[2]))
    print("")
    print("--- 要対応の多い順 ---")
    for st, n_s, n_u, n_h in ranked:
        if not (n_s or n_u):
            continue
        extra = f" / 判定不能 {n_u}" if n_u else ""
        print(f"  {st.store_code} {st.store_name[:16]:<16} 要対応 {n_s:>4}件{extra}"
              f"  （影響なし {n_h}）")

    n_sold = sum(len(bk["売れた"]) for _, _, bk in rows)
    n_unknown = sum(len(bk["判定不能"]) for _, _, bk in rows)
    n_harmless = sum(len(bk["売れてない"]) for _, _, bk in rows)
    total_menu = sum(got["total"] for _, got, _ in rows)
    print(f"=== 調べた {len(rows)}店 / メニュー {total_menu}件 / 未設定 "
          f"{n_sold + n_unknown + n_harmless}件 "
          f"（要対応 {n_sold} / 判定不能 {n_unknown} / 影響なし {n_harmless}）===")

    if failures:
        print(f"::error::[分析コード] 調べられなかった店 {len(failures)}件: {failures}")
    if n_sold:
        print(f"::error::[分析コード] **売れているのに分析用コードが無い**メニューが "
              f"{n_sold}件あります。このぶんの売上は店長会資料でアラカルトに"
              f"紛れ込み、宴会・ランチ・ツアー等の数字が実際より小さく出ます")
    if n_unknown:
        print(f"::error::[分析コード] 商品別売上が無く判定できないメニューが "
              f"{n_unknown}件あります（その店のABCが未取得の可能性）")
    if failures or n_sold or n_unknown:
        return 1
    if n_harmless:
        print(f"売れているメニューの取りこぼしはありません"
              f"（売れていない未設定 {n_harmless}件は影響なし）。")
    else:
        print("分析用コードの入力漏れはありません。")
    return 0
