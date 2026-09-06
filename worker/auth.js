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

/** 名前と期限を署名して1本の文字列にする。 */
export async function makeToken(secret, name, expiresAtMs) {
  const payload = b64url(enc.encode(JSON.stringify({ n: name, e: expiresAtMs })));
  return `${payload}.${await hmac(secret, payload)}`;
}

/** 署名と期限を確かめて名前を返す。だめなら null。 */
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
  return { name: data.n, expiresAt: data.e };
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
export const SESSION_DAYS = 30;

export function sessionCookie(token, days = SESSION_DAYS) {
  const maxAge = Math.round(days * 24 * 60 * 60);
  return `${COOKIE}=${encodeURIComponent(token)}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Lax`;
}

export const clearCookie = () =>
  `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;

/**
 * 誰として扱うかを決める。null なら入れない。
 *   1. Cloudflare Access が残っていればそのメール（記録が正確なので優先）
 *   2. 合言葉のセッション（署名クッキー）
 * 合言葉が未設定なら 2 は成立しない ＝ 誰も入れない（開けっ放しにしない）。
 */
export async function identify(request, env) {
  const email = request.headers.get("Cf-Access-Authenticated-User-Email");
  if (email) return { who: email, via: "access" };
  if (!env.APP_PASSWORD || !env.COOKIE_SECRET) return null;
  const token = readCookie(request.headers.get("cookie"), COOKIE);
  const v = token && (await readToken(env.COOKIE_SECRET, token));
  return v ? { who: v.name, via: "passphrase" } : null;
}
