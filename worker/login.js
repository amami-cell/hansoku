// 合言葉の入力画面。Worker が自分で返す（静的アセットに置くと入口の外になる）。
export function loginPage({ error = "", name = "" } = {}) {
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  return `<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>販促｜ログイン</title>
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
</style>
</head><body>
  <form class="card" method="POST" action="/login">
    <h1>販促</h1>
    <p class="sub">合言葉とお名前を入れてください。<br>一度入れると、この端末では30日間そのまま使えます。</p>
    <label for="name">お名前（店名でも可）</label>
    <input id="name" name="name" autocomplete="name" required maxlength="40"
           value="${esc(name)}" placeholder="例: 梅田 田中">
    <label for="password">合言葉</label>
    <input id="password" name="password" type="password" autocomplete="current-password"
           required maxlength="200" placeholder="共有の合言葉">
    ${error ? `<p class="err">${esc(error)}</p>` : ""}
    <button type="submit">入る</button>
    <p class="fine">お名前は「誰が目標やメモを入れたか」の記録に使います。<br>
      合言葉が分からないときは本部へ。</p>
  </form>
</body></html>`;
}

export const htmlResponse = (body, status = 200, headers = {}) =>
  new Response(body, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", ...headers },
  });
