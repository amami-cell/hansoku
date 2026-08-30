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


def _click_search(session) -> None:
    """「検索」を押す。ボタンが <input type=button value=検索> のこともあるため、
    click_text（innerText 一致）で駄目なら value 属性まで見て押す。"""
    if session.click_text("検 索", wait=2.0) or session.click_text("検索", wait=2.0):
        return True
    clicked = session.page.evaluate(
        """() => {
        const norm = s => (s || '').replace(/\\s/g, '');
        const els = document.querySelectorAll(
            'button, a, input[type=button], input[type=submit], div[role=button], span, label');
        for (const el of els) {
            if (!el.offsetParent) continue;
            const t = norm(el.innerText) || norm(el.value);
            if (t.includes('検索')) {
                el.click();
                el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                return true;
            }
        }
        return false;
    }"""
    )
    session._settle(2.0)
    return bool(clicked)


def _read_month(session) -> str | None:
    """画面上の「YYYY年MM月」を "YYYY-MM" にして返す。"""
    txt = session.page.evaluate(
        """() => {
        const re = /((19|20)\\d{2})年\\s*(\\d{1,2})月/;
        for (const el of document.querySelectorAll('input,div,span,td,li')) {
            if (!el.offsetParent) continue;
            const s = (el.value || el.innerText || '');
            const m = s.match(re);
            if (m) return m[1] + '-' + String(m[3]).padStart(2, '0');
        }
        return null;
    }"""
    )
    return txt


# 月別予算登録は表のセルが input で出来ている。行ラベル「売上高（税抜き）」も
# テキストではなく読み取り専用 input の value。そのため「値が売上高を含む input」を
# 見つけ、DOM順で次に来る数字入りの input（＝売上高の金額）を返す。
# 例: [売上高（税抜き）] [20,500,000] [（客数）] [8,009] [（客単価）] [2,560]
_SALES_JS = """() => {
    const inputs = [...document.querySelectorAll('input')].filter(
        i => i.offsetParent && !['button', 'checkbox', 'radio', 'submit'].includes(i.type));
    for (let i = 0; i < inputs.length; i++) {
        if ((inputs[i].value || '').replace(/\\s/g, '').includes('売上高')) {
            for (let j = i + 1; j < inputs.length; j++) {
                const nv = (inputs[j].value || '').trim();
                if (/[0-9]/.test(nv)) return nv;   // 最初の数字入り＝金額
                if (/[（(]/.test(nv)) break;        // 次のラベル（客数）に達したら空扱い
            }
        }
    }
    return null;
}"""


def _read_sales_budget(session, retries: int = 12, delay: float = 0.5) -> int | None:
    """「売上高（税抜き）」行の入力値（円）を返す。検索直後は非同期で値が
    入るので、非空になるまで少し粘る。"""
    for _ in range(retries):
        val = session.page.evaluate(_SALES_JS)
        if val is not None:
            digits = "".join(ch for ch in str(val) if ch.isdigit())
            if digits:
                return int(digits)
        time.sleep(delay)
    return None


def _store_text(session) -> str:
    """コンボボックスに今表示されている店舗テキスト。"""
    return session.page.evaluate(
        """() => {
        const inp = document.querySelector('store-combo-box input.form-control, app-combobox input.form-control');
        return inp ? (inp.value || '').trim() : '';
    }"""
    )


def _diag_budget(session) -> None:
    """検索後の画面を丸ごと吸い出す。売上高グリッドが出ているか、
    出ていないなら何が出ているかを、テキストで確認するため。"""
    session.snapshot("budget_after_search")
    info = session.page.evaluate(
        """() => {
        const clip = s => (s || '').replace(/\\s+/g, ' ').trim();
        // 売上/税抜/予算 を含む葉要素（グリッドが出ていれば行ラベルが拾える）
        const hits = [];
        for (const el of document.querySelectorAll('*')) {
            if (el.children.length) continue;
            const t = clip(el.innerText);
            if (t && /売上|税抜|予算/.test(t)) hits.push(el.tagName.toLowerCase() + ':' + t.slice(0, 24));
        }
        // 可視 input を行ラベルごとにまとめる
        const rows = [], byLabel = new Map();
        for (const inp of document.querySelectorAll('input')) {
            if (!inp.offsetParent) continue;
            if (['button', 'checkbox', 'radio', 'submit'].includes(inp.type)) continue;
            let node = inp, label = '';
            for (let i = 0; i < 6 && node; i++) {
                node = node.parentElement; if (!node) break;
                const leaf = [...node.querySelectorAll('*')].find(
                    e => !e.children.length && clip(e.innerText) && e.tagName !== 'INPUT' && e.tagName !== 'BUTTON');
                if (leaf) { label = clip(leaf.innerText); break; }
            }
            if (!byLabel.has(label)) { byLabel.set(label, []); rows.push(label); }
            byLabel.get(label).push(clip(inp.value));
        }
        // 押せる要素（検索ボタンの正体を確認）
        const btns = [];
        for (const el of document.querySelectorAll('button, input[type=button], input[type=submit], a')) {
            if (!el.offsetParent) continue;
            const t = clip(el.innerText) || clip(el.value);
            if (t && t.length < 20) btns.push(el.tagName.toLowerCase() + ':' + t);
        }
        return {
            url: location.href,
            iframes: document.querySelectorAll('iframe').length,
            tables: document.querySelectorAll('table').length,
            inputs: document.querySelectorAll('input').length,
            hits: [...new Set(hits)].slice(0, 30),
            rows: rows.slice(0, 40).map(l => ({ label: l, vals: (byLabel.get(l) || []).slice(0, 12) })),
            btns: [...new Set(btns)].slice(0, 30),
        };
    }"""
    )
    print(f"[budget]   診断: 店舗='{_store_text(session)}' url={info.get('url')}")
    print(f"[budget]   iframe={info.get('iframes')} table={info.get('tables')} input={info.get('inputs')}")
    print(f"[budget]   売上/税抜/予算を含む要素: {info.get('hits')}")
    print(f"[budget]   押せる要素: {info.get('btns')}")
    for r in info.get("rows", []):
        print(f"[budget]   [{r['label']}] {' | '.join(r['vals'])}")


_LOOKS_STORE = (
    "/店|すさび|ぎふや|GOLD|Largo|UMAMI|ARATA|んだんだ|たぬき|ちゃー|"
    "たいだい|NagaGutsu|熊|ひよこ|泡|CRAFTMAN|寿司/"
)


def _open_store_ngselect(page) -> bool:
    """店舗ラベル近傍の ng-select を開く。"""
    return page.evaluate(
        """() => {
        const lab = [...document.querySelectorAll('*')].find(
            el => el.children.length === 0 && (el.textContent || '').trim() === '店舗' && el.offsetParent);
        let node = lab ? lab.parentElement : null, trig = null;
        for (let i = 0; i < 8 && node; i++) {
            trig = node.querySelector('ng-select,.ng-select,.ng-input,.ng-value-container,.ng-arrow-wrapper');
            if (trig) break;
            node = node.parentElement;
        }
        if (trig) { trig.click(); return true; }
        return false;
    }"""
    )


def _combo_options(session) -> list[dict]:
    """月別予算登録の店舗コンボボックス（store-combo-box / app-combobox）の
    選択肢を返す。各 li.option は value=FW店舗コード(0埋め) / title=店名。
    d-none で隠れているが DOM にはあるので、可視判定はしない。"""
    return session.page.evaluate(
        """() => {
        const out = [];
        for (const li of document.querySelectorAll(
                'store-combo-box li.option, app-combobox li.option, li.option')) {
            const value = (li.getAttribute('value') || '').trim();
            const name = (li.getAttribute('title') || li.textContent || '').trim();
            if (value) out.push({ value, name });
        }
        return out;
    }"""
    )


def _select_combo(session, value: str) -> bool:
    """店舗コンボボックスを開いて value の選択肢をクリックする。"""
    page = session.page
    page.evaluate(
        """() => {
        const btn = document.querySelector(
            'store-combo-box .dropdown-btn, app-combobox .dropdown-btn, .combobox .dropdown-btn');
        if (btn) btn.click();
    }"""
    )
    time.sleep(0.4)
    ok = page.evaluate(
        """(value) => {
        const li = document.querySelector(
            `store-combo-box li.option[value="${value}"], app-combobox li.option[value="${value}"], li.option[value="${value}"]`);
        if (li) { li.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); return true; }
        return false;
    }""",
        value,
    )
    time.sleep(0.4)
    return bool(ok)


def _store_options(session) -> list[str]:
    """店舗ドロップダウンの選択肢名を返す。<select> か ng-select を自動判別する。"""
    page = session.page
    native = page.evaluate(
        """() => {
        const clip = s => (s || '').trim();
        const looks = t => """
        + _LOOKS_STORE
        + """.test(t);
        for (const sel of document.querySelectorAll('select')) {
            const o = [...sel.options].map(x => clip(x.textContent)).filter(Boolean);
            if (o.length > 3 && o.some(looks)) return o;
        }
        return null;
    }"""
    )
    if native:
        session._store_kind = "select"
        return native

    # ng-select：開かないと選択肢が出ない
    _open_store_ngselect(page)
    time.sleep(0.7)
    opts = page.evaluate(
        """() => [...document.querySelectorAll('.ng-option,[class*="ng-option"],li.option')]
            .filter(o => o.offsetParent).map(o => (o.textContent || '').trim()).filter(Boolean)"""
    )
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    session._store_kind = "ng" if opts else "none"
    diag = page.evaluate(
        """() => {
        const has = t => [...document.querySelectorAll('button,a,li,span,div')]
            .some(e => e.offsetParent && (e.innerText || '').replace(/\\s/g,'').includes(t));
        // 「店舗」ラベルの入っている行コンテナの outerHTML（先頭のみ）
        const lab = [...document.querySelectorAll('*')].find(
            el => el.children.length === 0 && (el.textContent || '').trim() === '店舗' && el.offsetParent);
        let html = '';
        if (lab) {
            let node = lab;
            for (let i = 0; i < 4 && node.parentElement; i++) node = node.parentElement;
            html = (node.outerHTML || '').replace(/\\s+/g, ' ').slice(0, 900);
        }
        return {
            url: location.href,
            selects: document.querySelectorAll('select').length,
            ng: document.querySelectorAll('ng-select,.ng-select').length,
            hasSearch: has('検索'), hasPrev: has('前月'), hasNext: has('翌月'),
            storeArea: html,
        };
    }"""
    )
    print(f"[budget] セレクタ診断: kind={session._store_kind} 選択肢={len(opts)}")
    print(f"[budget]   url={diag.get('url')}")
    print(f"[budget]   select={diag.get('selects')} ng={diag.get('ng')} "
          f"検索={diag.get('hasSearch')} 前月={diag.get('hasPrev')} 翌月={diag.get('hasNext')}")
    print(f"[budget]   店舗エリアHTML: {diag.get('storeArea')}")
    return opts


def _select_store(session, name: str) -> bool:
    """店舗ドロップダウンで name を選ぶ（<select> / ng-select 両対応）。"""
    page = session.page
    if getattr(session, "_store_kind", "") == "select":
        ok = page.evaluate(
            """(name) => {
            const clip = s => (s || '').trim();
            for (const sel of document.querySelectorAll('select')) {
                const opt = [...sel.options].find(o => clip(o.textContent) === name);
                if (opt) { sel.value = opt.value; sel.dispatchEvent(new Event('change', {bubbles: true})); return true; }
            }
            return false;
        }""",
            name,
        )
        time.sleep(0.5)
        return bool(ok)

    # ng-select
    _open_store_ngselect(page)
    time.sleep(0.5)
    ok = page.evaluate(
        """(name) => {
        const o = [...document.querySelectorAll('.ng-option,[class*="ng-option"],li.option')]
            .find(x => (x.textContent || '').trim() === name);
        if (o) { o.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true})); return true; }
        return false;
    }""",
        name,
    )
    time.sleep(0.5)
    return bool(ok)


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


def ingest(
    warehouse,
    master,
    *,
    artifacts: Path,
    months_back: int = 3,
    store_limit: int | None = None,
    dry_run: bool = False,
) -> int:
    """月別予算登録から各店×直近数ヶ月の「売上高（税抜き）」を読み、売上予算として取り込む。

    画面は1ヶ月ずつ表示。店舗を選び→検索→当月を読む→前月へ→…を繰り返す。
    表示中の月をそのままラベルにするので、月がずれても取り違えない。
    """
    from datetime import date as _date
    from datetime import datetime, timezone

    from ..model import (
        GRAIN_MONTH,
        KIND_FINAL,
        METRIC_SALES_BUDGET,
        ActualRow,
    )

    source = "fw_budget"
    ingested_at = datetime.now(timezone.utc)
    collected: list[tuple[str, str, int]] = []
    unresolved: list[str] = []

    active_by_code = {s.store_code: s for s in master.active}

    with fw_session(artifacts) as session:
        _open_monthly_budget(session)
        options = _combo_options(session)
        print(f"[budget] 店舗コンボボックス {len(options)}件")
        # 稼働24店（store_code一致）に絞る。value は0埋めFWコードなので0を外して照合
        targets = []
        for opt in options:
            code = opt["value"].lstrip("0")
            store = active_by_code.get(code) or master.find_by_name(opt["name"])
            if store and store.active:
                targets.append((opt["value"], store))
        print(f"[budget] マスタと一致した稼働店 {len(targets)}件")
        if store_limit:
            targets = targets[:store_limit]

        for ti, (value, store) in enumerate(targets):
            name = store.store_name
            if not _select_combo(session, value):
                print(f"[budget] 店舗選択に失敗: {name} ({value})")
                continue
            _click_search(session)
            if ti == 0:
                # 最初の1店だけ、選択・グリッド読み取りの状態を診断出力する
                _diag_budget(session)
            for k in range(months_back):
                if k > 0:
                    session.click_text("前月", wait=1.2)
                    _click_search(session)
                month = _read_month(session)
                budget_val = _read_sales_budget(session)
                if month and budget_val is not None:
                    collected.append((store.store_code, month, budget_val))
                    print(f"  {store.store_code} {name[:14]} {month} 売上予算 {budget_val:,}")
                else:
                    print(f"  {store.store_code} {name[:14]} 読み取り失敗 (month={month}, val={budget_val})")
            # 次の店のため当月へ戻す
            for _ in range(months_back - 1):
                session.click_text("翌月", wait=0.6)

    if unresolved:
        print(f"[budget] マスタ未解決の店舗（スキップ）: {unresolved}")
    print(f"[budget] 収集 {len(collected)} 件")

    if dry_run:
        print("[budget] dry-run のため書き込みはしません")
        return 0

    rows = [
        ActualRow(
            store_code=code,
            date=_date(int(m[:4]), int(m[5:7]), 1),
            grain=GRAIN_MONTH,
            metric=METRIC_SALES_BUDGET,
            value=float(value),
            kind=KIND_FINAL,
            source=source,
            ingested_at=ingested_at,
        )
        for (code, m, value) in collected
    ]
    warehouse.ensure_schema()
    loaded = warehouse.replace_actuals(rows)
    print(f"[budget] warehouse へ {loaded} 件 書き込みました")
    return 0


# ── 店長会資料DL（損益管理 → 実績管理業務 → 店長会資料DL）──────────────────
# 店長会議用のP&L系ダウンロード。予実達成率・原価率・粗利・客数・客単価・順位など
# 月次KPIが1ファイルに揃う（探索で確認: 出力形式 Excel/CSV・出力分類 全商品/部門/
# グループ）。まず probe でCSVを1本落として列構成を確認し、それに合わせて ingest を書く。
MANAGER_MENU = ("損益管理", "実績管理業務", "店長会資料DL")


def _open_manager_dl(session) -> None:
    for label in MANAGER_MENU:
        if not session.click_text(label):
            session.snapshot(f"missing_{label}")
            session.dump_clickables(f"failed_{label}")
            raise FWError(f"「{label}」に進めませんでした")
        session.snapshot(f"opened_{label}")


def _download_csv_any(session, artifacts: Path, name: str, buttons: list[str]) -> Path | None:
    """複数のボタン名候補で「CSV/ダウンロード」を押してファイルを保存する。"""
    page = session.page
    try:
        with page.expect_download(timeout=30000) as dl_info:
            clicked = False
            for b in buttons:
                if session.click_text(b, wait=1.0):
                    clicked = True
                    break
            if not clicked:
                page.evaluate(
                    """(labels) => { const norm=s=>(s||'').replace(/\\s/g,'');
                    for (const el of document.querySelectorAll('button,a,label,span,div')) {
                      const t=norm(el.innerText||el.textContent);
                      if (labels.some(l=>t.includes(l)) && el.offsetParent) { el.click(); return true; } }
                    return false; }""",
                    buttons,
                )
        download = dl_info.value
        path = artifacts / name
        download.save_as(str(path))
        return path
    except Exception as exc:  # noqa: BLE001
        session.snapshot("mgrdl_failed")
        session.dump_clickables("mgrdl_failed")
        print(f"[店長会DL] ダウンロード失敗: {exc}")
        return None


def _dump_manager_form(session, tag: str) -> dict:
    """店長会資料DL のフォーム構成（input/radio/select/label）を吸い出して印字する。

    dump_clickables は radio/checkbox/日付入力を拾わないため、ここで拾う。
    """
    info = session.page.evaluate(
        r"""() => {
        const clip = s => (s || '').replace(/\s+/g, ' ').trim();
        const near = el => {                     // 近傍のラベル文言
            let n = el, out = '';
            for (let i = 0; i < 5 && n; i++) {
                n = n.parentElement; if (!n) break;
                const leaf = [...n.querySelectorAll('*')].find(
                    e => e.children.length === 0 && clip(e.innerText) &&
                         !['INPUT','BUTTON'].includes(e.tagName));
                if (leaf) { out = clip(leaf.innerText); break; }
            }
            return out.slice(0, 24);
        };
        const inputs = [...document.querySelectorAll('input')].filter(i => i.offsetParent)
            .map(i => ({type: i.type, id: i.id || '', name: i.name || '',
                        ph: i.placeholder || '', val: clip(i.value),
                        checked: i.checked, label: near(i)}));
        const radios = inputs.filter(i => i.type === 'radio');
        const dates  = [...document.querySelectorAll('input')].filter(i => i.offsetParent &&
                        (/date|day|from|to|period|期間/i.test(i.id + i.name + i.placeholder) ||
                         /^\d{4}\/\d{1,2}\/\d{1,2}$/.test((i.value||'').trim())))
            .map(i => ({id: i.id||'', name: i.name||'', ph: i.placeholder||'', val: clip(i.value)}));
        const selects = [...document.querySelectorAll('select')].filter(s => s.offsetParent)
            .map(s => ({id: s.id||'', name: s.name||'', label: near(s),
                        opts: [...s.options].slice(0, 12).map(o => clip(o.textContent))}));
        const labels = [...document.querySelectorAll('label')].filter(l => l.offsetParent)
            .map(l => clip(l.innerText)).filter(Boolean).slice(0, 30);
        return {n_inputs: inputs.length, radios, dates, selects, labels,
                text_inputs: inputs.filter(i => ['text','tel',''].includes(i.type))};
    }"""
    )
    print(f"[店長会DL] フォーム構成（{tag}）: input {info.get('n_inputs')}個")
    for r in info.get("radios", [])[:20]:
        print(f"    radio [{r['label']}] name={r['name']} id={r['id']} val={r['val']} checked={r['checked']}")
    for d in info.get("dates", [])[:8]:
        print(f"    date  id={d['id']} name={d['name']} ph={d['ph']} val={d['val']}")
    for s in info.get("selects", [])[:8]:
        print(f"    select[{s['label']}] name={s['name']} opts={s['opts']}")
    for t in info.get("text_inputs", [])[:14]:
        print(f"    text  [{t['label']}] id={t['id']} name={t['name']} ph={t['ph']} val={t['val']}")
    if info.get("labels"):
        print(f"    labels: {info['labels']}")
    return info


def _fill_manager_period(session, ym: str) -> bool:
    """期間（from〜to）を対象月に設定する。空欄でも入るよう複数手法を試す。ym='YYYY-MM'。"""
    import calendar

    y, m = int(ym[:4]), int(ym[5:7])
    last = calendar.monthrange(y, m)[1]
    d_from, d_to = f"{y}/{m:02d}/01", f"{y}/{m:02d}/{last:02d}"
    return bool(
        session.page.evaluate(
            r"""([f, t]) => {
        const set = (el, v) => {
            el.focus(); el.value = v;
            for (const ev of ['input','change','blur'])
                el.dispatchEvent(new Event(ev, {bubbles: true}));
            el.dispatchEvent(new KeyboardEvent('keyup', {key: ' ', bubbles: true}));
        };
        // 期間らしいテキスト入力（値が空/日付形式）を左→右に2つ拾う
        const cand = [...document.querySelectorAll('input')].filter(i => i.offsetParent &&
            ['text','tel',''].includes(i.type) &&
            (!(i.value||'').trim() || /^\d{4}\/\d{1,2}\/\d{1,2}$/.test((i.value||'').trim())));
        if (cand.length >= 2) { set(cand[0], f); set(cand[1], t); return true; }
        if (cand.length === 1) { set(cand[0], f); return true; }
        // type=date もフォールバック
        const iso = s => s.replace(/\//g, '-').replace(/-(\d)-/g,'-0$1-').replace(/-(\d)$/,'-0$1');
        const dd = [...document.querySelectorAll('input[type=date]')].filter(i => i.offsetParent);
        if (dd.length >= 2) { set(dd[0], iso(f)); set(dd[1], iso(t)); return true; }
        return false;
    }""",
            [d_from, d_to],
        )
    )


def probe_manager_dl(artifacts: Path, *, month: str = "2026-07") -> int:
    """店長会資料DL の画面に入り、フォーム構成を吸い出し→期間を設定→出力し、
    落ちたファイル（CSV/Excel/ZIP）の中身か、モーダル等の後続画面を印字する。"""
    import sys

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    with fw_session(artifacts) as session:
        try:
            session.page.set_default_timeout(9000)
            session.page.set_default_navigation_timeout(15000)
        except Exception:  # noqa: BLE001
            pass
        _open_manager_dl(session)
        items = session.dump_clickables("mgrdl_screen")
        print(f"[店長会DL] 画面の操作要素 {len(items)}件:")
        for it in items[:40]:
            txt = " ".join((it.get("text") or "").split())[:44]
            print(f"    {it.get('tag',''):<8} {txt}")
        # フォームの実体（radio/date/select）を吸い出す
        _dump_manager_form(session, "初期")
        # 出力形式=CSV を選ぶ（radio/label/text いずれでも）
        for lbl in ("CSV", "ＣＳＶ"):
            if session.click_text(lbl, wait=0.4):
                break
        # 期間を対象月に設定
        ok = _fill_manager_period(session, month)
        print(f"[店長会DL] 期間={month} 設定 {'OK' if ok else '失敗（空欄のまま）'}")
        session.snapshot("mgrdl_ready")
        _dump_manager_form(session, "設定後")

        page = session.page
        path = None
        try:
            with page.expect_download(timeout=30000) as dl_info:
                if not session.click_text("出力", wait=1.2):
                    page.evaluate(
                        """() => { for (const b of document.querySelectorAll('button,a')) {
                          if ((b.innerText||'').replace(/\\s/g,'').includes('出力') && b.offsetParent)
                          { b.click(); return; } } }"""
                    )
            download = dl_info.value
            fname = download.suggested_filename or "manager_dl.bin"
            path = artifacts / fname
            download.save_as(str(path))
            print(f"[店長会DL] ダウンロード成功: {fname}")
        except Exception as exc:  # noqa: BLE001
            print(f"[店長会DL] 出力クリック後にダウンロード無し: {exc}")
            session.snapshot("mgrdl_after_output")
            after = session.dump_clickables("mgrdl_after_output")
            print(f"[店長会DL] 出力後の操作要素 {len(after)}件（モーダル/形式選択の可能性）:")
            for it in after[:40]:
                txt = " ".join((it.get("text") or "").split())[:44]
                print(f"    {it.get('tag',''):<8} {txt}")
            _dump_manager_form(session, "出力後")

        if path:
            raw = path.read_bytes()
            suffix = path.suffix.lower()
            print(f"[店長会DL] 取得 {len(raw)} bytes / {path.name}")
            if suffix in (".csv", ".txt") or raw[:4] not in (b"PK\x03\x04",):
                text, enc = _decode(raw)
                lines = text.splitlines()
                print(f"[店長会DL] テキスト {len(lines)}行 / enc={enc}")
                print("---- 先頭40行 ----")
                for line in lines[:40]:
                    print(line[:400])
            else:
                # Excel/ZIP の場合はシート/エントリ名だけ確認
                import io
                import zipfile

                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                        names = zf.namelist()
                        print(f"[店長会DL] ZIP/xlsx エントリ {len(names)}件: {names[:20]}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[店長会DL] バイナリ（解析不可）: {exc}")
    print(f"\n成果物: {artifacts}")
    return 0
