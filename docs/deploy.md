# 画面の公開（Cloudflare Workers + Access）

画面は静的サイト。表示のたびにデータベースを叩かないので速く、アクセスが増えても
Neon の無料枠を消費しない。売上データが載るので Access で認証をかける。すべて無料枠内。

Cloudflare の新しい画面は Workers に寄っていて Pages の入口が分かりにくいので、
**Cloudflare 側で git 連携はしない**。GitHub Actions から直接公開する。
天がやるのは「APIトークンを1つ登録」と「Accessで認証をかける」の2つだけ。

---

## 1. Cloudflare API トークンを作る

1. https://dash.cloudflare.com/profile/api-tokens → **Create Token**
2. **Edit Cloudflare Workers** テンプレートを使う（Workers の編集権限が付く）
3. Account Resources は自分のアカウントを選ぶ
4. 発行されたトークンを控える（一度しか表示されない）

## 2. GitHub Secrets に登録

https://github.com/amami-cell/hansoku/settings/secrets/actions

| Name | 値 |
|---|---|
| `CLOUDFLARE_API_TOKEN` | 発行したトークン |
| `CLOUDFLARE_ACCOUNT_ID` | `c1e40aaf3b66e879041312f8bc154f2b`（R2と同じ） |

登録すると、GitHub の Actions →「画面をデプロイ」を実行したときに公開される。
初回実行後、`https://hansoku.<サブドメイン>.workers.dev` のURLが発行される
（URLは実行ログと、Cloudflare の Workers & Pages 一覧に出る）。
以後は毎晩 11:00 JST に最新データで自動更新される。

## 3. Access で認証をかける（重要）

このままだと URL を知っていれば誰でも見られる。天と店長だけに絞る。

1. Cloudflare dash → **Zero Trust**（無料。初回はチーム名を決めるだけ）
2. **Access** → **Applications** → **Add an application** → **Self-hosted**
3. 設定:
   - Application name: `販促`
   - Application domain: 手順1で発行された Workers のURL（`hansoku.<サブドメイン>.workers.dev`）
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
2. 公開URLを開く → Access のメール認証 → 画面が出る

店舗・指標を切り替えて、実績が表示されれば完了。
