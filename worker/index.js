// 本番 hansoku（Cloudflare Access 保護）の Worker。
// 静的アセット配信に、販促目標の読み書きAPI（/api/targets）を足す。
//   GET  /api/targets            … 全目標を返す { targets: { id: {value,by,at} } }
//   POST /api/targets  {id,target}… 目標を保存／削除（target=null で削除）
// 認証は Cloudflare Access。POST は Access が付ける本人メール
// (Cf-Access-Authenticated-User-Email) が無ければ拒否する（ログイン必須）。
import { neon } from "@neondatabase/serverless";

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/targets") {
      try {
        return await handleTargets(request, env);
      } catch (e) {
        return json({ error: String((e && e.message) || e) }, 500);
      }
    }
    if (url.pathname === "/api/notes") {
      try {
        return await handleNotes(request, env);
      } catch (e) {
        return json({ error: String((e && e.message) || e) }, 500);
      }
    }
    // 制作物PDF（R2）。Access の内側で同一ドメイン配信する。
    if (url.pathname.startsWith("/creatives/")) {
      try {
        return await handleCreative(url, env);
      } catch (e) {
        return json({ error: String((e && e.message) || e) }, 500);
      }
    }
    // それ以外は静的アセット（web/）を返す
    return env.ASSETS.fetch(request);
  },
};

async function handleNotes(request, env) {
  if (!env.DATABASE_URL) return json({ error: "no-db" }, 503);
  const sql = neon(env.DATABASE_URL);

  if (request.method === "GET") {
    const rows = await sql`SELECT campaign_id, note, set_by, set_at FROM promo_notes`;
    const notes = {};
    for (const r of rows) {
      if ((r.note || "").trim()) notes[r.campaign_id] = { note: r.note, by: r.set_by, at: r.set_at };
    }
    return json({ notes });
  }

  if (request.method === "POST") {
    const email = request.headers.get("Cf-Access-Authenticated-User-Email") || "";
    if (!email) return json({ error: "unauthenticated" }, 401);

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "bad-json" }, 400);
    }
    const id = typeof body.id === "string" ? body.id.slice(0, 128) : "";
    if (!id) return json({ error: "no-id" }, 400);

    const note = typeof body.note === "string" ? body.note.slice(0, 2000).trim() : "";
    if (!note) {
      await sql`DELETE FROM promo_notes WHERE campaign_id = ${id}`;
      return json({ ok: true, id, note: "" });
    }
    await sql`
      INSERT INTO promo_notes (campaign_id, note, set_by, set_at)
      VALUES (${id}, ${note}, ${email}, now())
      ON CONFLICT (campaign_id) DO UPDATE
        SET note = EXCLUDED.note, set_by = EXCLUDED.set_by, set_at = now()`;
    return json({ ok: true, id, note, by: email });
  }

  return json({ error: "method" }, 405);
}

async function handleCreative(url, env) {
  if (!env.CREATIVES) return json({ error: "no-bucket" }, 503);
  const key = decodeURIComponent(url.pathname.slice(1)); // 先頭の / を落として R2 キーに
  const obj = await env.CREATIVES.get(key);
  if (!obj) return json({ error: "not-found", key }, 404);
  const headers = new Headers();
  obj.writeHttpMetadata(headers);
  headers.set("content-type", (obj.httpMetadata && obj.httpMetadata.contentType) || "application/pdf");
  headers.set("etag", obj.httpEtag);
  // Access の内側限定なので private。1時間はキャッシュ可。
  headers.set("cache-control", "private, max-age=3600");
  return new Response(obj.body, { headers });
}

async function handleTargets(request, env) {
  if (!env.DATABASE_URL) return json({ error: "no-db" }, 503);
  const sql = neon(env.DATABASE_URL);

  if (request.method === "GET") {
    const rows = await sql`SELECT campaign_id, target_value, set_by, set_at FROM promo_targets`;
    const targets = {};
    for (const r of rows) {
      targets[r.campaign_id] = { value: Number(r.target_value), by: r.set_by, at: r.set_at };
    }
    return json({ targets });
  }

  if (request.method === "POST") {
    const email = request.headers.get("Cf-Access-Authenticated-User-Email") || "";
    if (!email) return json({ error: "unauthenticated" }, 401);

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: "bad-json" }, 400);
    }
    const id = typeof body.id === "string" ? body.id.slice(0, 128) : "";
    if (!id) return json({ error: "no-id" }, 400);

    const t = body.target;
    if (t === null || t === undefined || t === "") {
      await sql`DELETE FROM promo_targets WHERE campaign_id = ${id}`;
      return json({ ok: true, id, target: null });
    }
    const value = Math.round(Number(t));
    if (!Number.isFinite(value) || value < 0) return json({ error: "bad-target" }, 400);
    await sql`
      INSERT INTO promo_targets (campaign_id, target_value, set_by, set_at)
      VALUES (${id}, ${value}, ${email}, now())
      ON CONFLICT (campaign_id) DO UPDATE
        SET target_value = EXCLUDED.target_value, set_by = EXCLUDED.set_by, set_at = now()`;
    return json({ ok: true, id, target: value, by: email });
  }

  return json({ error: "method" }, 405);
}
