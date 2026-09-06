# 画面の公開（Cloudflare Workers）

画面は静的サイト。表示のたびにデータベースを叩かないので速く、アクセスが増えても
Neon の無料枠を消費しない。売上データが載るので、合言葉で入口を守る。すべて無料枠内。

Cloudflare の新しい画面は Workers に寄っていて Pages の入口が分かりにくいので、
**Cloudflare 側で git 連携はしない**。GitHub Actions から直接公開する。
天がやるのは「APIトークンを1つ登録」と「合言葉を決める」の2つだけ。
どちらも GitHub の Secrets に貼るだけで、Cloudflare の画面はさわらない。

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

## 3. 入口をつくる（合言葉ログイン）

このままだと URL を知っていれば誰でも見られる。全員共通の合言葉で入口を作る。
Cloudflare の画面をさわる必要はない。**GitHub の Secrets に2つ足すだけ。**

GitHub → リポジトリ → Settings → Secrets and variables → Actions → New secret

| 名前 | 中身 |
|---|---|
| `HANSOKU_APP_PASSWORD` | 店長に配る合言葉。覚えやすく、推測しにくいものを（例: 3語つなげる） |
| `HANSOKU_COOKIE_SECRET` | ログイン状態の署名鍵。人が覚える必要はない。長いランダム文字列 |

署名鍵はこれで作れる（結果をそのまま貼る）:

    openssl rand -base64 48

登録したら Actions →「画面をデプロイ」を実行。以後の使い方:

1. 公開URLを開く → 合言葉とお名前を入れる
2. その端末では **30日間** そのまま使える（毎回入力しなくてよい）
3. 右上の「ログアウト」で切れる

お名前は「誰が目標・メモを入れたか」の記録に使う。店名でもよい。

### 気をつけること

合言葉は**全員で1つ**なので、次の性質がある。手順ではなく、性質として理解しておく。

- 1人から漏れれば全員ぶん漏れる。SNSやグループ外に貼らない
- 辞めた人を締め出すには、`HANSOKU_APP_PASSWORD` を変えて再デプロイし、
  全員に新しい合言葉を配り直す（個別には切れない）
- 「誰が入れたか」はお名前の自己申告。なりすましは技術的には防げない
- 全員を一斉にログアウトさせたいときは `HANSOKU_COOKIE_SECRET` を変えて再デプロイ
  （合言葉はそのままでよい）

もっと厳密にしたくなったら、Cloudflare Access（メール認証）へ戻せる。Worker は
Access が前段にあればそちらを優先するので、**コードは変えずに切り替えられる**。

### いま Cloudflare Access が掛かっている場合

Access が前段で止めるので、合言葉の画面まで届かない。Zero Trust →
Access → Applications → `販促` を削除してから使う。
（消さずに残す場合は、Access のログイン方法に **One-time PIN** を追加すれば
そのまま使える。Settings → Authentication → Login methods → Add new）

## 4. 動作確認

1. Actions →「画面をデプロイ」を手動実行 → 成功を確認
2. 公開URLを開く → 合言葉とお名前を入れる → 画面が出る

店舗・指標を切り替えて、実績が表示されれば完了。
