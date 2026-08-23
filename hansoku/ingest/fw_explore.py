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
