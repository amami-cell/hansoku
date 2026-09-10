// 実データ（web/data/dashboard.json）＋本番 app.js で、店舗ページ全体
// （renderStore）をサーバ側で組み立て、画面に出る文字を段組みで確認する。
// ブラウザ無しで、店長が実際に目にする1ページの流れを検証するための道具。
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const src = fs.readFileSync(path.join(ROOT, "web/app.js"), "utf8");
const data = JSON.parse(fs.readFileSync(path.join(ROOT, "web/data/dashboard.json"), "utf8"));

const code = process.env.STORE || "1160";

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
// 本番と同じ既定。制作物・目標APIは本番で使えるので true 相当で見る。
vm.runInContext(`WRITE_OK = false; API_OK = true; CREATIVES_API_OK = true; METRIC = "sales";`, ctx);

const store = (data.stores || []).find(s => s.code === code);
console.log(`== ${store ? store.name : code} 店舗ページ（renderStore）== 全体の流れ ==\n`);
const html = vm.runInContext(`renderStore("${code}")`, ctx);

// セクション見出し（h1/h2）だけ拾って、ページの骨格を出す
const heads = [...html.matchAll(/<h([12])[^>]*>([\s\S]*?)<\/h\1>/g)]
  .map(m => `${m[1] === "1" ? "#" : "  ##"} ${m[2].replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim()}`);
console.log("── 骨格（見出しの並び）──");
console.log(heads.join("\n"));
console.log(`\n（HTML 総量 ${(html.length / 1024).toFixed(1)} KB）\n`);

// 全文をテキスト化（タグ除去）して、頭から一定量を確認
const text = html
  .replace(/<button[^>]*class="mhd[^"]*"[^>]*>/g, "\n【月】")
  .replace(/<\/section>/g, "\n———\n")
  .replace(/<[^>]+>/g, " ")
  .replace(/[ \t]+/g, " ")
  .replace(/\n /g, "\n")
  .replace(/\n{3,}/g, "\n\n")
  .trim();
console.log("── 本文（先頭 3500 字）──");
console.log(text.slice(0, 3500));
