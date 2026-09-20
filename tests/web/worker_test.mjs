// Worker の入口（名前＋自分のパスワードでログイン）を、実際に fetch を呼んで確かめる。
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

// app_users をメモリに持つ偽DBに差し替えた index.js を一時ファイルとして読み込む。
//
// 以前は neon() を「呼ばれたら例外」にしていた。するとログインは DATABASE_URL が
// 無い経路（ユーザーが引けない＝401）しか通らず、**クッキーが出る本来の経路が
// 一度も検査されていなかった**。参加コードで登録 → 自分のパスワードでログイン、
// という今の導線をそのまま通せるように、最低限のSQLだけ解釈する偽物にする。
//
// 解釈するのは index.js が実際に投げる4種類だけ（SELECT / INSERT…ON CONFLICT /
// UPDATE / CREATE TABLE）。SQLを真面目に実装したいのではなく、
// 「誰が登録され、誰がログインできるか」を本物のコードに決めさせたいだけ。
const USERS = new Map();   // name -> {name, pass_hash, pass_salt, role, disabled, must_reset}
const fakeSql = (strings, ...vals) => {
  const q = strings.join("?").replace(/\s+/g, " ").trim();
  if (/^SELECT .* FROM app_users WHERE name/.test(q)) {
    const u = USERS.get(vals[0]);
    return Promise.resolve(u ? [{ ...u }] : []);
  }
  if (/^INSERT INTO app_users/.test(q)) {
    const [name, pass_hash, pass_salt, role] = vals;
    USERS.set(name, { name, pass_hash, pass_salt, role, disabled: false, must_reset: false });
    return Promise.resolve([]);
  }
  if (/^UPDATE app_users SET role=/.test(q)) {
    const u = USERS.get(vals[0]);
    if (u) u.role = "owner";
    return Promise.resolve([]);
  }
  return Promise.resolve([]);   // CREATE TABLE / last_login / audit_log は素通し
};
const src = fs.readFileSync(path.join(ROOT, "worker/index.js"), "utf8")
  .replace(
    'import { neon } from "@neondatabase/serverless";',
    "const neon = globalThis.__fakeNeon;",
  );
globalThis.__fakeNeon = () => fakeSql;
// 相対importが解決できるよう、一時ファイルは worker/ の中に置く
const tmp = path.join(ROOT, "worker", ".under_test.mjs");
fs.writeFileSync(tmp, src);
const worker = (await import(tmp)).default;
fs.unlinkSync(tmp);

const ENV = {
  // 合言葉あらため**参加コード**。これ単体ではログインできず、/join で自分の
  // パスワードを決めるための鍵になった（2026-09-14 の個人アカウント化）。
  APP_PASSWORD: "ただしい参加コード",
  COOKIE_SECRET: "s".repeat(48),
  DATABASE_URL: "postgres://fake",   // 偽DBを使う合図
  ASSETS: { fetch: () => new Response("<html>本編</html>", { headers: { "content-type": "text/html" } }) },
};
const MY_PASS = "じぶんのパスワード";
// 参加コードで登録してから、自分のパスワードでログインする（今の導線そのまま）
const joinThenLogin = async (name = "天") => {
  await call("/join", { method: "POST",
    body: form({ name, code: ENV.APP_PASSWORD, password: MY_PASS, password2: MY_PASS }) });
  return call("/login", { method: "POST", body: form({ name, password: MY_PASS }) });
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

console.log("入口（参加コードが設定されているとき）");

await test("ログインせずに本編を開くとログインへ送られる", async () => {
  const res = await call("/");
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/login");
});

await test("ログインせずにAPIを叩くと401（HTMLを返さない）", async () => {
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
  const html = await res.text();
  assert.match(html, /お名前/);
  assert.match(html, /パスワード/);
});

await test("パスワードが違えば401で、入れない", async () => {
  const res = await call("/login", { method: "POST", body: form({ name: "天", password: "ちがう" }) });
  assert.equal(res.status, 401);
  assert.equal(res.headers.get("set-cookie"), null);
});

// 参加コードはログインのパスワードではない。ここが 2026-09-14 で変わった点で、
// テストが合言葉のまま取り残されていた。**参加コードだけ知っていても入れない。**
await test("参加コードそのものではログインできない", async () => {
  const res = await call("/login", { method: "POST", body: form({ name: "天", password: ENV.APP_PASSWORD }) });
  assert.equal(res.status, 401);
  assert.equal(res.headers.get("set-cookie"), null);
});

await test("参加コードが違えば登録できない", async () => {
  const res = await call("/join", { method: "POST",
    body: form({ name: "誰か", code: "ちがう", password: MY_PASS, password2: MY_PASS }) });
  assert.equal(res.status, 401);
  assert.equal(res.headers.get("set-cookie"), null);
});

// 参加コードは「まだ持っていない人が最初のパスワードを決める」ための鍵。
// 登録済みの名前を参加コードで上書きできると、コードを知る全員が他人に
// なりすませてしまう。
await test("登録済みの名前は参加コードで上書きできない（なりすまし防止）", async () => {
  await joinThenLogin("上書きされる人");
  const res = await call("/join", { method: "POST",
    body: form({ name: "上書きされる人", code: ENV.APP_PASSWORD, password: "のっとり", password2: "のっとり" }) });
  assert.equal(res.status, 409);
  assert.equal(res.headers.get("set-cookie"), null);
});

await test("名前が空なら通さない（誰が入れたか残らなくなるため）", async () => {
  // パスワードは正しい人のものを使う。名前だけを理由に弾けているかを見たいので、
  // でたらめなパスワードだと「パスワードが違う」で落ちて検査にならない。
  await joinThenLogin("名前ありの人");
  const res = await call("/login", { method: "POST", body: form({ name: "  ", password: MY_PASS }) });
  assert.equal(res.status, 400);
  assert.equal(res.headers.get("set-cookie"), null);
});

await test("自分で決めたパスワードならクッキーが出て本編へ", async () => {
  const res = await joinThenLogin("クッキーの人");
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("location"), "/");
  const sc = res.headers.get("set-cookie");
  assert.match(sc, /HttpOnly/);
  assert.match(sc, /Secure/);
  assert.match(sc, /SameSite=Lax/);
});

await test("そのクッキーで本編が開ける", async () => {
  const login = await joinThenLogin("本編の人");
  const res = await call("/", { headers: { cookie: cookieOf(login) } });
  assert.equal(res.status, 200);
  assert.match(await res.text(), /本編/);
});

await test("クッキーを1文字でも書き換えると通らない（署名）", async () => {
  const login = await joinThenLogin("署名の人");
  const bad = cookieOf(login).slice(0, -1) + (cookieOf(login).endsWith("a") ? "b" : "a");
  assert.equal((await call("/", { headers: { cookie: bad } })).status, 303);
});

await test("別サイトのクッキーは通らない（署名鍵が違う）", async () => {
  // 別の鍵で**本当にログインを成立させて**からクッキーを持ち込む。
  // 以前はここで 401 になっていてクッキーが空文字だったため、
  // 「鍵が違うから弾かれた」ではなく「クッキーが無いから弾かれた」を見ていた。
  // 緑なのに何も検査していない状態だったので、成立を assert で押さえる。
  const other = { ...ENV, COOKIE_SECRET: "t".repeat(48) };
  await call("/join", { method: "POST",
    body: form({ name: "よその人", code: other.APP_PASSWORD, password: MY_PASS, password2: MY_PASS }) }, other);
  const login = await call("/login", { method: "POST",
    body: form({ name: "よその人", password: MY_PASS }) }, other);
  assert.equal(login.status, 303, "別サイト側ではログインできている");
  const cookie = cookieOf(login);
  assert.ok(cookie.length > 20, "クッキーが実際に出ている（空だと検査にならない）");
  assert.equal((await call("/", { headers: { cookie } })).status, 303, "こちらでは通らない");
});

await test("ログアウトでクッキーが消える", async () => {
  const res = await call("/logout");
  assert.equal(res.status, 303);
  assert.match(res.headers.get("set-cookie"), /Max-Age=0/);
});

console.log("入口（参加コードが未設定のとき）");

const NOENV = { ASSETS: ENV.ASSETS };

await test("参加コードが無ければ開けっ放しにせず、全部止める", async () => {
  const res = await call("/", {}, NOENV);
  assert.equal(res.status, 503);
  assert.match(await res.text(), /設定されていません/);
});

await test("参加コードが無ければAPIも止める", async () => {
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

await test("参加コードが未設定なら、ヘッダがあっても全部止まる", async () => {
  const res = await call("/", { headers: { "Cf-Access-Authenticated-User-Email": "amami@8sin.co.jp" } }, NOENV);
  assert.equal(res.status, 503);
});

console.log("開放モード（OPEN_ACCESS=1）");
const OPEN_ENV = { ...ENV, OPEN_ACCESS: "1", ASSETS: ENV.ASSETS };

await test("開放モードならログインなしで本編が見られる", async () => {
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
