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

# グリッドの見えている行を {商品CD, 名称, 分析用コード} で吸い出す。分析用コードは行内の
# input 値（空欄＝未入力）。EJS TreeGrid は固定列/スクロール列が別描画なので、CD セルと
# 同じ縦位置(top±6px)の葉を1行に束ねる。
_EXTRACT_JS = r"""() => {
    const clip = s => (s || '').replace(/\s+/g, ' ').trim();
    const all = [];
    for (const el of document.querySelectorAll('td, div, span, input')) {
        if (!el.offsetParent) continue;
        if (el.children && el.children.length) continue;
        const r = el.getBoundingClientRect();
        if (r.top < 175 || r.width === 0 || r.height === 0) continue;   // ヘッダより下だけ
        const isInput = el.tagName === 'INPUT';
        const txt = clip(isInput ? el.value : el.innerText);
        all.push({ top: Math.round(r.top), left: Math.round(r.left), txt, isInput });
    }
    const cds = all.filter(a => !a.isInput && /^\d{10,}$/.test(a.txt));
    const rows = [];
    for (const cd of cds) {
        const row = all.filter(a => Math.abs(a.top - cd.top) <= 6).sort((a, b) => a.left - b.left);
        let name = '';
        for (const c of row) { if (c.left > cd.left && c.txt && !/^[\d.]/.test(c.txt)) { name = c.txt; break; } }
        const inputs = row.filter(a => a.isInput);
        const code = inputs.length ? inputs[inputs.length - 1].txt : '';
        rows.push({ cd: cd.txt, name, code });
    }
    return rows;
}"""

# グリッド本体を1画面ぶん下へ送る。実際に動いたら true。
_SCROLL_JS = r"""() => {
    const cands = [...document.querySelectorAll('div, table, tbody')].filter(el => {
        const r = el.getBoundingClientRect();
        return el.scrollHeight > el.clientHeight + 20 && el.clientHeight > 120 && r.top > 120;
    });
    if (!cands.length) return false;
    cands.sort((a, b) => b.clientHeight - a.clientHeight);
    const el = cands[0];
    const before = el.scrollTop;
    el.scrollTop = before + Math.round(el.clientHeight * 0.85);
    return el.scrollTop > before;
}"""


def _collect_all_rows(session, *, max_steps: int = 300) -> list[dict]:
    """グリッドを最後まで送りながら全行を集める（商品CDで重複排除）。"""
    seen: dict[str, dict] = {}
    stagnant = 0
    for _ in range(max_steps):
        for r in session.page.evaluate(_EXTRACT_JS):
            if r["cd"] and r["cd"] not in seen:
                seen[r["cd"]] = r
        before = len(seen)
        moved = session.page.evaluate(_SCROLL_JS)
        time.sleep(0.5)
        for r in session.page.evaluate(_EXTRACT_JS):
            if r["cd"] and r["cd"] not in seen:
                seen[r["cd"]] = r
        # 動かない or 増えない が続いたら終了（最下部に到達）。
        if not moved or len(seen) == before:
            stagnant += 1
            if stagnant >= 3:
                break
        else:
            stagnant = 0
    return list(seen.values())

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

        # 描画待ち（最初の数行が出るまで）。
        for attempt in range(15):
            time.sleep(2.0)
            if len(session.page.evaluate(_EXTRACT_JS)) >= 3:
                break
            print(f"  待機中… ({attempt + 1})")
        # 最後までスクロールして全行を集める。
        rows = _collect_all_rows(session)
        session.snapshot("analysis_grid")
        missing = [r for r in rows if not r["code"]]
        codes = {}
        for r in rows:
            codes[r["code"] or "(空欄)"] = codes.get(r["code"] or "(空欄)", 0) + 1
        print(f"\n=== 取得 総行数: {len(rows)} ===")
        print(f"=== 分析用コード分布: {dict(sorted(codes.items(), key=lambda x: str(x[0])))}")
        print(f"=== 分析用コード未入力: {len(missing)}件 ===")
        for r in missing[:30]:
            print(f"  未入力 CD={r['cd']} 名称={r['name']}")
        print("=== サンプル 先頭20行（CD｜名称｜コード）===")
        for r in rows[:20]:
            print(f"  {r['cd']} | {r['name']} | {r['code'] or '(空欄)'}")
    print(f"\n成果物: {artifacts}")
    return 0
