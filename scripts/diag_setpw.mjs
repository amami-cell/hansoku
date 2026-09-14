// 一時診断：/setpw の書き込み経路（ensureUsers + INSERT + PBKDF2）を実DBで再現する。
// NEON_DATABASE_URL は Actions 上でマスクされる。DSN は絶対に出力しない。
import { neon } from "@neondatabase/serverless";

const url = process.env.NEON_DATABASE_URL;
if (!url) { console.log("NEON_DATABASE_URL 未設定"); process.exit(0); }

const enc = new TextEncoder();
const b64 = (b) => Buffer.from(b).toString("base64");
async function hashPassword(password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey("raw", enc.encode(String(password)), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt, iterations: 120000, hash: "SHA-256" }, key, 256);
  return { hash: b64(new Uint8Array(bits)), salt: b64(salt) };
}
const safe = (e) => String(e && e.message ? e.message : e).replace(/postgres(ql)?:\/\/[^ ]+/gi, "postgres://REDACTED");

const sql = neon(url);
const name = "診断テスト " + Date.now();
try {
  console.log("1) PBKDF2 hash...");
  const { hash, salt } = await hashPassword("mypw12");
  console.log("   ok hashlen=", hash.length);

  console.log("2) ensureUsers (CREATE TABLE IF NOT EXISTS)...");
  await sql`CREATE TABLE IF NOT EXISTS app_users (
    name TEXT PRIMARY KEY, pass_hash TEXT NOT NULL DEFAULT '', pass_salt TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'editor', disabled BOOLEAN NOT NULL DEFAULT false,
    must_reset BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login TIMESTAMPTZ)`;
  console.log("   ok");

  console.log("3) SELECT (getUser)...");
  const rows = await sql`SELECT name, pass_hash, pass_salt, role, disabled, must_reset FROM app_users WHERE name = ${name}`;
  console.log("   ok rows=", rows.length);

  console.log("4) INSERT ... ON CONFLICT (the failing suspect)...");
  await sql`
    INSERT INTO app_users (name, pass_hash, pass_salt, role, disabled, must_reset, created_at, updated_at, last_login)
    VALUES (${name}, ${hash}, ${salt}, ${'owner'}, false, false, now(), now(), now())
    ON CONFLICT (name) DO UPDATE
      SET pass_hash=EXCLUDED.pass_hash, pass_salt=EXCLUDED.pass_salt, role=${'owner'},
          must_reset=false, updated_at=now()`;
  console.log("   ok INSERT succeeded");

  console.log("5) cleanup...");
  await sql`DELETE FROM app_users WHERE name = ${name}`;
  console.log("ALL OK — 書き込み経路は正常");
} catch (e) {
  console.log("FAILED at some step:");
  console.log("  name:", e && e.name);
  console.log("  code:", e && e.code);
  console.log("  msg :", safe(e));
}
