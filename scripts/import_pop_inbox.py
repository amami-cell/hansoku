#!/usr/bin/env python3
"""config/creatives_inbox.tsv の Drive公開POPを R2 とNeon(promo_creatives)へ直接取り込む。

Worker(/api/creatives)は Cloudflare Access の内側にいるため、サーバー越しの
curl は SSO に 302 される。R2 の S3 API と Neon は Access を通らないので、
ランナーから直接書き込む。書き込むキー・テーブルはアプリ内アップロードと
同じ形なので、アプリの一覧・プレビューにそのまま出る。

再実行しても増えないよう、(campaign_id, title) が既にある行はスキップする。
必要な環境変数（cloud設定）: HANSOKU_ENV=cloud, NEON_DATABASE_URL,
R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY。
"""
from __future__ import annotations

import csv
import sys
import time
import uuid
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hansoku.db import get_appdb, get_object_store  # noqa: E402
from hansoku.settings import load_settings  # noqa: E402

INBOX = Path("config/creatives_inbox.tsv")
STORE_CODE = "1160"  # ルクアLargo
UA = "Mozilla/5.0 (X11; Linux x86_64) hansoku-pop-import"


def render_thumb(pdf_bytes: bytes) -> bytes | None:
    """PDFの1ページ目をPNG画像に。iframeのPDFはスマホで真っ白になるため、小窓は画像で出す。"""
    try:
        import fitz  # PyMuPDF
    except Exception as e:  # noqa: BLE001
        print(f"WARN: PyMuPDF なし（{e}）→ サムネ無しで続行")
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count == 0:
            return None
        page = doc.load_page(0)
        # 2倍スケール。POP1枚を十分読める解像度で、かつ数百KBに収める。
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        return pix.tobytes("png")
    except Exception as e:  # noqa: BLE001
        print(f"WARN: サムネ生成失敗（{e}）")
        return None


def drive_download(drive_id: str) -> bytes:
    """公開Driveファイルを直ダウンロード（スキャン確認ページは confirm=t で回避）。"""
    url = (
        "https://drive.usercontent.google.com/download"
        f"?id={drive_id}&export=download&confirm=t"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> int:
    settings = load_settings()
    store = get_object_store(settings)
    rows = list(csv.DictReader(INBOX.open(encoding="utf-8"), delimiter="\t"))
    print(f"== 台帳 {INBOX}: {len(rows)} 行 / 取り込み先バケット {settings.objects.bucket} ==")

    put = skip = fail = 0
    with get_appdb(settings) as db:
        existing = {
            (r["campaign_id"], r["title"]): r
            for r in db.query("SELECT id, campaign_id, title, r2_key, thumb_key FROM promo_creatives")
        }
        print(f"既存の制作物: {len(existing)} 件")

        thumbed = 0
        for r in rows:
            cid = (r.get("campaign_id") or "").strip()
            title = (r.get("制作物") or "").strip()
            drive_id = (r.get("driveId") or "").strip()
            filename = (r.get("filename") or "file.pdf").strip()
            start = (r.get("期間") or "").split("〜")[0].strip()
            if not drive_id or not cid:
                print(f"SKIP(不足): {title}")
                continue
            row = existing.get((cid, title))
            # 既存でサムネもある → 何もしない
            if row and row.get("thumb_key"):
                print(f"SKIP(既存): [{cid}] {title}")
                skip += 1
                continue
            try:
                data = drive_download(drive_id)
            except Exception as e:  # noqa: BLE001
                print(f"FAIL(DL): [{cid}] {title} -> {e}")
                fail += 1
                continue
            if data[:4] != b"%PDF":
                head = data[:16]
                print(f"FAIL(非PDF {head!r}): [{cid}] {title} — Drive公開設定を確認")
                fail += 1
                continue
            thumb_png = render_thumb(data)

            if row:
                # 既存だがサムネ未生成 → サムネだけ作って貼る（冪等・バックフィル）
                base_key = str(row["r2_key"])
                tkey = base_key + ".thumb.png"
                if thumb_png:
                    store.put(tkey, thumb_png, "image/png")
                    db.execute(
                        "UPDATE promo_creatives SET thumb_key = %s WHERE id = %s",
                        (tkey, row["id"]),
                    )
                    print(f"THUMB: [{cid}] {title}（{len(thumb_png):,} bytes）")
                    thumbed += 1
                else:
                    print(f"WARN(サムネ生成不可): [{cid}] {title}")
                continue

            base = Path(filename).stem[:40] or "file"
            key = f"creatives/uploads/{cid}/{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}_{base}.pdf"
            store.put(key, data, "application/pdf")
            tkey = ""
            if thumb_png:
                tkey = key + ".thumb.png"
                store.put(tkey, thumb_png, "image/png")
            db.execute(
                """
                INSERT INTO promo_creatives
                    (id, campaign_id, store_code, title, kind, r2_key, mime, doc_date, set_by, set_at, thumb_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
                """,
                (
                    uuid.uuid4().hex,
                    cid,
                    STORE_CODE,
                    title,
                    "dev",
                    key,
                    "application/pdf",
                    start,
                    "import",
                    tkey,
                ),
            )
            existing[(cid, title)] = {"id": None, "r2_key": key, "thumb_key": tkey}
            print(f"PUT : [{cid}] {title} ({len(data):,} bytes)" + ("＋サムネ" if tkey else ""))
            put += 1

        # 最終状態
        after = db.query(
            "SELECT campaign_id, title, mime, thumb_key FROM promo_creatives ORDER BY campaign_id, title"
        )
    print(f"\n== 完了: 追加 {put} / サムネ追加 {thumbed} / 既存 {skip} / 失敗 {fail} ==")
    print(f"promo_creatives 合計 {len(after)} 件:")
    for a in after:
        print(f"  [{a['campaign_id']}] {a['title']} ({a['mime']})")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
