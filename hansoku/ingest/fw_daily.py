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


def report_probe(artifacts: Path, last_label: str) -> int:
    """損益管理→実績管理業務→<last_label> を開き、店舗を選び検索してグリッド構造を
    吸い出す。月次の客数・客単価がどの帳票にあるかを特定するための診断。"""
    menu = ("損益管理", "実績管理業務", last_label)
    with fw_session(artifacts) as session:
        _open_menu(session, menu)
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
