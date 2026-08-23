# 画面（閲覧SPA）

`dashboard.json` を読むだけの静的サイト。表示のたびにデータベースを叩かないので速く、
アクセスが増えても Neon の無料枠を消費しない。

## 構成
- `index.html` … ガワ
- `styles.css` … 見た目
- `app.js` … JSONを読んで描画（フレームワーク不使用・ビルド不要）
- `data/dashboard.json` … 夜間バッチが `hansoku export-web` で書き出す（Gitには含めない）

## ローカルで見る
```
python -m hansoku.cli export-web --date-from 2025-01-01 --date-to 2026-12-31 --out web/data
cd web && python -m http.server 8000
# http://localhost:8000
```
