"""
FW「分析用コード設定」（マスタ管理→販売マスタ→分析用コード設定）を読む。

この画面は商品ごとに『分析用コード（1〜28）』を持つマスタ。列は
  コード（商品CD） / 名称 / 単価(標準/軽減/税抜) / 原価 / 部門 / グループ /
  消費税 / ユーザー用メニューコード / 分析用コード
店舗プルダウンで店を切り替える。まずは probe で画面の実体（店一覧・列・数行・
分析用コードの分布）をログに出し、それを見て本取込を書く。read-only（登録は押さない）。
"""
from __future__ import annotations

import time
from pathlib import Path

from .fw_browser import fw_session

# トップナビ「マスタ管理」→「販売マスタ」→「分析用コード設定」。
NAV = ("マスタ管理", "販売マスタ", "分析用コード設定")


def _read_grid(session) -> dict:
    """画面のテーブルからヘッダ・行を吸い出す。分析用コードは input 値も拾う。"""
    return session.page.evaluate(
        """() => {
        // 店舗プルダウンの現在値
        let store = "";
        const ssel = document.querySelector('select, ng-select, .ng-value');
        if (ssel) store = (ssel.value || ssel.textContent || "").trim();
        // 一番行数の多いテーブルを本体とみなす
        const tables = [...document.querySelectorAll('table')];
        let best = null, bestRows = -1;
        for (const t of tables) {
            const n = t.querySelectorAll('tr').length;
            if (n > bestRows) { bestRows = n; best = t; }
        }
        if (!best) return {store, error: 'no-table', tables: tables.length};
        const headers = [...best.querySelectorAll('thead th, thead td')].map(c => (c.innerText||'').trim());
        const rows = [];
        for (const tr of best.querySelectorAll('tbody tr')) {
            const cells = [...tr.children].map(c => {
                const inp = c.querySelector('input');
                const v = inp ? (inp.value || '') : (c.innerText || '');
                return v.trim();
            });
            if (cells.some(x => x)) rows.push(cells);
        }
        return {store, headers, rowCount: rows.length, rows: rows.slice(0, 40)};
    }"""
    )


def _store_options(session) -> list:
    """店舗プルダウンの選択肢（店名一覧）。<select> でも ng-select でも拾えるよう広めに。"""
    return session.page.evaluate(
        """() => {
        const out = [];
        for (const o of document.querySelectorAll('select option')) {
            const t = (o.textContent||'').trim();
            if (t) out.push(t);
        }
        return out;
    }"""
    )


def probe(artifacts: Path) -> int:
    with fw_session(artifacts) as session:
        for label in NAV:
            if not session.click_text(label):
                session.snapshot(f"missing_{label}")
                session.dump_clickables(f"failed_at_{label}")
                print(f"⚠ 「{label}」が見つかりませんでした。")
                return 1
            session.snapshot(f"after_{label}")
            print(f"--- 「{label}」クリック後 URL: {session.page.url}")
        # グリッドは読み込みに時間がかかる（『時間たったら出てくる』）。粘って待つ。
        grid = {}
        for attempt in range(12):
            time.sleep(2.5)
            grid = _read_grid(session)
            if grid.get("rowCount"):
                break
            print(f"  待機中… ({attempt + 1}) rows={grid.get('rowCount')} err={grid.get('error')}")
        session.snapshot("analysis_grid")
        opts = _store_options(session)
        print(f"\n=== 店舗プルダウン候補 {len(opts)}件 ===")
        for o in opts[:40]:
            print(f"  {o}")
        print(f"\n=== 現在の店舗: {grid.get('store')!r} ===")
        print(f"=== 列ヘッダ: {grid.get('headers')}")
        print(f"=== 行数（本体テーブル）: {grid.get('rowCount')}")
        # 分析用コードの分布（最終列 or 『分析用コード』列）を数える
        headers = grid.get("headers") or []
        rows = grid.get("rows") or []
        ci = None
        for i, h in enumerate(headers):
            if "分析用コード" in h:
                ci = i
        print(f"=== 先頭40行（コード / 名称 / …末尾=分析用コード）===")
        for r in rows:
            code = r[ci] if (ci is not None and ci < len(r)) else (r[-1] if r else "")
            name = r[1] if len(r) > 1 else ""
            print(f"  {(r[0] if r else ''):<12} {name:<24} 分析用コード={code}")
    print(f"\n成果物: {artifacts}")
    return 0
