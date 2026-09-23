// ログイン画面。Worker が自分で返す（静的アセットに置くと入口の外になる）。
// 入口は「共通の合言葉 ＋ お名前」だけ。個人パスワードも初期設定も無い。
//   お名前 … 「誰が・いつ・何を更新したか」の記録用。
//   合言葉 … 全員共通。本部が配布し、漏れたら env の値を差し替える。
//   一度入れると、その端末では365日入れっぱなし。
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

// ログイン（お名前 ＋ 共通の合言葉）。個人パスワード・初期設定は無い。
export function loginPage({ error = "", name = "", notice = "" } = {}) {
  return shell("販促｜ログイン", `
  <form class="card" method="POST" action="/login" autocomplete="on">
    <h1>販促マネジメント</h1>
    <p class="sub">お名前と、みんな共通の合言葉でログイン。<br>一度入れると、この端末では入れっぱなしになります。</p>
    ${notice ? `<p class="ok">${esc(notice)}</p>` : ""}
    <label for="name">お名前</label>
    <input id="name" name="name" autocomplete="username" required maxlength="40"
           value="${esc(name)}" placeholder="例: 天見 真悟">
    <label for="password">合言葉</label>
    <input id="password" name="password" type="password" autocomplete="current-password"
           required maxlength="200" placeholder="みんな共通の合言葉">
    ${error ? `<p class="err">${esc(error)}</p>` : ""}
    <button type="submit">ログイン</button>
    <p class="fine">お名前は「誰が・いつ・何を更新したか」の記録に使います。合言葉が分からないときは本部へ。</p>
  </form>`);
}

export const htmlResponse = (body, status = 200, headers = {}) =>
  new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", ...headers },
  });
