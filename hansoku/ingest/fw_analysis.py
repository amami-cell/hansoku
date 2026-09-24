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


def _open_store_combo_and_list(session) -> list:
    """店舗プルダウンを開いて選択肢（店名）を吸い出す。ng-select/native/EJSコンボに広く対応。"""
    session.page.evaluate(
        """() => {
        // 「店舗」ラベル近傍のコンボ（ng-select / select / .ng-input / combobox）を開く
        const lab = [...document.querySelectorAll('*')].find(
            el => el.children.length === 0 && (el.textContent || '').trim() === '店舗' && el.offsetParent);
        let node = lab ? lab.parentElement : null, trig = null;
        for (let i = 0; i < 8 && node; i++) {
            trig = node.querySelector('ng-select,.ng-select,.ng-input,.ng-arrow-wrapper,select,[role="combobox"],input,button');
            if (trig) break;
            node = node.parentElement;
        }
        if (trig) { trig.click(); trig.dispatchEvent(new MouseEvent('mousedown', {bubbles:true})); }
    }"""
    )
    time.sleep(1.2)
    return session.page.evaluate(
        """() => {
        const out = [];
        const sel = 'li.option,[class*="ng-option"],mat-option,option,[role="option"],ul li';
        for (const o of document.querySelectorAll(sel)) {
            if (!o.offsetParent) continue;
            const t = (o.textContent || '').trim();
            if (t && t.length <= 40 && !t.includes('\\n')) out.push(t);
        }
        return [...new Set(out)];
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

        stores = _open_store_combo_and_list(session)
        print(f"\n=== 店舗プルダウン候補 {len(stores)}件 ===")
        for s in stores[:60]:
            print(f"  {s}")

        # 実データを見たいので1店選ぶ（門真Largo があれば優先、無ければ先頭）。
        target = next((s for s in stores if "Largo" in s or "門真" in s), stores[0] if stores else "")
        picked = _pick_store(session, target) if target else False
        print(f"\n=== 選択した店舗: {target!r}（picked={picked}）===")

        # TreeGrid の描画を粘って待つ（『時間たったら出てくる』）。
        rows = []
        for attempt in range(15):
            time.sleep(2.5)
            rows = session.page.evaluate(_VISUAL_ROWS_JS)
            # コードらしき数字行が複数あればロード完了とみなす
            datarows = [r for r in rows if r and any(c.replace(",", "").isdigit() and len(c) >= 4 for c in r[:1])]
            if len(datarows) >= 5:
                break
            print(f"  待機中… ({attempt + 1}) 視覚行={len(rows)}")
        session.snapshot("analysis_grid")

        print(f"\n=== 視覚行 総数: {len(rows)} ===")
        print("=== 先頭50視覚行（左→右のセル）===")
        for r in rows[:50]:
            print("  | ".join(r))
    print(f"\n成果物: {artifacts}")
    return 0
