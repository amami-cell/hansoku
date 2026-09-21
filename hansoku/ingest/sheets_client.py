"""
Google Sheets の読み取り口。

既存パイプライン（infomart_automation / foodist_journal.py）が書き込んでいる
共有シートを *読むだけ* に使う。書き込みは一切しない。
テストとオフライン検証のために、同じインターフェースの
フィクスチャ実装（JSONファイル）も用意する。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

# 読み取り専用スコープ。既存シートを壊さないため書き込み権限は要求しない。
READONLY_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class SheetReader(ABC):
    @abstractmethod
    def tab_names(self) -> list[str]:
        """シート（タブ）名の一覧。"""

    @abstractmethod
    def values(self, tab: str) -> list[list]:
        """1タブ分の値を行列で返す。空セルは空文字で埋まる。"""


def sheet_range(tab: str, last_column: str = "E") -> str:
    """読む範囲。**最後の列を間違えると、その列だけ黙って空になる。**

    エラーにならないので気づきにくい。実際 A:E のままで POS売上タブの
    `客数`（F列）が取れず、売上だけが入った。
    純粋な関数にしてあるのは、実APIを叩かずにここを固定するため。
    """
    return f"'{tab}'!A:{last_column}"


class GoogleSheetReader(SheetReader):
    """共有シートを読む。**列の範囲は最後の列で決まる。**

    既定は A:E。FWタブとインフォマートの月次集計が E列までで収まるため。
    ⚠️ **これより右の列を使うタブは `last_column` を広げること。**
    広げ忘れると、その列だけ黙って空になる（エラーにならないので気づきにくい）。
    実際、POS売上タブの `客数`（F列）がこれで取れず、売上だけが入った。
    """

    def __init__(self, spreadsheet_id: str, service_account_json: str,
                 last_column: str = "E"):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        text = service_account_json.strip()
        if text.startswith("{"):
            creds = service_account.Credentials.from_service_account_info(
                json.loads(text), scopes=READONLY_SCOPES
            )
        else:
            creds = service_account.Credentials.from_service_account_file(
                text, scopes=READONLY_SCOPES
            )
        self._id = spreadsheet_id
        self._last_column = last_column
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def tab_names(self) -> list[str]:
        info = self._service.spreadsheets().get(spreadsheetId=self._id).execute()
        return [s["properties"]["title"] for s in info.get("sheets", [])]

    def values(self, tab: str) -> list[list]:
        result = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=self._id, range=sheet_range(tab, self._last_column))
            .execute()
        )
        return result.get("values", [])


class FixtureSheetReader(SheetReader):
    """``{タブ名: [[...行...]]}`` の JSON を読む。テスト・オフライン検証用。"""

    def __init__(self, data: dict[str, list[list]]):
        self._data = data

    @classmethod
    def from_file(cls, path: Path | str) -> "FixtureSheetReader":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def tab_names(self) -> list[str]:
        return list(self._data)

    def values(self, tab: str) -> list[list]:
        return self._data.get(tab, [])
