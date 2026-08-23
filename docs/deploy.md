# 画面の公開（Cloudflare Pages + Access）

画面は静的サイトなので、表示のたびにデータベースを叩かない。CDN から配るだけなので速く、
アクセスが増えても Neon の無料枠を消費しない。売上データが載るので Access で認証をかける。

すべて無料枠内。

---

## 1. Cloudflare Pages プロジェクトを作る

1. https://dash.cloudflare.com → 左メニュー **Workers & Pages**
2. **Create** → **Pages** → **Connect to Git**
3. リポジトリ `amami-cell/hansoku` を選ぶ
4. ビルド設定:
   - Framework preset: **None**
   - Build command: （空欄）
   - Build output directory: **web**
5. **Save and Deploy**

数十秒で `https://hansoku.pages.dev` のようなURLが発行される。

> 中身のデータ（dashboard.json）は Git に含めていない。次の手順の GitHub Actions が
> 毎晩 Neon を集計して書き出し、Pages へ公開する。

---

## 2. GitHub Actions から公開できるようにする

Cloudflare の API トークンを GitHub に登録する。

1. https://dash.cloudflare.com/profile/api-tokens → **Create Token**
2. テンプレート **Edit Cloudflare Workers** を使う（Pages も含まれる）
   - もしくは権限: Account → **Cloudflare Pages** → Edit
3. 発行されたトークンを控える
4. アカウントIDは R2 のときと同じ値（`c1e40aaf3b66e879041312f8bc154f2b`）

GitHub Secrets（https://github.com/amami-cell/hansoku/settings/secrets/actions）に登録:

| Name | 値 |
|---|---|
| `CLOUDFLARE_API_TOKEN` | 発行したトークン |
| `CLOUDFLARE_ACCOUNT_ID` | `c1e40aaf3b66e879041312f8bc154f2b` |

登録すると、毎晩 11:00 JST に自動で最新データが公開される。
Actions の「画面をデプロイ」を手動実行すれば即時反映もできる。

---

## 3. Access で認証をかける（重要）

このままだと URL を知っていれば誰でも見られる。天と店長だけに絞る。

1. Cloudflare dash → **Zero Trust**（無料。初回はチーム名を決めるだけ）
2. **Access** → **Applications** → **Add an application** → **Self-hosted**
3. 設定:
   - Application name: `販促`
   - Application domain: `hansoku.pages.dev`
4. **Policy** を1つ作る:
   - Policy name: `許可メンバー`
   - Action: **Allow**
   - Include → **Emails** → 天と店長のメールアドレスを並べる
5. 保存

以後、`hansoku.pages.dev` を開くとメールアドレスの入力を求められ、
登録済みのアドレスに届くコードを入れた人だけが入れる。パスワード管理は不要。

無料枠は50人まで。天のグループ（天＋店長 約27人）は収まる。

---

## 4. 動作確認

1. Actions →「画面をデプロイ」を手動実行 → 成功を確認
2. `https://hansoku.pages.dev` を開く → Access のメール認証 → 画面が出る

店舗・指標を切り替えて、実績が表示されれば完了。
