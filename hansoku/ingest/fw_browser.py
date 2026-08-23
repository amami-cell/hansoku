"""
FW（Foodist Journal / HASSIN）のブラウザ操作。

既存パイプライン（infomart_automation の foodist_journal.py）で確立された作法を踏襲する。
このサイトは Angular 4.x の SPA で、素直に操作できない箇所がいくつかある:

  * ログインフォームはオーバーレイに覆われ、通常の click / fill が届かない
    → JS で値を入れ、input / change / keyup を自分で発火させる
  * page.goto() で直接URLへ飛ぶとセッションが失われ、ログイン画面へ戻される
    → 画面内のメニューをクリックして遷移する
  * ドロップダウンの選択肢は開いたときだけ DOM に現れ、CSS で不可視のため
    Playwright の click が使えない → dispatchEvent で直接叩く

失敗したときに何が起きたか分からないと直しようがないので、
各段階でスクリーンショットとページ構造を残す。
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

LOGIN_URL = "https://www2.pros-asp.net/corp/hassin/index"
TIMEOUT_MS = 30000


class FWError(RuntimeError):
    """FW の操作に失敗した。"""


class FWSession:
    """ログイン済みの FW 画面を操作する。"""

    def __init__(self, page, artifacts: Path):
        self.page = page
        self.artifacts = Path(artifacts)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._step = 0

    # ── 記録 ──────────────────────────────────────────────────────────────
    def snapshot(self, name: str) -> None:
        """スクリーンショットとURLを残す。失敗しても本処理は止めない。"""
        self._step += 1
        stem = f"{self._step:02d}_{name}"
        try:
            self.page.screenshot(path=str(self.artifacts / f"{stem}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.artifacts / f"{stem}.url.txt").write_text(self.page.url, encoding="utf-8")
        except Exception:
            pass

    def dump_clickables(self, name: str) -> list[dict]:
        """
        いま画面に見えているクリックできる要素の一覧を残す。

        メニュー名やボタン名を実物から拾うためのもの。
        セレクタを当て推量で書くより、これを見てから書いた方が確実に速い。
        """
        items = self.page.evaluate(
            """() => {
            const seen = new Set();
            const out = [];
            const sel = 'a, button, [role="button"], li, div[class*="tile"], '
                      + 'div[class*="card"], div[class*="menu"], input[type="button"], '
                      + 'input[type="submit"]';
            for (const el of document.querySelectorAll(sel)) {
                if (!el.offsetParent) continue;               // 非表示は除く
                const text = (el.innerText || el.value || '').trim();
                if (!text || text.length > 40) continue;
                const key = el.tagName + '|' + text;
                if (seen.has(key)) continue;
                seen.add(key);
                const r = el.getBoundingClientRect();
                out.push({
                    tag: el.tagName.toLowerCase(),
                    text: text,
                    cls: (el.className || '').toString().slice(0, 80),
                    href: el.getAttribute('href') || '',
                    routerlink: el.getAttribute('routerlink') || '',
                    x: Math.round(r.x), y: Math.round(r.y),
                });
            }
            return out;
        }"""
        )
        self._step += 1
        path = self.artifacts / f"{self._step:02d}_{name}.clickables.json"
        path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        return items

    # ── ログイン ──────────────────────────────────────────────────────────
    def login(self) -> None:
        user_id = os.environ.get("FOODIST_JOURNAL_USER_ID", "").strip()
        password = os.environ.get("FOODIST_JOURNAL_PASSWORD", "").strip()
        if not user_id or not password:
            raise FWError(
                "FOODIST_JOURNAL_USER_ID / FOODIST_JOURNAL_PASSWORD が未設定です。"
                " GitHub Secrets に登録してください。"
            )

        self.page.goto(LOGIN_URL, timeout=TIMEOUT_MS)
        self.page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        self.snapshot("login_form")

        # オーバーレイに阻まれるため、JS で値を入れて Angular の変更検知を起こす
        self.page.evaluate(
            """([user, password]) => {
            const fill = (el, value) => {
                if (!el) return;
                el.focus();
                el.value = value;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {key: ' ', bubbles: true}));
            };
            fill(document.querySelector('input.form-control[type="text"]'), user);
            fill(document.querySelector('input[type="password"]'), password);
        }""",
            [user_id, password],
        )
        time.sleep(1)
        self.page.evaluate("() => { const b = document.querySelector('button'); if (b) b.click(); }")

        try:
            self.page.wait_for_url("**/app/**", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

        if self.page.url.rstrip("/").endswith("/index"):
            self.snapshot("login_failed")
            raise FWError(
                f"ログインに失敗しました（URL: {self.page.url}）。"
                " ID / パスワードを確認してください。"
            )

        self.close_dialogs()
        self.snapshot("after_login")

    def close_dialogs(self) -> None:
        """ログイン後に出るお知らせダイアログ等を閉じる。"""
        time.sleep(1)
        self.page.keyboard.press("Escape")
        time.sleep(0.5)
        self.page.evaluate(
            """() => {
            const labels = ['閉じる', 'Close', 'OK', '×'];
            for (const label of labels) {
                for (const el of document.querySelectorAll('button, a, div[role="button"]')) {
                    if (el.innerText && el.innerText.trim().includes(label)) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        }"""
        )
        time.sleep(1)

    # ── 遷移 ──────────────────────────────────────────────────────────────
    def click_text(self, text: str, *, wait: float = 2.5) -> bool:
        """
        画面上の文字でクリックする。

        :has-text() は部分一致なので、入れ子のメニューでは親にもマッチする。
        たとえば「販売管理」の li は子の「店舗業務」を含むため、
        li:has-text("店舗業務") が親を掴んでしまい、メニューを開き直すだけになる。
        そのため完全一致（:text-is）を先に試し、駄目なときだけ部分一致へ落とす。

        Angular のオーバーレイに阻まれることもあるので、
        通常クリック → force クリック → JS の順に粘る。
        """
        exact = (
            f'a:text-is("{text}"), button:text-is("{text}"), '
            f'div[role="button"]:text-is("{text}")'
        )
        loose = (
            f'a:has-text("{text}"), button:has-text("{text}"), '
            f'div[role="button"]:has-text("{text}")'
        )
        for selector in (exact, loose):
            for force in (False, True):
                try:
                    loc = self.page.locator(selector)
                    if loc.count() > 0:
                        loc.first.click(force=force, timeout=5000)
                        self._settle(wait)
                        return True
                except Exception:
                    continue

        clicked = self.page.evaluate(
            """(text) => {
            const els = Array.from(document.querySelectorAll(
                'a, button, li, div[role="button"], div[class*="tile"], div[class*="card"]'
            )).filter(el => el.offsetParent);
            // 完全一致を最優先。無ければ、含む要素のうち最も文字数が少ないもの
            // （＝目的の要素に一番近いもの）を選ぶ。親を掴まないため。
            const exact = els.find(el => (el.innerText || '').trim() === text);
            const target = exact || els
                .filter(el => (el.innerText || '').includes(text))
                .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0];
            if (!target) return false;
            target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            return true;
        }""",
            text,
        )
        if clicked:
            self._settle(wait)
        return bool(clicked)

    def _settle(self, wait: float) -> None:
        """クリック後、描画と通信が落ち着くのを待つ。"""
        time.sleep(wait)
        try:
            self.page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
        except Exception:
            pass

    def click_text_or_fail(self, text: str, *, wait: float = 2.0) -> None:
        if not self.click_text(text, wait=wait):
            self.snapshot(f"missing_{text}")
            self.dump_clickables(f"missing_{text}")
            raise FWError(f"画面に「{text}」が見つかりません。")


@contextmanager
def fw_session(artifacts: Path, *, headless: bool = False) -> Iterator[FWSession]:
    """
    ログイン済みの FW セッションを開く。

    既存パイプラインと同じく headless=False で動かし、
    GitHub Actions では xvfb 上で実行する。この構成で実績があるため踏襲する。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=300)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        session = FWSession(page, artifacts)
        try:
            session.login()
            yield session
        finally:
            try:
                browser.close()
            except Exception:
                pass
