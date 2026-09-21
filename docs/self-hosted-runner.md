# セルフホストrunnerの立て方（FW取込を無料枠を消費せず回す）

GitHub Actions の**無料枠（プライベートリポは月2,000分）**は、FWスクレイピングのような
重い処理を毎月30店ぶん回すには足りません。**自前のランナーを1台立てる**と、GitHub の
計測分を **一切消費しません（＝実質無制限・無料）**。ここではその手順をまとめます。

> セキュリティ: FWのID/PW・Neon・R2 の**シークレットは今まで通り GitHub Secrets のまま**です。
> ランナーはジョブ実行時に GitHub からメモリ上に受け取るだけで、**平文でディスクに保存しません**
> （GitHub提供ランナーと同じ扱い）。**このリポジトリは必ずプライベートのまま**にしてください
> （公開リポで自前ランナーはフォークから任意コードを実行され得るため危険）。

## 用意するマシン

- **Ubuntu（Linux）**にしてください。ワークフローが `xvfb-run` と `apt` を使うため、Mac/Windows
  そのままでは動きません。以下のいずれかでOK：
  - 事務所に置ける **常時ONのPC（Ubuntuを入れる／既にLinux）**
  - Windows機なら **WSL2 の Ubuntu**（無料。PCが起きている間だけ動けばよい）
  - 月数百円の **Ubuntu VPS**（例: さくら/Conoha/Hetzner等。24時間動かせて確実）
- 必要スペックは軽い（メモリ2GB程度でも可）。FW・Neon・R2 に**ネット接続**できること。

## 一度だけの準備（マシン側で実行）

```bash
# Python 3.12 と基本ツール
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3-pip git xvfb curl

# このリポジトリの依存とChromium（システム依存も含めて一度だけ）
git clone https://github.com/amami-cell/hansoku.git
cd hansoku
pip install -r requirements.txt
python3 -m playwright install --with-deps chromium   # ← 依存(apt)込みで一度だけ
```

## ランナーを登録する（トークンは GitHub 画面から）

1. ブラウザで **`https://github.com/amami-cell/hansoku` → Settings → Actions → Runners**
   → **New self-hosted runner** → OS に **Linux** を選ぶ。
2. 画面に**そのマシン用のコマンド**（download / config / run）が表示されます。
   `./config.sh --url ... --token XXXX` の**トークンは1回限りの秘密**なので、
   **必ずこのGitHub画面からコピペ**してください（私からは発行できません）。
3. 表示どおりに実行：

```bash
# 例（実際のURL/トークンはGitHub画面のものを使う）
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64.tar.gz -L <画面のダウンロードURL>
tar xzf actions-runner-linux-x64.tar.gz
./config.sh --url https://github.com/amami-cell/hansoku --token <画面のトークン>
# 常駐サービスとして動かす（PC再起動後も自動で上がる）
sudo ./svc.sh install
sudo ./svc.sh start
```

4. GitHub の **Settings → Actions → Runners** に、そのランナーが **「Idle」** で出れば成功。

## 使い方（登録後）

- FWのワークフロー（`fw.yml`）を実行するとき、入力の **`runner` を `self-hosted`** にするだけ。
  → そのジョブは自前ランナーで動き、**GitHubの無料枠を消費しません**。
- `hosted` のままなら従来どおり GitHub 提供ランナー（無料枠を消費）で動きます。切り替え自由。
- ランナーのマシンは、ジョブを流したい時に**起きていれば**OK。VPSなら常時ON。

## これで何が変わるか

- ルクアの販売時期実績バックフィル（残り施策）を **1回の実行でまとめて**流し切れます
  （施策ごとに画面を開き直す方式に直したので途中で空にならない）。
- 30店の月次更新も、**過去の確定分はスキップ**して新しい月ぶんだけ取るので軽く、
  自前ランナーなら回数・時間を気にせず回せます。
