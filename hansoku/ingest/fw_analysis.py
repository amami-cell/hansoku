"""
FW「分析用コード設定」（マスタ管理→販売マスタ→分析用コード設定）を読む。

この画面は商品ごとに『分析用コード（1〜28）』を持つマスタ。列は
  コード（商品CD） / 名称 / 単価(標準/軽減/税抜) / 原価 / 部門 / グループ /
  消費税 / ユーザー用メニューコード / 分析用コード
本体は EJS TreeGrid（ABC画面と同じ）で、店舗プルダウンで店を選ぶと表が出る。
まずは probe で「店舗候補・列・数行・分析用コードの分布」をログに出し、それを見て
本取込を書く。read-only（登録は押さない）。
"""
from __future__ import annotations

import time
from pathlib import Path

from .fw_browser import fw_session

NAV = ("マスタ管理", "販売マスタ", "分析用コード設定")

# EJS TreeGrid は固定列とスクロール列を別テーブルに描くため、DOMの葉を画面上の縦位置(top)で
# まとめて1視覚行に復元する（fw_daily の _VISUAL_ROWS_JS と同手法。input値も拾う）。
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


def _dump_store_control(session) -> dict:
    """『店舗』ラベル近傍のコントロール構造を吸い出す（正しいセレクタ特定用）。"""
    return session.page.evaluate(
        """() => {
        const lab = [...document.querySelectorAll('*')].find(
            el => el.children.length === 0 && (el.textContent || '').trim() === '店舗' && el.offsetParent);
        let ancestorHtml = '';
        if (lab) {
            let n = lab;
            for (let i = 0; i < 3 && n.parentElement; i++) n = n.parentElement;
            ancestorHtml = (n.outerHTML || '').slice(0, 1500);
        }
        // 画面上部(top<200px)の入力/コンボ候補を列挙
        const cand = [];
        const sel = 'input,select,button,ejs-dropdownlist,ejs-combobox,[class*="dropdown"],[class*="combo"],[class*="ng-select"],[role="combobox"]';
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (!el.offsetParent || r.top > 220 || r.width === 0) continue;
            cand.push({
                tag: el.tagName.toLowerCase(),
                id: el.id || '',
                cls: (el.className || '').toString().slice(0, 80),
                value: (el.value || '').slice(0, 40),
                text: (el.textContent || '').trim().slice(0, 40),
                left: Math.round(r.left), top: Math.round(r.top),
            });
        }
        return {ancestorHtml, cand};
    }"""
    )


def _pick_store(session, name: str) -> bool:
    return session.page.evaluate(
        """(name) => {
        const sel = 'li.option,[class*="ng-option"],mat-option,option,[role="option"],ul li';
        const opts = [...document.querySelectorAll(sel)].filter(o => o.offsetParent);
        let t = opts.find(o => (o.textContent || '').trim() === name)
             || opts.find(o => (o.textContent || '').trim().includes(name));
        if (!t) return false;
        t.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
        if (t.tagName === 'OPTION') {
            const s = t.closest('select');
            if (s) { s.value = t.value; s.dispatchEvent(new Event('change', {bubbles:true})); }
        }
        return true;
    }""",
        name,
    )


def probe(artifacts: Path) -> int:
    with fw_session(artifacts) as session:
        for label in NAV:
            if not session.click_text(label):
                session.snapshot(f"missing_{label}")
                session.dump_clickables(f"failed_at_{label}")
                print(f"⚠ 「{label}」が見つかりませんでした。")
                return 1
            print(f"--- 「{label}」クリック後 URL: {session.page.url}")
        time.sleep(2)
        session.snapshot("analysis_open")

        # 店舗一覧は ul.options > li.option[value=店コード] にある（開かなくても読める）。
        stores = session.page.evaluate(
            """() => [...document.querySelectorAll('li.option')]
                .map(li => ({code: (li.getAttribute('value')||'').trim(), name: (li.textContent||'').trim()}))
                .filter(s => s.code)"""
        )
        print(f"\n=== 店舗一覧 {len(stores)}件（コード:店名）===")
        for s in stores[:40]:
            print(f"  {s['code']}:{s['name']}")

        # 1店で抽出を確認（すさび湯 0001006）。ドロップダウンを開いて li を選ぶ。
        target = next((s for s in stores if s["code"].endswith("1006")), stores[0] if stores else None)
        if not target:
            print("⚠ 店舗が取れませんでした。")
            return 1
        print(f"\n=== 選択: {target['code']}:{target['name']} ===")
        session.page.evaluate(
            """(code) => {
            const btn = document.querySelector('.dropdown-btn.enabledbutton');
            if (btn) btn.click();
            const li = [...document.querySelectorAll('li.option')].find(o => (o.getAttribute('value')||'').trim() === code);
            if (li) li.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
            return !!li;
        }""",
            target["code"],
        )
        # 検索/表示が要る場合に備えて Enter も送る
        try:
            session.page.keyboard.press("Enter")
        except Exception:
            pass

        rows = []
        for attempt in range(15):
            time.sleep(2.5)
            rows = session.page.evaluate(_VISUAL_ROWS_JS)
            data = [r for r in rows if r and r[0].replace(",", "").isdigit() and len(r[0]) >= 4]
            if len(data) >= 3:
                break
            print(f"  待機中… ({attempt + 1}) 視覚行={len(rows)}")
        session.snapshot("analysis_grid")
        print(f"\n=== 視覚行 総数: {len(rows)} ===")
        print("=== 先頭60視覚行（コード｜名称｜…｜分析用コード）===")
        for r in rows[:60]:
            print("  | ".join(r))
    print(f"\n成果物: {artifacts}")
    return 0
