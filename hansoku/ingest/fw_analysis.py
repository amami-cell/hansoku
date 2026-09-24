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
        all.push({ top: r.top, left: r.left, txt });
    }
    const isCode = t => /^([1-9]|1[0-9]|2[0-8])$/.test(t);   // 1〜28 のみ（分析用コード）
    const cds = all.filter(a => /^\d{10,}$/.test(a.txt));
    const rows = [];
    for (const cd of cds) {
        // 同じ行＝縦位置が近い葉（分析用コードは表示位置が数px ずれるので ±12 で拾う）
        const row = all.filter(a => Math.abs(a.top - cd.top) <= 12).sort((a, b) => a.left - b.left);
        // 名称：CD の右で最初の「数字始まりでない」テキスト
        let name = '';
        for (const c of row) { if (c.left > cd.left && c.txt && !/^[\d.]/.test(c.txt)) { name = c.txt; break; } }
        // 分析用コード：行の右端にある 1〜28 の数字（無ければ空欄＝未入力）
        let code = '';
        for (const c of row) { if (isCode(c.txt) && c.left > cd.left) code = c.txt; }
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


def _scroll_grid_top(session) -> None:
    """グリッド上でホイールを上へ送り、一番上まで戻す（店切替後の残スクロール対策）。"""
    try:
        session.page.mouse.move(700, 450)
        for _ in range(6):
            session.page.mouse.wheel(0, -20000)
            time.sleep(0.1)
    except Exception:
        pass


def _collect_all_rows(session, *, max_steps: int = 500) -> list[dict]:
    """グリッドを最後まで送りながら全行を集める（商品CDで重複排除）。
    仮想スクロールなので、グリッド上にマウスを置いてホイールで送る（container 探索より確実）。"""
    page = session.page
    seen: dict[str, dict] = {}
    _scroll_grid_top(session)   # 先頭行を取りこぼさない
    stagnant = 0
    for _ in range(max_steps):
        for r in page.evaluate(_EXTRACT_JS):
            if r["cd"] and r["cd"] not in seen:
                seen[r["cd"]] = r
        before = len(seen)
        try:
            page.mouse.wheel(0, 1600)
        except Exception:
            page.evaluate(_SCROLL_JS)
        time.sleep(0.35)
        for r in page.evaluate(_EXTRACT_JS):
            if r["cd"] and r["cd"] not in seen:
                seen[r["cd"]] = r
        if len(seen) == before:
            stagnant += 1
            if stagnant >= 6:
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


def _capture_csv(session, artifacts: Path) -> str | None:
    """画面の「CSV出力」を押してCSVをダウンロード取得し、テキストで返す（cp932想定）。"""
    try:
        with session.page.expect_download(timeout=30000) as dl_info:
            session.page.evaluate(
                """() => {
                const b = [...document.querySelectorAll('button,a,div,span')]
                    .find(e => (e.textContent || '').trim() === 'CSV出力' && e.offsetParent);
                if (b) b.click();
            }"""
            )
        dl = dl_info.value
        raw = Path(dl.path()).read_bytes()
        try:
            dl.save_as(str(artifacts / "analysis_codes.csv"))
        except Exception:
            pass
        for enc in ("cp932", "utf-8-sig", "utf-8"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("cp932", "replace")
    except Exception as e:
        print(f"⚠ CSV出力の取得に失敗: {e}")
        return None


_SELECT_STORE_JS = r"""(code) => {
    const btn = document.querySelector('.dropdown-btn.enabledbutton');
    if (btn) btn.click();
    const li = [...document.querySelectorAll('li.option')]
        .find(o => (o.getAttribute('value') || '').trim() === code);
    if (li) { li.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true})); return true; }
    return false;
}"""


def _combo_state(session) -> str:
    """店舗コンボの『いま選ばれている店名』を拾う（切替が効いたか判定用）。
    選択中の店名は div.combobox-cont の title 属性に入る（診断で確認済み）。"""
    return session.page.evaluate(
        """() => {
        const c = document.querySelector('.combobox-cont[title]');
        if (c) return (c.getAttribute('title') || '').trim();
        const b = document.querySelector('.dropdown-btn');
        return b ? (b.innerText || '').trim().slice(0, 60) : '(取得不可)';
    }"""
    )


def _select_store(session, code: str, name: str, *, tries: int = 6) -> bool:
    """店舗を選び、コンボの表示（combobox-cont の title）が目的店に変わるまで確認する。

    合成イベント（dispatchEvent）だと表示は変わるが Angular のグリッド再読込が
    走らない。Playwright の実クリックは trusted イベントなので、開いた
    ドロップダウンの li を実際にクリックして選ぶ。切替が効かないまま読むと
    『前店の行を別店コードで保存』する事故になるため、表示が目的店に変わった
    ことを必ず確かめる。
    """
    page = session.page
    for _ in range(tries):
        # ドロップダウンを開く（実クリック）。
        try:
            btn = page.locator(".dropdown-btn.enabledbutton")
            if btn.count():
                btn.first.click(timeout=4000, force=True)
        except Exception:
            pass
        time.sleep(0.5)
        # 目的の li を実クリック（trusted → Angular の選択＆再読込が走る）。
        try:
            li = page.locator(f'li.option[value="{code}"]')
            if li.count():
                li.first.click(timeout=4000, force=True)
        except Exception:
            # 実クリックが当たらないときだけ合成イベントで粘る。
            session.page.evaluate(_SELECT_STORE_JS, code)
        for _ in range(4):
            time.sleep(0.6)
            title = _combo_state(session)
            if title and (title == name or name in title or title in name):
                return True
    return False


def _store_options(session) -> list[dict]:
    return session.page.evaluate(
        """() => [...document.querySelectorAll('li.option')]
            .map(li => ({code: (li.getAttribute('value')||'').trim(), name: (li.textContent||'').trim()}))
            .filter(s => s.code)"""
    )


def _first_cd(session) -> str:
    rows = session.page.evaluate(_EXTRACT_JS)
    return rows[0]["cd"] if rows else ""


def _wait_grid(session, tries: int = 20) -> bool:
    for _ in range(tries):
        time.sleep(1.5)
        if len(session.page.evaluate(_EXTRACT_JS)) >= 3:
            return True
    return False


def _wait_grid_change(session, prev_cds: set[str], tries: int = 20) -> bool:
    """店を切り替えたら、前店に無い商品CDが現れる（＝新しい店の表に再読込された）まで待つ。"""
    for _ in range(tries):
        time.sleep(1.5)
        rows = session.page.evaluate(_EXTRACT_JS)
        cds = [r["cd"] for r in rows if r["cd"]]
        if len(cds) >= 3 and any(c not in prev_cds for c in cds):
            return True
    return len(session.page.evaluate(_EXTRACT_JS)) >= 3


def _nav_to_screen(session) -> bool:
    """ログイン直後の画面から 分析用コード設定 まで開く。"""
    for label in NAV:
        if not session.click_text(label):
            session.snapshot(f"missing_{label}")
            print(f"⚠ 「{label}」が見つかりませんでした。")
            return False
    time.sleep(2)
    return True


def ingest(artifacts: Path, db, active_codes: set[str], *, dry_run: bool = False, limit: int | None = None) -> int:
    """イニシエート（active）店の 商品→分析用コード を全店読み取り、Neon に保存。

    1セッション・1ログインで、画面はそのまま店舗コンボだけを切り替えて回す
    （診断で in-place 切替が効くことを確認済み。再ナビは不要でむしろ再読込を壊す）。
    切替が効いたかは combobox の表示（title）で必ず確認し、前店の行を別店コードで
    保存する事故を防ぐ。
    """
    with fw_session(artifacts) as session:
        if not _nav_to_screen(session):
            return 1
        stores = _store_options(session)

        # FWコード(0001006) → アプリコード(1006)。active（イニシエート）だけに絞る。
        targets = []
        for s in stores:
            try:
                app = str(int(s["code"]))
            except ValueError:
                continue
            if app in active_codes:
                targets.append({"app": app, **s})
        if limit:
            targets = targets[:limit]
        print(f"=== 対象（イニシエート）{len(targets)}店 / FW全体 {len(stores)}店 ===")

        grand_rows = grand_missing = fails = 0
        prev_cds: set[str] = set()
        for s in targets:
            if not _select_store(session, s["code"], s["name"]):
                print(f"  {s['app']} {s['name']}: 店舗切替を確認できず（スキップ）")
                fails += 1
                continue
            _scroll_grid_top(session)   # 前店で下まで送った位置が残っていることがある
            # 切替後、前店に無い商品CDが出る＝新しい店の表に再読込された、を待つ。
            if not _wait_grid_change(session, prev_cds):
                print(f"  {s['app']} {s['name']}: グリッドが出ませんでした（スキップ）")
                fails += 1
                continue
            rows = _collect_all_rows(session)
            recs = [
                {
                    "product_code": r["cd"],
                    "product_name": r["name"],
                    "analysis_code": int(r["code"]) if r["code"] else None,
                }
                for r in rows
                if r["cd"]
            ]
            prev_cds = {r["product_code"] for r in recs}
            missing = sum(1 for r in recs if r["analysis_code"] is None)
            if not dry_run and recs:
                db.replace_analysis_codes(s["app"], recs)
            grand_rows += len(recs)
            grand_missing += missing
            print(f"  {s['app']} {s['name']}: {len(recs)}件 / 未入力 {missing}件 {'(dry-run)' if dry_run else '→ 保存'}")
        tail = f"／取得失敗 {fails}店" if fails else ""
        print(f"\n=== 合計 {len(targets)}店 / {grand_rows}件 / 未入力 {grand_missing}件{tail} {'(dry-run・未保存)' if dry_run else '保存済み'} ===")
    return 0


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

        # ホイールで最後まで送りながら全行を集める。
        rows = _collect_all_rows(session)
        session.snapshot("analysis_grid")
        missing = [r for r in rows if not r["code"]]
        codes: dict[str, int] = {}
        for r in rows:
            codes[r["code"] or "(空欄)"] = codes.get(r["code"] or "(空欄)", 0) + 1
        print(f"\n=== 取得 総行数: {len(rows)} ===")
        print(f"=== 分析用コード分布: {dict(sorted(codes.items(), key=lambda x: str(x[0])))}")
        print(f"=== 分析用コード未入力: {len(missing)}件 ===")
        for r in missing[:40]:
            print(f"  未入力 CD={r['cd']} 名称={r['name']}")

        # ── 店を切り替えても表が再読込されない。何が『表示』を起こすのか探す ──
        # trusted 実クリックでも title は変わるが行は出ない＝選択とは別に
        # 表示/検索のトリガーが要る（既定店だけ入場時に自動表示される）。
        # 画面のボタン一覧を採り、それらしきボタンを押して行が出るか試す。
        print("\n=== 店舗切替の診断（表示トリガー探索）===")
        other = next((s for s in stores if not s["code"].endswith("1006")), None)
        if other:
            print(f"  → 切替先: {other['code']}:{other['name']}")
            # trusted 実クリックで店を選ぶ（title は変わるはず）。
            try:
                session.page.locator(".dropdown-btn.enabledbutton").first.click(timeout=4000, force=True)
                time.sleep(0.5)
                session.page.locator(f'li.option[value="{other["code"]}"]').first.click(timeout=4000, force=True)
            except Exception as e:
                print(f"  実クリック失敗: {e}")
            time.sleep(1.5)
            print(f"  選択直後: コンボ表示={_combo_state(session)!r} / グリッド行数={len(session.page.evaluate(_EXTRACT_JS))}")

            # 画面の全ボタン/リンクを列挙（表示・検索・再表示・絞込・実行・更新 を探す）。
            btns = session.page.evaluate(
                """() => {
                const out = [];
                for (const el of document.querySelectorAll('button, a, input[type=button], input[type=submit], [role=button]')) {
                    if (!el.offsetParent) continue;
                    const t = (el.innerText || el.value || '').trim();
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    out.push({t: t.slice(0,24), tag: el.tagName.toLowerCase(),
                              cls: (el.className||'').toString().slice(0,40),
                              dis: !!el.disabled, x: Math.round(r.x), y: Math.round(r.y)});
                }
                return out;
            }"""
            )
            print(f"  画面のボタン/リンク {len(btns)}個:")
            for b in btns[:40]:
                print(f"    {b}")

            # それらしきボタンを順に押して、行が出るか見る。
            for label in ("表示", "再表示", "検索", "絞込", "絞り込み", "実行", "更新", "決定", "OK"):
                hit = session.page.evaluate(
                    """(label) => {
                    const el = [...document.querySelectorAll('button, a, input[type=button], input[type=submit], [role=button]')]
                        .find(e => e.offsetParent && !e.disabled && ((e.innerText||e.value||'').trim() === label));
                    if (!el) return false;
                    el.click(); return true;
                }""",
                    label,
                )
                if not hit:
                    continue
                time.sleep(3.0)
                n = len(session.page.evaluate(_EXTRACT_JS))
                print(f"    ボタン「{label}」押下後 → グリッド行数: {n}")
                if n >= 3:
                    print(f"    ★ 表示トリガーは「{label}」")
                    break

            # ボタンで駄目でも、単に生成に時間がかかるだけかもしれない。最大60秒待つ。
            appeared = False
            for i in range(20):
                time.sleep(3.0)
                n = len(session.page.evaluate(_EXTRACT_JS))
                if n >= 3:
                    print(f"    （待機 {(i+1)*3}秒で {n} 行出現）")
                    appeared = True
                    break
            if not appeared:
                print("    60秒待っても行は出ず（ボタン押下も効かず）")
            session.snapshot("analysis_other_store")
    print(f"\n成果物: {artifacts}")
    return 0
