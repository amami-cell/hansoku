# 本番URLの「ログイン必須（Cloudflare Access）」を外す手順

本番 `https://hansoku.amami-c1e.workers.dev/` は、Cloudflare の **Access（Zero Trust
の SSO）** がワーカーの前段に立っており、開くたびに `shy-wood-500f.cloudflareaccess.com`
のログインを求められます。

このため、ログイン直後は画面が開いても、**画面内でデータ（`data/dashboard.json`）を
取りに行くところで Access に弾かれ**、ブラウザが「Failed to fetch」で止まることがあります
（＝データではなく認証層の問題）。

> ワーカー本体はすでに「開放モード（`OPEN_ACCESS=1`）＝ログイン不要・閲覧専用」で
> 動いています。**手前の Access を外せば、そのまま合言葉なしで普通に使えます。**
> Access はデプロイ用トークンでは操作できないため、**オーナー権限で管理画面から**外します。

---

## いちばん手軽な代替（作業不要）
外す作業をしない場合は、**ログイン不要の公開URL**をそのまま使えます。最新データ・
最新の画面が入っています。

**https://hansoku-preview.amami-c1e.workers.dev/**

本番URLにこだわらないなら、こちらをブックマークするだけで完結します。

---

## 手順A：Access アプリを削除して「誰でも閲覧」にする（推奨・いちばん簡単）

1. Cloudflare にログイン → 右上でアカウント **「Amami@8sin.co.jp's Account」** を選択。
2. 左メニュー **Zero Trust** を開く（初回は組織名 `shy-wood-500f` の確認が出ることがあります）。
3. **Access → Applications** を開く。
4. 一覧から **`hansoku.amami-c1e.workers.dev`**（Self-hosted アプリ）を見つける。
5. 行の右の **「…」→ Delete**（または詳細を開いて **Delete application**）。
6. 確認ダイアログで削除を実行。

これで Access が外れ、本番URLは合言葉なしで開けるようになります（ワーカーが
`OPEN_ACCESS=1` のため）。反映は通常すぐ〜数分です。ブラウザは**キャッシュとCookieを
消して**から開き直すと確実です（Access の Cookie が残っていることがあるため）。

---

## 手順B：削除はせず「社内の人だけ・毎回SSOはしない」にしたい場合

「全公開はしたくない、でも毎回ログインは煩わしい」場合は、Access を残したまま**セッションを
長く**します。

1. **Zero Trust → Access → Applications → `hansoku...` → Edit**。
2. **Session Duration** を長め（例：`1 week` / `1 month`）に設定して保存。

これで一度ログインすれば、その期間は再ログイン不要になり「Failed to fetch」も起きにくく
なります（ただし期限が切れれば再発するので、根治は手順A）。

---

## 手順C：どうしても Access を残しつつ確実に直したい（上級・任意）

Access を残す前提で「画面内の fetch も確実に通す」には、ワーカー側で
`data/dashboard.json` だけを Access の外に出す（別ワーカー/別ルートで無認証配信する）
などの改修が要ります。手間の割にメリットが薄いので、通常は**手順A（削除）か公開URL**で
十分です。ご希望あればこの改修も承ります。

---

## 外したかどうかの確認

ランナー（GitHub Actions）から確認できます。リポジトリの **Actions → `probe-app`** を
`url = https://hansoku.amami-c1e.workers.dev/` で実行し、結果が

- `HTTP=302 redirect=https://shy-wood-500f.cloudflareaccess.com/...` → **まだ Access が有効**
- `HTTP=200`（本文が `<!doctype html>` の画面HTML）→ **Access が外れた**

で判断できます。

---

## メモ
- ワーカーの公開/非公開は `wrangler.toml` の `[vars] OPEN_ACCESS` で制御しています
  （`"1"`＝ログイン不要の閲覧専用 / 消す・`"0"`＝合言葉ログインに戻す）。**Access はこれとは
  別レイヤー**なので、合言葉に戻したい場合は Access 削除ではなく `OPEN_ACCESS` を外します。
- 全公開でも**書き込み（POP追加・目標・メモ・削除）は不可（閲覧専用）**。書き込みは合言葉/
  Access の内側だけに限る実装です。
