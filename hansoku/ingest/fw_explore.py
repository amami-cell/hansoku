"""
FW の画面構造を調べる探索モード。

セレクタを当て推量で書くと、失敗したときに「何が違うのか」が分からず
試行錯誤が長引く。先に実物のメニュー名・ボタン名を吸い出しておき、
それを見てから取り込み処理を書く。

指定された順にメニューをクリックし、各段階で
スクリーンショットとクリック可能要素の一覧を成果物として残す。
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .fw_browser import fw_session


def explore(path: Sequence[str], artifacts: Path) -> int:
    """``path`` の各文字を順にクリックしながら、画面構造を記録する。"""
    with fw_session(artifacts) as session:
        items = session.dump_clickables("top")
        print(f"[ログイン後] クリックできる要素 {len(items)} 件")
        for item in items[:60]:
            print(f"    {item['tag']:<8} {item['text']}")

        for step, label in enumerate(path, start=1):
            print(f"\n--- 「{label}」をクリック ---")
            if not session.click_text(label):
                session.snapshot(f"missing_{label}")
                print(f"  ⚠ 「{label}」が見つかりませんでした。ここまでの構造を残します。")
                session.dump_clickables(f"failed_at_{label}")
                return 1
            session.snapshot(f"after_{step}_{label}")
            items = session.dump_clickables(f"after_{step}_{label}")
            print(f"  遷移後 URL: {session.page.url}")
            print(f"  クリックできる要素 {len(items)} 件")
            for item in items[:60]:
                print(f"    {item['tag']:<8} {item['text']}")

    print(f"\n成果物: {artifacts}")
    return 0


def list_stores(artifacts: Path) -> int:
    """
    販売管理の帳票で「店舗選択 → エリア:イニシエート」を開き、
    現れる全店舗の一覧を吸い出す。エリア分類のもとになる。
    """
    import time

    from .fw_browser import fw_session

    with fw_session(artifacts) as session:
        # 店舗業務のどれか1つの帳票に入れば店舗選択ボタンが出る
        for label in ("販売管理", "店舗業務", "月別日別売上推移"):
            if not session.click_text(label):
                session.snapshot(f"missing_{label}")
                print(f"⚠ 「{label}」に進めませんでした")
                session.dump_clickables(f"failed_{label}")
                return 1
            session.snapshot(f"opened_{label}")

        # 店舗選択ボタン
        if not session.click_text("店舗選択"):
            session.snapshot("missing_store_select")
            session.dump_clickables("no_store_select")
            print("⚠ 店舗選択ボタンが見つかりません")
            return 1
        time.sleep(2)
        session.snapshot("store_select_open")

        # エリアのドロップダウンで「イニシエート」を選ぶ
        picked = session.page.evaluate(
            """() => {
            const label = [...document.querySelectorAll('*')]
                .find(el => el.children.length === 0 &&
                            el.textContent.trim() === 'エリア' && el.offsetParent);
            if (!label) return {ok:false, why:'no-area-label'};
            // ラベル近傍の ng-select を開く
            let node = label.parentElement, trigger = null;
            for (let i = 0; i < 8 && node; i++) {
                trigger = node.querySelector('ng-select,.ng-select,.ng-input,.ng-value-container,.ng-arrow-wrapper');
                if (trigger) break;
                node = node.parentElement;
            }
            if (trigger) trigger.click();
            return {ok:true, opened: !!trigger};
        }"""
        )
        time.sleep(0.8)
        session.page.evaluate(
            """() => {
            const opts = [...document.querySelectorAll('li.option,[class*="ng-option"],mat-option')];
            const t = opts.find(o => o.textContent.trim() === 'イニシエート' ||
                                     o.getAttribute('title') === 'イニシエート');
            if (t) t.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
        }"""
        )
        time.sleep(2)
        session.snapshot("area_initiate")

        # 左パネルに並ぶ全店舗名を吸い出す
        stores = session.page.evaluate(
            """() => {
            // チェックボックス付きの店舗行 / li / option を広めに拾う
            const out = [];
            const seen = new Set();
            const sel = 'li, tr, label, [class*="item"], [class*="store"], [class*="option"]';
            for (const el of document.querySelectorAll(sel)) {
                if (!el.offsetParent) continue;
                const t = (el.innerText || '').trim();
                if (!t || t.length > 40 || t.includes('\n')) continue;
                // 店名っぽいものだけ（コード付き "0001015_..." や 店名）
                if (/^0*\d{3,}[_\s]/.test(t) || /店|すさび|ぎふや|GOLD|Largo|UMAMI|ARATA|んだんだ|たぬきや|ちゃーちゃん|たいだい|NagaGutsu|熊の鳥焼|ひよこ|泡喰|CRAFTMAN/.test(t)) {
                    if (!seen.has(t)) { seen.add(t); out.push(t); }
                }
            }
            return out;
        }"""
        )
        session.dump_clickables("store_list_raw")
        print(f"\n=== エリア:イニシエート の店舗一覧（{len(stores)}件）===")
        for name in stores:
            print(f"  {name}")

    print(f"\n成果物: {artifacts}")
    return 0
