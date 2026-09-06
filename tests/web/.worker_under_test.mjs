// 本番 hansoku の Worker。静的アセット配信に、販促目標・メモの読み書きAPIを足す。
//   GET  /api/targets             … 全目標を返す { targets: { key: {value,by,at} } }
//   POST /api/targets {id,target} … 目標を保存／削除（target=null で削除）
//   GET/POST /api/notes           … 要因メモ（同じ形）
//
// 入口は Worker 自身が守る（合言葉＋お名前 → 署名クッキー30日）。
// Cloudflare Access が前段に残っていればそちらを優先する（本人のメールが取れる）。
// 合言葉もAccessも無ければ、開けっ放しにせず全部拒否する。
const neon = () => { throw new Error('DBは使わない'); };

import {
  clearCookie, identify, makeToken, sessionCookie, SESSION_DAYS, timingSafeEqual,
} from "./auth.js";
import { htmlResponse, loginPage } from "./login.js";

const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

/** 合言葉が正しければセッションを発行する。 */
async function handleLogin(request, env) {
  if (!env.APP_PASSWORD || !env.COOKIE_SECRET) {
    return htmlResponse(
      loginPage({ error: "合言葉がまだ設定されていません。本部（システム担当）へ連絡してください。" }),
      503,
    );
  }
  if (request.method === "GET") return htmlResponse(loginPage());
  if (request.method !== "POST") return json({ error: "method" }, 405);

  const form = await request.formData();
  const name = String(form.get("name") || "").trim().slice(0, 40);
  const password = String(form.get("password") || "");

  // 総当たりを少しでも割に合わなくする。合否によらず同じだけ待つ。
  await new Promise((r) => setTimeout(r, 400));

  if (!timingSafeEqual(password, env.APP_PASSWORD)) {
    return htmlResponse(loginPage({ error: "合言葉が違います。", name }), 401);
  }
  if (!name) {
    return htmlResponse(loginPage({ error: "お名前を入れてください。" }), 400);
  }
  const token = await makeToken(
    env.COOKIE_SECRET, name, Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000,
  );
  return new Response(null, {
    status: 303,
    headers: { location: "/", "set-cookie": sessionCookie(token), "cache-control": "no-store" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/login") {
      try {
        return await handleLogin(request, env);
      } catch (e) {
        return json({ error: String((e && e.message) || e) }, 500);
      }
    }
    if (url.pathname === "/logout") {
      return new Response(null, {
        status: 303,
        headers: { location: "/login", "set-cookie": clearCookie(), "cache-control": "no-store" },
      });
    }

    // ここから先は入口の内側。誰として扱うかが決まらなければ通さない。
    const me = await identify(request, env);
    if (!me) {
      if (url.pathname.startsWith("/api/")) return json({ error: "unauthenticated" }, 401);
      if (!env.APP_PASSWORD || !env.COOKIE_SECRET) {
        // 合言葉が未設定 ＝ 入口が無い。開けっ放しにはしない。
        return htmlResponse(
          loginPage({ error: "合言葉がまだ設定されていません。本部（システム担当）へ連絡してください。" }),
          503,
        );
      }
      return new Response(null, {
        status: 303,
        headers: { location: "/login", "cache-control": "no-store" },
      });
    }

    if (url.pathname === "/api/targets") {
      try {
        return await handleTargets(request, env, me.who);
      } catch (e) {
        return json({ error: String((e && e.message) || e) }, 500);
      }
    }
    if (url.pathname === "/api/notes") {
      try {
        return await handleNotes(request, env, me.who);
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

async function handleNotes(request, env, who) {
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
    const email = who || "";
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

async function handleTargets(request, env, who) {
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
    const email = who || "";
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
