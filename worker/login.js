// ログイン／パスワード設定の画面。Worker が自分で返す（静的アセットに置くと入口の外になる）。
// 使い方はシンプルに1本化した：
//   ふだん … ID（お名前）＋ 自分のパスワードでログイン。端末に保存できる。
//   初回・忘れた時 … パスワード欄に参加コード(8888)を入れてログイン → 設定画面で自分の
//                    パスワードを決める → ログイン画面に戻って 名前＋新パスワードで入る。
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

function shell(title, inner) {
  return `<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>${esc(title)}</title>
<style>
  :root { color-scheme: light dark; --bg:#f7f7f5; --card:#fff; --ink:#1b1b1a; --ink3:#6b7280;
          --line:#e3e3df; --accent:#2E4A7D; --warn:#9A3B54; --ok:#2f6b4f; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16171a; --card:#1e2024; --ink:#f2f2f0; --ink3:#9aa0aa; --line:#2c2f35; --accent:#7ea2dd; --ok:#7fc6a3; }
  }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100dvh; display:grid; place-items:center; padding:24px;
         background:var(--bg); color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif; }
  .card { width:100%; max-width:390px; background:var(--card); border:1px solid var(--line);
          border-radius:14px; padding:28px 24px; }
  h1 { margin:0 0 4px; font-size:20px; letter-spacing:.02em; }
  .sub { margin:0 0 18px; color:var(--ink3); font-size:13px; line-height:1.6; }
  label { display:block; font-size:13px; font-weight:600; margin:14px 0 6px; }
  input { width:100%; padding:12px 13px; font-size:16px; border-radius:9px;
          border:1px solid var(--line); background:var(--bg); color:var(--ink); }
  input:focus { outline:2px solid var(--accent); outline-offset:1px; }
  input[readonly] { color:var(--ink3); }
  button { width:100%; margin-top:20px; padding:13px; font-size:15px; font-weight:600;
           color:#fff; background:var(--accent); border:0; border-radius:9px; cursor:pointer; }
  .err { margin:14px 0 0; padding:10px 12px; border-radius:8px; font-size:13px;
         color:var(--warn); background:color-mix(in srgb, var(--warn) 12%, transparent); }
  .ok { margin:14px 0 0; padding:10px 12px; border-radius:8px; font-size:13px;
        color:var(--ok); background:color-mix(in srgb, var(--ok) 14%, transparent); }
  .hint { margin-top:16px; padding:11px 12px; border-radius:9px; font-size:12.5px; line-height:1.7;
          color:var(--ink3); background:color-mix(in srgb, var(--accent) 8%, transparent);
          border:1px solid color-mix(in srgb, var(--accent) 22%, transparent); }
  .hint b { color:var(--ink); }
  .fine { margin-top:16px; color:var(--ink3); font-size:12px; line-height:1.6; }
</style>
</head><body>
${inner}
</body></html>`;
}

// ふだんのログイン（ID＝お名前 ＋ 自分のパスワード）。初回は参加コード(8888)もここに入れる。
export function loginPage({ error = "", name = "", notice = "" } = {}) {
  return shell("販促｜ログイン", `
  <form class="card" method="POST" action="/login" autocomplete="on">
    <h1>販促マネジメント</h1>
    <p class="sub">ID（お名前）と、自分のパスワードでログイン。<br>一度入れると、この端末では入れっぱなしになります。</p>
    ${notice ? `<p class="ok">${esc(notice)}</p>` : ""}
    <label for="name">ID（お名前）</label>
    <input id="name" name="name" autocomplete="username" required maxlength="40"
           value="${esc(name)}" placeholder="例: 天見 真悟">
    <label for="password">パスワード</label>
    <input id="password" name="password" type="password" autocomplete="current-password"
           required maxlength="200" placeholder="自分のパスワード">
    ${error ? `<p class="err">${esc(error)}</p>` : ""}
    <button type="submit">ログイン</button>
    <div class="hint"><b>初めての方・パスワードを忘れた方</b><br>
      パスワード欄に <b>参加コード（8888）</b> を入れてログインしてください。
      次の画面で、これから使う自分のパスワードを決められます。</div>
    <p class="fine">IDは「誰が・いつ・何を更新したか」の記録に使います。</p>
  </form>`);
}

// パスワード設定（初回・再設定）。今のパスワード（初回は8888）＋新しいパスワード。
export function setpwPage({ error = "", name = "" } = {}) {
  return shell("販促｜パスワード設定", `
  <form class="card" method="POST" action="/setpw" autocomplete="on">
    <h1>パスワードの設定</h1>
    <p class="sub"><b>${esc(name)}</b> さん、これから使う自分のパスワードを決めてください。
      設定できたら、ログイン画面で ID（お名前）＋新しいパスワードで入れます。</p>
    <input type="text" name="who" autocomplete="username" value="${esc(name)}" readonly
           aria-label="ID（お名前）">
    <label for="oldpw">今のパスワード（初回は参加コード 8888）</label>
    <input id="oldpw" name="oldpw" type="password" autocomplete="current-password"
           required maxlength="200" placeholder="初回は 8888">
    <label for="newpw">新しいパスワード（4文字以上）</label>
    <input id="newpw" name="newpw" type="password" autocomplete="new-password"
           required minlength="4" maxlength="200" placeholder="これから使うパスワード">
    <label for="newpw2">新しいパスワード（確認）</label>
    <input id="newpw2" name="newpw2" type="password" autocomplete="new-password"
           required minlength="4" maxlength="200" placeholder="もう一度">
    ${error ? `<p class="err">${esc(error)}</p>` : ""}
    <button type="submit">設定して完了</button>
    <p class="fine">端末のパスワード保存を使うと、次回から自動入力できます。</p>
  </form>`);
}

// 旧・初回登録画面（現在はログイン→設定に一本化。念のため後方互換で残す）。
export function joinPage(opts = {}) { return setpwPage(opts); }

export const htmlResponse = (body, status = 200, headers = {}) =>
  new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", ...headers },
  });
