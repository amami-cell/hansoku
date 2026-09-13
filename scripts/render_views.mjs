// 実データ＋本番 app.js で、トップの一覧画面（renderSchedule）と店舗一覧
// （renderList）をサーバ側で組み立て、要約が分かるかを確認する。管理しやすさの監査用。
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const src = fs.readFileSync(path.join(ROOT, "web/app.js"), "utf8");
const data = JSON.parse(fs.readFileSync(path.join(ROOT, "web/data/dashboard.json"), "utf8"));

const noop = () => {};
const el = new Proxy({}, { get: (_t, k) => (k === "textContent" || k === "innerHTML" ? "" : noop), set: () => true });
const sandbox = {
  console, Math, Date, JSON, Intl,
  document: { getElementById: () => el, documentElement: el, addEventListener: noop, querySelectorAll: () => [] },
  window: { prompt: () => null, matchMedia: () => ({ matches: false }) },
  localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
  fetch: noop,
};
sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);
vm.runInContext(src, ctx, { filename: "app.js" });
vm.runInContext(`DATA = ${JSON.stringify(data)};`, ctx);
vm.runInContext(`WRITE_OK=false; API_OK=true; CREATIVES_API_OK=true; METRIC="sales";`, ctx);

const toText = (html) => html
  .replace(/<\/section>/g, "\n———\n")
  .replace(/<button class="rptile[^"]*"[^>]*>/g, "\n[タイル] ")
  .replace(/<button class="scard[^"]*"[^>]*>/g, "\n[店] ")
  .replace(/<[^>]+>/g, " ").replace(/[ \t]+/g, " ").replace(/\n /g, "\n").replace(/\n{3,}/g, "\n\n").trim();

for (const [label, expr] of [["トップ（renderSchedule）", "renderSchedule()"], ["店舗一覧（renderList）", "renderList()"]]) {
  console.log(`\n===== ${label} =====`);
  const html = vm.runInContext(expr, ctx);
  console.log(toText(html).slice(0, 3200));
}
