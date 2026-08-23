"""
正規化ユーティリティ。

取り込み元（FW共有シート・Excel・インフォマート）ごとに店名と日付の書き方が違う。
フォーマット不一致は静かにデータを取りこぼす事故の温床なので、
``f_actuals`` へ入る前に必ずここを通す。
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, datetime

# 取り込み元の店名に付く運営会社サフィックス（正規化時に落とす）
_SUFFIXES = ("(HASSIN)", "(ハッシン)")

# 突き合わせ時に無視する記号類
_NOISE = re.compile(r"[\s　・･,，\.。\-‐-―ー_/／\\（）()「」\[\]【】&＆'\"’”]+")

_YM = re.compile(r"^(\d{4})[-/年]?(\d{1,2})月?$")
_YMD = re.compile(r"^(\d{4})[-/年]?(\d{1,2})[-/月]?(\d{1,2})日?$")


def normalize_text(value: str) -> str:
    """NFKC で全角→半角に寄せ、前後空白を落とす。表示用の穏やかな正規化。"""
    return unicodedata.normalize("NFKC", str(value)).strip()


def store_key(value: str) -> str:
    """
    店名の突き合わせキー。

    全角/半角・空白・記号・運営会社サフィックスの揺れを吸収する。
    「すさび湯　歌舞伎町（ＨＡＳＳＩＮ）」も「すさび湯 歌舞伎町」も同じキーになる。
    """
    s = normalize_text(value)
    for suffix in _SUFFIXES:
        s = s.replace(suffix, "")
    s = _NOISE.sub("", s)
    return s.casefold()


def store_code(value: str | int) -> str:
    """
    店舗コードの正規化。

    取り込み元によって 922 / "922" / "0922" / "922.0" と揺れるため、
    数値として解釈できるものは先頭ゼロを落とした10進表記に揃える。
    """
    s = normalize_text(value)
    if not s:
        raise ValueError("store_code が空です")
    if re.fullmatch(r"\d+(\.0+)?", s):
        return str(int(float(s)))
    return s


# FW共有シートの店名に付く先頭のゼロ埋め店舗コード（例 "0001015_すさび湯 歌舞伎町"）
_CODE_PREFIX = re.compile(r"^0*(\d+)[_\s]+(.*)$")


def split_source_store_name(value: str) -> tuple[str | None, str]:
    """
    取り込み元の店名を (店舗コード, 店名) に分ける。

    FW共有シートは店名の先頭に店舗コードを埋め込んでいる。
    コードで突き合わせられるなら、そちらの方が店名の表記揺れより遥かに確実なので
    優先して使う。コードが無い形式なら (None, 元の文字列) を返す。
    """
    s = normalize_text(value)
    match = _CODE_PREFIX.match(s)
    if not match:
        return None, s
    return match.group(1), match.group(2).strip()


def parse_year_month(value: str) -> date:
    """
    「2026-08」「2026/8」「2026年8月」を、その月の1日の date にして返す。

    FW共有シートのA列（年月）はこの形式で入っている。月次実績は
    「その月の初日」を代表日として ``f_actuals.date`` に載せる。
    """
    s = normalize_text(value)
    m = _YM.match(s)
    if not m:
        raise ValueError(f"年月として解釈できません: {value!r}")
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"月が範囲外です: {value!r}")
    return date(year, month, 1)


def parse_date(value) -> date:
    """日付らしきものを date に揃える（datetime / date / 文字列を受ける）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = normalize_text(value)
    m = _YMD.match(s)
    if not m:
        raise ValueError(f"日付として解釈できません: {value!r}")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def month_end(day: date) -> date:
    """その月の末日を返す。月次実績の期間終了日に使う。"""
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def parse_amount(value) -> float:
    """
    金額セルを float にする。

    「1,234」「1，234」「30%」「¥1,234」「(1,234)」などの表記揺れを吸収する。
    空・ハイフン・解釈不能は 0.0（取り込み元が「実績なし」を空欄で表すため）。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = normalize_text(value)
    if not s or s in {"-", "--", "—", "―"}:
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = s.replace(",", "").replace("，", "")
    s = s.replace("¥", "").replace("￥", "").replace("円", "")
    s = s.replace("%", "").replace("％", "").strip()
    if not s:
        return 0.0
    try:
        amount = float(s)
    except ValueError:
        return 0.0
    return -amount if negative else amount
