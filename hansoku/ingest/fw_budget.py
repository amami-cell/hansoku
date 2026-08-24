"""
FW 損益管理 → 予算管理業務 → 月別予算登録 から売上予算を取り込む。

画面は Angular。グリッドを直接パースするより、画面の「CSV出力」で落とした
CSV を読む方が壊れにくい（既存パイプラインでも CSV 経由が安定している）。
まず ``probe`` で CSV の中身を確認し、それに合わせて ``ingest`` を書く。

メニュー階層（探索で確認済み）:
  損益管理 → 予算管理業務(/app/profit_loss/pl-menu-budget)
           → 月別予算登録(/app/profit_loss/pl-menu-budget/monthly)
月別予算登録の操作要素: 店舗選択 / 前月・翌月 / ＦＯＯＤ・ＤＲＩＮＫ・雑費 /
  検索 / 登録 / CSV出力 / CSV取込。
"""
from __future__ import annotations

import time
from pathlib import Path

from .fw_browser import FWError, fw_session

BUDGET_MENU = ("損益管理", "予算管理業務", "月別予算登録")


def _open_monthly_budget(session) -> None:
    """月別予算登録の画面まで遷移する。"""
    for label in BUDGET_MENU:
        if not session.click_text(label):
            session.snapshot(f"missing_{label}")
            session.dump_clickables(f"failed_{label}")
            raise FWError(f"「{label}」に進めませんでした")
        session.snapshot(f"opened_{label}")


def _decode(raw: bytes) -> tuple[str, str]:
    """FW の CSV は Shift_JIS のことが多い。読める符号化を探す。"""
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace"), "cp932(replace)"


def _download_csv(session, artifacts: Path, name: str) -> Path | None:
    """画面の「CSV出力」を押してダウンロードを保存する。"""
    page = session.page
    try:
        with page.expect_download(timeout=30000) as dl_info:
            if not session.click_text("CSV出力", wait=1.0):
                page.evaluate(
                    """() => {
                    for (const b of document.querySelectorAll('button, a')) {
                        if ((b.innerText || '').replace(/\\s/g,'').includes('CSV出力')) { b.click(); return; }
                    }
                }"""
                )
        download = dl_info.value
        path = artifacts / name
        download.save_as(str(path))
        return path
    except Exception as exc:  # noqa: BLE001
        session.snapshot("csv_failed")
        session.dump_clickables("csv_failed")
        print(f"[budget] CSV出力に失敗: {exc}")
        return None


def _pick_first_store(session) -> str | None:
    """店舗セレクタ（ng-select）を開いて先頭の店舗を選ぶ。best-effort。"""
    page = session.page
    page.evaluate(
        """() => {
        const lab = [...document.querySelectorAll('*')].find(
            el => el.children.length === 0 && el.textContent.trim() === '店舗' && el.offsetParent);
        let node = lab ? lab.parentElement : null, trig = null;
        for (let i = 0; i < 8 && node; i++) {
            trig = node.querySelector('ng-select,.ng-select,.ng-input,.ng-value-container,.ng-arrow-wrapper');
            if (trig) break;
            node = node.parentElement;
        }
        if (trig) trig.click();
    }"""
    )
    time.sleep(0.8)
    picked = page.evaluate(
        """() => {
        const opts = [...document.querySelectorAll('.ng-option,[class*="ng-option"],li.option,mat-option')]
            .filter(o => o.offsetParent);
        if (opts.length) {
            opts[0].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            return opts[0].textContent.trim();
        }
        return null;
    }"""
    )
    time.sleep(0.8)
    return picked


def _dump_inputs_grouped(session) -> None:
    """入力欄を「行ラベルごと」にまとめて吸い出す（表ではなく div/input 構成のため）。"""
    rows = session.page.evaluate(
        """() => {
        const clip = s => (s || '').replace(/\\s+/g, ' ').trim();
        const inputs = [...document.querySelectorAll('input')]
            .filter(i => i.offsetParent && !['button','checkbox','radio','submit'].includes(i.type));
        const rows = [];
        const byLabel = new Map();
        for (const inp of inputs) {
            let node = inp, label = '';
            for (let i = 0; i < 6 && node; i++) {
                node = node.parentElement;
                if (!node) break;
                const leaf = [...node.querySelectorAll('*')].find(
                    el => el.children.length === 0 && clip(el.innerText) &&
                          el.tagName !== 'INPUT' && el.tagName !== 'BUTTON');
                if (leaf) { label = clip(leaf.innerText); break; }
            }
            if (!byLabel.has(label)) { byLabel.set(label, []); rows.push(label); }
            byLabel.get(label).push(clip(inp.value));
        }
        return rows.map(l => ({ label: l, vals: byLabel.get(l) }));
    }"""
    )
    print(f"---- 入力欄（行ラベル別） {len(rows)}行 ----")
    for r in rows[:50]:
        vals = " | ".join(r["vals"][:14])
        print(f"   [{r['label']}] {vals}")


def _dump_grid(session) -> None:
    """画面のグリッド（表・入力欄）を 2 次元で吸い出して表示する。"""
    tables = session.page.evaluate(
        """() => {
        const clip = s => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 24);
        const tables = [...document.querySelectorAll('table')].filter(t => t.offsetParent);
        return tables.slice(0, 4).map(t => {
            const rows = [];
            for (const tr of t.querySelectorAll('tr')) {
                const cells = [];
                for (const c of tr.querySelectorAll('th,td')) {
                    const inp = c.querySelector('input,select');
                    cells.push(clip(inp ? (inp.value || '') : c.innerText));
                }
                if (cells.some(x => x)) rows.push(cells);
            }
            return rows;
        });
    }"""
    )
    # 選択中の店舗・期間まわりのラベルも拾う
    context = session.page.evaluate(
        """() => {
        const clip = s => (s || '').replace(/\\s+/g, ' ').trim();
        const out = [];
        for (const el of document.querySelectorAll('input,select,.ng-value,[class*="store"],[class*="date"],[class*="month"]')) {
            if (!el.offsetParent) continue;
            const v = clip(el.value || el.innerText);
            if (v && v.length < 30) out.push(el.tagName.toLowerCase() + ':' + v);
        }
        return [...new Set(out)].slice(0, 30);
    }"""
    )
    print("---- 選択中の状態（店舗/期間/入力欄） ----")
    for c in context:
        print("   ", c)
    for ti, rows in enumerate(tables):
        print(f"---- 表 {ti} （{len(rows)}行）----")
        for row in rows[:25]:
            print("   ", " | ".join(row))


def probe(artifacts: Path) -> int:
    """月別予算登録に入り、検索してグリッドを吸い出す。CSVも試す。取り込み前の下調べ。"""
    with fw_session(artifacts) as session:
        _open_monthly_budget(session)
        items = session.dump_clickables("budget_screen")
        print(f"[budget] 月別予算登録の操作要素 {len(items)} 件")

        # 店舗を選んでから検索する（登録画面は店舗未選択だと空のことがある）
        store = _pick_first_store(session)
        print(f"[budget] 選んだ店舗: {store}")
        if session.click_text("検 索", wait=2.5) or session.click_text("検索", wait=2.5):
            print("[budget] 検索を押した")
        session.snapshot("after_search")
        session.dump_clickables("after_search")
        _dump_inputs_grouped(session)
        _dump_grid(session)

        # CSV出力も試す（新規タブ/ダイアログのこともある）
        path = _download_csv(session, artifacts, "budget_monthly.csv")
        if path:
            raw = path.read_bytes()
            text, enc = _decode(raw)
            print(f"[budget] CSV {len(raw)} bytes, enc={enc}")
            print("---- CSV 先頭40行 ----")
            for line in text.splitlines()[:40]:
                print(line)
    print(f"\n成果物: {artifacts}")
    return 0
