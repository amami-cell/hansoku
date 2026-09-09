// 本番 hansoku の Worker。静的アセット配信に、販促目標・メモの読み書きAPIを足す。
//   GET  /api/targets             … 全目標を返す { targets: { key: {value,by,at} } }
//   POST /api/targets {id,target} … 目標を保存／削除（target=null で削除）
//   GET/POST /api/notes           … 要因メモ（同じ形）
//
// 入口は Worker 自身が守る（合言葉＋お名前 → 署名クッキー30日）。
// Cloudflare Access が前段に残っていればそちらを優先する（本人のメールが取れる）。
// 合言葉もAccessも無ければ、開けっ放しにせず全部拒否する。
import { neon } from "@neondatabase/serverless";

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

// 例外の本文はブラウザに返さない。@neondatabase/serverless は接続文字列が
// 不正なとき、その接続文字列（＝パスワード入り）をメッセージに含めることがある。
// 調べるための情報はログにだけ出す。
const serverError = (e) => {
  console.error("[hansoku]", e && e.stack ? e.stack : String(e));
  return json({ error: "server-error" }, 500);
};

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
        return serverError(e);
      }
    }
    if (url.pathname === "/logout") {
      return new Response(null, {
        status: 303,
        headers: { location: "/login", "set-cookie": clearCookie(), "cache-control": "no-store" },
      });
    }

    // ここから先は入口の内側。誰として扱うかが決まらなければ通さない。
    // OPEN_ACCESS=1 のときは「開放モード」＝ログイン無しで誰でも入れる（合言葉を外す）。
    //   ※全店の実売上がURLだけで見えるようになる。戻すときは vars の OPEN_ACCESS を消す。
    let me = await identify(request, env);
    if (!me && env.OPEN_ACCESS === "1") me = { who: "オープン", via: "open" };
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

    // 開放モード（ログイン無し）は「閲覧専用」。誰でも見られるが、共有データ
    // （制作物POP・目標・要因メモ）の書き換え・削除・アップロードはさせない。
    // 書き込みは本人が特定できる入口（合言葉／Access）の内側だけに限る。
    if (me.via === "open" && url.pathname.startsWith("/api/") && request.method !== "GET") {
      return json({ error: "read-only", detail: "開放モードは閲覧専用です" }, 403);
    }

    if (url.pathname === "/api/targets") {
      try {
        return await handleTargets(request, env, me.who);
      } catch (e) {
        return serverError(e);
      }
    }
    if (url.pathname === "/api/notes") {
      try {
        return await handleNotes(request, env, me.who);
      } catch (e) {
        return serverError(e);
      }
    }
    if (url.pathname === "/api/creatives") {
      try {
        return await handleCreatives(request, env, me.who);
      } catch (e) {
        return serverError(e);
      }
    }
    // 制作物PDF（R2）。Access の内側で同一ドメイン配信する。
    if (url.pathname.startsWith("/creatives/")) {
      try {
        return await handleCreative(url, env);
      } catch (e) {
        return serverError(e);
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

// アプリ内アップロードの制作物（POP・画像・資料）。GET=一覧 / POST=追加 / DELETE=削除。
// 実体は R2（/creatives/uploads/...）、メタは promo_creatives（Neon）。
const CR_MAX_BYTES = 25 * 1024 * 1024; // 1ファイル25MBまで
const CR_MIME_EXT = {
  "application/pdf": "pdf",
  "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/heic": "heic",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "application/vnd.ms-excel": "xls",
  "text/csv": "csv",
};
const crSafe = (s, n) => String(s || "").replace(/[^0-9A-Za-z._\-]+/g, "_").slice(0, n);

async function handleCreatives(request, env, who) {
  if (!env.DATABASE_URL) return json({ error: "no-db" }, 503);
  const sql = neon(env.DATABASE_URL);

  if (request.method === "GET") {
    const rows = await sql`
      SELECT id, campaign_id, store_code, title, kind, r2_key, mime, doc_date, set_by, set_at
      FROM promo_creatives ORDER BY doc_date DESC NULLS LAST, set_at DESC`;
    const creatives = rows.map((r) => ({
      id: r.id, campaign_id: r.campaign_id || "", store_code: r.store_code || "",
      title: r.title, kind: r.kind || "dev", mime: r.mime || "",
      date: r.doc_date || "", by: r.set_by || "", uploaded: true,
      url: "/" + String(r.r2_key).replace(/^\/+/, ""),
    }));
    // 開放モードは閲覧専用。画面が「追加/削除」ボタンを出さないための目印。
    return json({ creatives, readonly: who === "オープン" });
  }

  if (request.method === "POST") {
    const email = who || "";
    if (!email) return json({ error: "unauthenticated" }, 401);
    if (!env.CREATIVES) return json({ error: "no-bucket" }, 503);

    let form;
    try {
      form = await request.formData();
    } catch {
      return json({ error: "bad-form" }, 400);
    }
    const file = form.get("file");
    if (!file || typeof file.arrayBuffer !== "function") return json({ error: "no-file" }, 400);

    const mime = String(file.type || "application/octet-stream");
    if (!CR_MIME_EXT[mime]) return json({ error: "bad-type", mime }, 415);

    const buf = await file.arrayBuffer();
    if (buf.byteLength === 0) return json({ error: "empty" }, 400);
    if (buf.byteLength > CR_MAX_BYTES) return json({ error: "too-large" }, 413);

    const campaign = crSafe(form.get("campaign"), 128);
    const store = crSafe(form.get("store"), 16);
    if (!campaign && !store) return json({ error: "no-target" }, 400);
    const title = (String(form.get("title") || file.name || "資料")).slice(0, 200);
    const kind = crSafe(form.get("kind"), 16) || "dev";
    const docDate = crSafe(form.get("date"), 10);

    const ext = CR_MIME_EXT[mime];
    const base = crSafe((file.name || "file").replace(/\.[^.]+$/, ""), 40) || "file";
    const rand = crypto.randomUUID().slice(0, 8);
    const bucketKey = `creatives/uploads/${campaign || store}/${Date.now()}_${rand}_${base}.${ext}`;

    await env.CREATIVES.put(bucketKey, buf, { httpMetadata: { contentType: mime } });
    await sql`
      INSERT INTO promo_creatives (id, campaign_id, store_code, title, kind, r2_key, mime, doc_date, set_by, set_at)
      VALUES (${rand + "_" + Date.now()}, ${campaign}, ${store}, ${title}, ${kind}, ${bucketKey}, ${mime}, ${docDate}, ${email}, now())`;

    return json({
      ok: true,
      creative: {
        id: rand, campaign_id: campaign, store_code: store, title, kind, mime,
        date: docDate, by: email, uploaded: true, url: "/" + bucketKey,
      },
    });
  }

  if (request.method === "DELETE") {
    const email = who || "";
    if (!email) return json({ error: "unauthenticated" }, 401);
    let body;
    try { body = await request.json(); } catch { return json({ error: "bad-json" }, 400); }
    const id = typeof body.id === "string" ? body.id.slice(0, 128) : "";
    if (!id) return json({ error: "no-id" }, 400);
    const rows = await sql`SELECT r2_key FROM promo_creatives WHERE id = ${id}`;
    if (rows[0] && env.CREATIVES) {
      try { await env.CREATIVES.delete(rows[0].r2_key); } catch (e) { console.error("[hansoku] r2 delete", e); }
    }
    await sql`DELETE FROM promo_creatives WHERE id = ${id}`;
    return json({ ok: true, id });
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
