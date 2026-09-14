// Worker の入口（合言葉ログイン）を、実際に fetch を呼んで確かめる。
//
// 売上の実データが載る画面の入口なので、「開けっ放しになっていないこと」を
// 推測ではなくテストで押さえる。@neondatabase/serverless は入れずに動かしたいので、
// import を差し替えて読み込む。
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");

// neon() を使わない偽物に差し替えた index.js を一時ファイルとして読み込む
const src = fs.readFileSync(path.join(ROOT, "worker/index.js"), "utf8")
  .replace(
    'import { neon } from "@neondatabase/serverless";',
    "const neon = () => { throw new Error('DBは使わない'); };",
  );
// 相対importが解決できるよう、一時ファイルは worker/ の中に置く
const tmp = path.join(ROOT, "worker", ".under_test.mjs");
fs.writeFileSync(tmp, src);
const worker = (await import(tmp)).default;
fs.unlinkSync(tmp);

const ENV = {
  APP_PASSWORD: "ただしい合言葉",
  COOKIE_SECRET: "s".repeat(48),
  ASSETS: { fetch: () => new Response("<html>本編</html>", { headers: { "content-type": "text/html" } }) },
};

const req = (url, opts = {}) => new Request("https://x.example" + url, opts);
const call = (url, opts, env = ENV) => worker.fetch(req(url, opts), env);
const form = (o) => {
  const f = new FormData();
  for (const [k, v] of Object.entries(o)) f.append(k, v);
  return f;
};
const cookieOf = (res) => {
  const raw = res.headers.get("set-cookie") || "";
  return raw.split(";")[0];
};

let failed = 0;
const test = async (name, fn) => {
  try { await fn(); console.log("  ok   " + name); }
  catch (e) { failed += 1; console.log("  FAIL " + name + "\n       " + e.message); }
};

console.log("入口（合言葉が設定されているとき）");

await test("合言葉なしで本編を開くとログインへ送られる", async () => {
  const res = await call("/");
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/login");
});

await test("合言葉なしでAPIを叩くと401（HTMLを返さない）", async () => {
  const res = await call("/api/targets");
  assert.equal(res.status, 401);
  assert.equal((await res.json()).error, "unauthenticated");
});

await test("制作物PDFも入口の内側", async () => {
  assert.equal((await call("/creatives/a.pdf")).status, 303);
});

await test("ログイン画面は誰でも見られる", async () => {
  const res = await call("/login");
  assert.equal(res.status, 200);
  assert.match(await res.text(), /パスワード/);
});

await test("参加コードが違えば401で、入れない", async () => {
  const res = await call("/login", { method: "POST", body: form({ name: "天", password: "ちがう" }) });
  assert.equal(res.status, 401);
  assert.equal(res.headers.get("set-cookie"), null);
});

await test("名前が空なら通さない（誰が入れたか残らなくなるため）", async () => {
  const res = await call("/login", { method: "POST", body: form({ name: "  ", password: ENV.APP_PASSWORD }) });
  assert.equal(res.status, 400);
  assert.equal(res.headers.get("set-cookie"), null);
});

// 初回・未設定：参加コード(8888)を入れると「本入場」ではなく、パスワード設定へ誘導する。
await test("初回は参加コードでパスワード設定へ誘導される（本セッションは出ない）", async () => {
  const res = await call("/login", { method: "POST", body: form({ name: "天", password: ENV.APP_PASSWORD }) });
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/setpw");
  const sc = res.headers.get("set-cookie");
  assert.match(sc, /hansoku_setpw=/);      // 設定用の短命クッキー（本セッションではない）
  assert.doesNotMatch(sc, /hansoku_session=/);
  assert.match(sc, /HttpOnly/);
  assert.match(sc, /Secure/);
  assert.match(sc, /SameSite=Lax/);
});

// 設定前の一時クッキーで本編に入れてしまうと「8888だけで書き込める」ことになる。防ぐ。
await test("設定用の一時クッキーでは本編に入れない", async () => {
  const login = await call("/login", { method: "POST", body: form({ name: "天", password: ENV.APP_PASSWORD }) });
  const res = await call("/", { headers: { cookie: cookieOf(login) } });
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/login");
});

// 一時クッキーを本セッション名に付け替えても、setpw マーカーは入場・書き込みを許さない。
await test("設定用クッキーを session 名に移しても書き込めない", async () => {
  const login = await call("/login", { method: "POST", body: form({ name: "天", password: ENV.APP_PASSWORD }) });
  const tok = cookieOf(login).split("=").slice(1).join("=");
  const forged = "hansoku_session=" + tok;
  assert.equal((await call("/", { headers: { cookie: forged } })).status, 303);
  const post = await call("/api/targets", {
    method: "POST", headers: { cookie: forged, "content-type": "application/json" },
    body: JSON.stringify({ id: "x@2026", target: 1 }),
  });
  assert.equal(post.status, 401);
});

// 一時クッキーが無ければ設定画面は使えない（ログインからやり直し）。
await test("/setpw は一時クッキー無しだとログインへ戻す", async () => {
  const res = await call("/setpw");
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/login");
});

// 旧URL /join は廃止しログインへ寄せる。
await test("旧 /join はログインへ転送", async () => {
  const res = await call("/join");
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/login");
});

await test("クッキーを1文字でも書き換えると通らない（署名）", async () => {
  const login = await call("/login", { method: "POST", body: form({ name: "天", password: ENV.APP_PASSWORD }) });
  const bad = cookieOf(login).slice(0, -1) + (cookieOf(login).endsWith("a") ? "b" : "a");
  assert.equal((await call("/", { headers: { cookie: bad } })).status, 303);
});

await test("別の合言葉サイトのクッキーは通らない（署名鍵が違う）", async () => {
  const login = await call("/login", { method: "POST", body: form({ name: "天", password: ENV.APP_PASSWORD }) },
    { ...ENV, COOKIE_SECRET: "t".repeat(48) });
  assert.equal((await call("/", { headers: { cookie: cookieOf(login) } })).status, 303);
});

await test("ログアウトでクッキーが消える", async () => {
  const res = await call("/logout");
  assert.equal(res.status, 303);
  assert.match(res.headers.get("set-cookie"), /Max-Age=0/);
});

console.log("入口（合言葉が未設定のとき）");

const NOENV = { ASSETS: ENV.ASSETS };

await test("合言葉が無ければ開けっ放しにせず、全部止める", async () => {
  const res = await call("/", {}, NOENV);
  assert.equal(res.status, 503);
  assert.match(await res.text(), /設定されていません/);
});

await test("合言葉が無ければAPIも止める", async () => {
  assert.equal((await call("/api/targets", {}, NOENV)).status, 401);
});

console.log("Cloudflare Access のヘッダを騙られても通さない");

// このヘッダは Access が前段に無ければクライアントが自由に付けられる。
// 以前ここを信用しており、curl -H 'Cf-Access-Authenticated-User-Email: x@y' の
// 1行で全店の売上が見える状態だった。以後この形で固定する。
await test("ヘッダを偽装しても本編は見られない", async () => {
  const res = await call("/", { headers: { "Cf-Access-Authenticated-User-Email": "attacker@evil.example" } });
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/login");
});

await test("ヘッダを偽装してもAPIは通らない", async () => {
  const res = await call("/api/targets", { headers: { "Cf-Access-Authenticated-User-Email": "attacker@evil.example" } });
  assert.equal(res.status, 401);
});

await test("ヘッダを偽装しても書き込めない", async () => {
  const res = await call("/api/targets", {
    method: "POST",
    headers: { "Cf-Access-Authenticated-User-Email": "attacker@evil.example", "content-type": "application/json" },
    body: JSON.stringify({ id: "他店の施策@2026", target: 999999999 }),
  });
  assert.equal(res.status, 401);
});

await test("合言葉が未設定なら、ヘッダがあっても全部止まる", async () => {
  const res = await call("/", { headers: { "Cf-Access-Authenticated-User-Email": "amami@8sin.co.jp" } }, NOENV);
  assert.equal(res.status, 503);
});

console.log("開放モード（OPEN_ACCESS=1）");
const OPEN_ENV = { ...ENV, OPEN_ACCESS: "1", ASSETS: ENV.ASSETS };

await test("開放モードなら合言葉なしで本編が見られる", async () => {
  const res = await call("/", {}, OPEN_ENV);
  assert.equal(res.status, 200);
  assert.match(await res.text(), /本編/);
});
await test("開放モードを外すと（未設定）またログインへ", async () => {
  const res = await call("/", {}, ENV);
  assert.equal(res.status, 303);
});

await test("開放モードは閲覧専用：POSTで書き込めない（403）", async () => {
  const res = await call("/api/targets", { method: "POST", body: JSON.stringify({ id: "x", target: 100 }) }, OPEN_ENV);
  assert.equal(res.status, 403);
});
await test("開放モードは閲覧専用：制作物の削除も止める（403）", async () => {
  const res = await call("/api/creatives", { method: "DELETE", body: JSON.stringify({ id: "x" }) }, OPEN_ENV);
  assert.equal(res.status, 403);
});
await test("開放モードでもGET（閲覧）は通る", async () => {
  const res = await call("/api/creatives", {}, OPEN_ENV);
  // DBが無いテスト環境では 503（no-db）になるが、403（read-only）にはならない＝GETは弾かれていない
  assert.notEqual(res.status, 403);
});

await test("開放モードは閲覧専用：手動ステータスのPOSTも403", async () => {
  const res = await call("/api/status", { method: "POST", body: JSON.stringify({ id: "x", status: "保留" }) }, OPEN_ENV);
  assert.equal(res.status, 403);
});
await test("ステータスAPIは未ログインだとPOST不可（401）", async () => {
  const res = await call("/api/status", { method: "POST", headers: { "Cf-Access-Authenticated-User-Email": "a@b" }, body: JSON.stringify({ id: "x", status: "保留" }) });
  assert.equal(res.status, 401);
});

console.log(failed ? `\n${failed} 件失敗` : "\nすべて通過");
process.exit(failed ? 1 : 0);
