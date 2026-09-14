// ログイン／初回登録・パスワード再設定の画面。Worker が自分で返す（静的アセットに
// 置くと入口の外になる）。個人パスワード方式：ふだんは 名前＋自分のパスワード。
// 初回・パスワード忘れ時だけ 名前＋参加コード(8888) で入り、その場で新パスワードを決める。
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
          --line:#e3e3df; --accent:#2E4A7D; --warn:#9A3B54; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16171a; --card:#1e2024; --ink:#f2f2f0; --ink3:#9aa0aa; --line:#2c2f35; --accent:#7ea2dd; }
  }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100dvh; display:grid; place-items:center; padding:24px;
         background:var(--bg); color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif; }
  .card { width:100%; max-width:380px; background:var(--card); border:1px solid var(--line);
          border-radius:14px; padding:28px 24px; }
  h1 { margin:0 0 4px; font-size:20px; letter-spacing:.02em; }
  .sub { margin:0 0 20px; color:var(--ink3); font-size:13px; line-height:1.6; }
  label { display:block; font-size:13px; font-weight:600; margin:14px 0 6px; }
  input { width:100%; padding:12px 13px; font-size:16px; border-radius:9px;
          border:1px solid var(--line); background:var(--bg); color:var(--ink); }
  input:focus { outline:2px solid var(--accent); outline-offset:1px; }
  button { width:100%; margin-top:20px; padding:13px; font-size:15px; font-weight:600;
           color:#fff; background:var(--accent); border:0; border-radius:9px; cursor:pointer; }
  .err { margin:14px 0 0; padding:10px 12px; border-radius:8px; font-size:13px;
         color:var(--warn); background:color-mix(in srgb, var(--warn) 12%, transparent); }
  .fine { margin-top:18px; color:var(--ink3); font-size:12px; line-height:1.6; }
  .alt { margin-top:16px; text-align:center; font-size:13px; }
  .alt a { color:var(--accent); font-weight:600; text-decoration:none; }
</style>
</head><body>
${inner}
</body></html>`;
}

// ふだんのログイン（名前＋自分のパスワード）。
export function loginPage({ error = "", name = "", notice = "" } = {}) {
  return shell("販促｜ログイン", `
  <form class="card" method="POST" action="/login">
    <h1>販促マネジメント</h1>
    <p class="sub">お名前と、自分のパスワードでログイン。<br>一度入れると、この端末では30日間そのまま使えます。</p>
    ${notice ? `<p class="sub" style="color:var(--accent)">${esc(notice)}</p>` : ""}
    <label for="name">お名前</label>
    <input id="name" name="name" autocomplete="username" required maxlength="40"
           value="${esc(name)}" placeholder="例: 天見 真悟">
    <label for="password">パスワード</label>
    <input id="password" name="password" type="password" autocomplete="current-password"
           required maxlength="200" placeholder="自分のパスワード">
    ${error ? `<p class="err">${esc(error)}</p>` : ""}
    <button type="submit">ログイン</button>
    <p class="alt"><a href="/join">初めての方・パスワードを忘れた方 →</a></p>
    <p class="fine">お名前は「誰が・いつ・何を更新したか」の記録に使います。</p>
  </form>`);
}

// 初回登録・パスワード再設定（名前＋参加コード8888 → 新パスワードを決める）。
export function joinPage({ error = "", name = "" } = {}) {
  return shell("販促｜初回登録・パスワード再設定", `
  <form class="card" method="POST" action="/join">
    <h1>初回登録・再設定</h1>
    <p class="sub">お名前と<b>参加コード</b>を入れ、これから使う<b>自分のパスワード</b>を決めてください。
      参加コードが分からないときは本部（天見）へ。</p>
    <label for="name">お名前</label>
    <input id="name" name="name" autocomplete="username" required maxlength="40"
           value="${esc(name)}" placeholder="例: 天見 真悟">
    <label for="code">参加コード</label>
    <input id="code" name="code" type="password" autocomplete="off"
           required maxlength="200" placeholder="共通の参加コード">
    <label for="password">これから使うパスワード（4文字以上）</label>
    <input id="password" name="password" type="password" autocomplete="new-password"
           required minlength="4" maxlength="200" placeholder="自分だけのパスワード">
    <label for="password2">パスワード（確認）</label>
    <input id="password2" name="password2" type="password" autocomplete="new-password"
           required minlength="4" maxlength="200" placeholder="もう一度">
    ${error ? `<p class="err">${esc(error)}</p>` : ""}
    <button type="submit">登録して入る</button>
    <p class="alt"><a href="/login">← ログインに戻る</a></p>
    <p class="fine">すでに登録済みの名前は上書きできません（本部が「初期化」した場合のみ再設定できます）。</p>
  </form>`);
}

export const htmlResponse = (body, status = 200, headers = {}) =>
  new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", ...headers },
  });
