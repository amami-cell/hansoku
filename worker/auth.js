// 合言葉ログイン。Cloudflare Access の代わりに、この Worker 自身が入口を守る。
//
// 店長27人に Cloudflare のログインを配るのは重いので、全員共通の合言葉＋お名前で
// 入れるようにした。守りは Access より弱い（合言葉が1人から漏れれば全員ぶん漏れる）。
// 弱くなるぶん、次を必ず守る:
//   - 合言葉の照合は定数時間（1文字ずつ早期returnしない）
//   - クッキーは HMAC 署名つき。名前と期限を書き換えられない
//   - HttpOnly / Secure / SameSite=Lax。JS から読めない・他サイトから送られない
//   - 合言葉が未設定なら「誰でも入れる」ではなく「誰も入れない」に倒す
//
// Access が残っていればそちらを優先する（本人のメールが取れるので記録が正確）。

const enc = new TextEncoder();

const b64url = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function hmac(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return b64url(await crypto.subtle.sign("HMAC", key, enc.encode(message)));
}

/** 長さの違いも含めて、比較にかかる時間を入力に依存させない。 */
export function timingSafeEqual(a, b) {
  const x = enc.encode(String(a));
  const y = enc.encode(String(b));
  // 長さが違っても同じ回数だけ回す。長さの違い自体は最後に足す。
  const n = Math.max(x.length, y.length);
  let diff = x.length ^ y.length;
  for (let i = 0; i < n; i++) diff |= (x[i] || 0) ^ (y[i] || 0);
  return diff === 0;
}

/** 名前・権限・期限を署名して1本の文字列にする。 */
export async function makeToken(secret, name, role, expiresAtMs) {
  const payload = b64url(enc.encode(JSON.stringify({ n: name, r: role || "editor", e: expiresAtMs })));
  return `${payload}.${await hmac(secret, payload)}`;
}

/** 署名と期限を確かめて名前・権限を返す。だめなら null。 */
export async function readToken(secret, token) {
  if (typeof token !== "string" || !token.includes(".")) return null;
  const cut = token.lastIndexOf(".");
  const payload = token.slice(0, cut);
  const sig = token.slice(cut + 1);
  if (!timingSafeEqual(sig, await hmac(secret, payload))) return null;
  let data;
  try {
    const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    data = JSON.parse(new TextDecoder().decode(
      Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)),
    ));
  } catch {
    return null;
  }
  if (!data || typeof data.n !== "string" || typeof data.e !== "number") return null;
  if (Date.now() >= data.e) return null;   // 期限切れ
  return { name: data.n, role: typeof data.r === "string" ? data.r : "editor", expiresAt: data.e };
}

// ── 個人パスワード（PBKDF2でハッシュ化して保存。平文は保存しない）─────────────
const b64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)));
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const PBKDF2_ITER = 120000;

/** パスワードをハッシュ化。saltB64 を渡さなければ新しい塩を作る。 */
export async function hashPassword(password, saltB64) {
  const salt = saltB64 ? unb64(saltB64) : crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey("raw", enc.encode(String(password)), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations: PBKDF2_ITER, hash: "SHA-256" }, key, 256);
  return { hash: b64(new Uint8Array(bits)), salt: b64(salt) };
}

/** 保存済みの塩・ハッシュと照合（定数時間比較）。 */
export async function verifyPassword(password, saltB64, hashB64) {
  if (!saltB64 || !hashB64) return false;
  const { hash } = await hashPassword(password, saltB64);
  return timingSafeEqual(hash, hashB64);
}

export function readCookie(header, name) {
  for (const part of String(header || "").split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    if (part.slice(0, i).trim() === name) {
      try {
        return decodeURIComponent(part.slice(i + 1).trim());
      } catch {
        return part.slice(i + 1).trim();
      }
    }
  }
  return null;
}

export const COOKIE = "hansoku_session";
// 一度ログインしたら、その端末ではずっと入れっぱなしにする（実質1年）。
// 期限が来ても、使っていれば下の「延長」で自動で伸びるので再ログインは要らない。
export const SESSION_DAYS = 365;

export function sessionCookie(token, days = SESSION_DAYS) {
  const maxAge = Math.round(days * 24 * 60 * 60);
  return `${COOKIE}=${encodeURIComponent(token)}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
}

export const clearCookie = () =>
  `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;

/**
 * 誰として扱うかを決める。null なら入れない。判断材料は署名クッキーだけ。
 *
 * ここで Cf-Access-Authenticated-User-Email を信用してはいけない。
 * このヘッダは、Cloudflare Access が実際にそのルートを覆っているときだけ
 * Cloudflare が付けるもので、それ以外では**クライアントが自由に付けられる**。
 * docs/deploy.md は合言葉方式にあたって Access アプリを削除するよう案内して
 * いるので、覆いは無い。信用すると
 *     curl -H 'Cf-Access-Authenticated-User-Email: x@y' <URL>
 * の1行で合言葉もクッキーも素通りし、全店の売上が見える（実際に再現した）。
 *
 * 将来ふたたび Access を前段に置くなら、ヘッダではなく
 * Cf-Access-Jwt-Assertion を Cloudflare の公開鍵(JWKS)で検証すること。
 * 検証しないヘッダは、無いのと同じ。
 */
export async function identify(request, env) {
  if (!env.COOKIE_SECRET) return null;
  const token = readCookie(request.headers.get("cookie"), COOKIE);
  const v = token && (await readToken(env.COOKIE_SECRET, token));
  return v ? { who: v.name, role: v.role, via: "user" } : null;
}
