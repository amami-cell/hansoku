// 実データ（web/data/dashboard.json）＋本番の app.js で、スケジュール一覧
// （storeAnnualCalendar）が実際に何を描くかをサーバ側で生成して確認する。
// ブラウザ無しで、画面に出る文字を目で確かめるための検証。
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const src = fs.readFileSync(path.join(ROOT, "web/app.js"), "utf8");
const data = JSON.parse(fs.readFileSync(path.join(ROOT, "web/data/dashboard.json"), "utf8"));

const code = process.env.STORE || "1160";
const year = process.env.YEAR || "2026";

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
// その年の全月を開いた状態で描画（見出しの数値＋展開ぶん両方を確認）
const openAll = {};
for (let mo = 1; mo <= 12; mo++) openAll[`${code}:${year}-${String(mo).padStart(2, "0")}`] = true;
vm.runInContext(`ANNUAL_OPEN = ${JSON.stringify(openAll)};`, ctx);

const store = (data.stores || []).find(s => s.code === code);
console.log(`== ${store ? store.name : code} / ${year}年 スケジュール一覧（storeAnnualCalendar）==`);
const html = vm.runInContext(`storeAnnualCalendar("${code}","${year}")`, ctx);
// タグを外して、月ごとに1行で読めるように整形
const text = html
  .replace(/<button[^>]*class="mhd[^"]*"[^>]*>/g, "\n【月】")
  .replace(/<span class="msl">/g, " ")
  .replace(/<span class="msv">/g, "=")
  .replace(/<[^>]+>/g, " ")
  .replace(/[ \t]+/g, " ")
  .replace(/\n /g, "\n")
  .trim();
console.log(text.slice(0, 4000));
