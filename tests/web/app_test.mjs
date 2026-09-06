// web/app.js の計算部分を、ブラウザ無しで動かして確かめる。
//
// app.js は 2900行の1ファイルで、いままで node --check しか当たっていなかった。
// 前年比・効果判定・目標達成率という「数字を作っている側」が無検査だったので、
// 施策の主指標まわりだけでも固定しておく。
//
// 読み込み時にDOMを触るのは最後の addEventListener だけなので、最小限の
// document / window を置いて評価する。
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const src = fs.readFileSync(path.join(ROOT, "web/app.js"), "utf8");

function loadApp(data, today = "2026-09-06") {
  const noop = () => {};
  const el = new Proxy({}, {
    get: (_t, k) => (k === "textContent" || k === "innerHTML" ? "" : noop),
    set: () => true,
  });
  const sandbox = {
    console,
    Math, Date, JSON, Intl,
    document: {
      getElementById: () => el,
      documentElement: el,
      addEventListener: noop,
      querySelectorAll: () => [],
    },
    window: { prompt: () => null, matchMedia: () => ({ matches: false }) },
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    fetch: noop,
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(src, ctx, { filename: "app.js" });
  vm.runInContext(`DATA = ${JSON.stringify(data)};`, ctx);
  return ctx;
}

const call = (ctx, expr) => vm.runInContext(expr, ctx);

// ── 土台となる最小データ ───────────────────────────────────────────────
// 1006 の「コース」部門が 2026-01 に 100万、前年同月 2025-01 に 80万。
// 店全体の売上は前年より落としてある（+25% の部門と -20% の店全体を区別させる）。
const bucket = (name, sales, total) => ({
  total_sales: total,
  buckets: [{ name, sales, qty: 10, share: sales / total, cost_rate: 30 }],
  raw: [],
});
const base = {
  generated_at: "2026-09-06T00:00:00+00:00",
  months: ["2025-01", "2026-01"],
  stores: [{ code: "1006", name: "すさび湯 梅田", region: "大阪", neighbors: [] }],
  monthly: { 1006: { "2025-01": { sales: 10000000 }, "2026-01": { sales: 8000000 } } },
  covers: {}, cost_rate: {}, budget: {}, hourly: {},
  products: {}, products_group: [], departments: {},
  departments_monthly: {
    1006: {
      "2025-01": bucket("コース", 800000, 10000000),
      "2026-01": bucket("コース", 1000000, 8000000),
    },
  },
  products_monthly: {
    1006: {
      "2025-01": [{ name: "忘年会コース", sales: 500000, rank: "A" }],
      "2026-01": [{ name: "忘年会コース", sales: 700000, rank: "A" }],
    },
  },
  campaigns: [], creatives: [], lunch: [], proposals: [], regions: [], env_effects: [],
};
const camp = (over) => ({
  id: "c1", stores: ["1006"], scope_all: false, title: "t", kind: "dev",
  start: "2026-01-01", end: "2026-01-31", note: "", bucket: null, items: [], ...over,
});

let failed = 0;
function test(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failed += 1; console.log("  FAIL " + name + "\n       " + e.message); }
}

console.log("施策の主指標（campTargeted）");

test("bucket を書くと、その部門だけの前年比になる（店全体の -20% ではなく +25%）", () => {
  const c = camp({ bucket: "コース" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const t = call(ctx, `campTargeted(${JSON.stringify(c)})`);
  assert.equal(t.label, "コース");
  assert.equal(t.cur, 1000000);
  assert.equal(t.prev, 800000);
  assert.equal(Math.round(t.pct), 25);
});

test("items を書くと、その商品だけの前年比になる", () => {
  const c = camp({ items: ["忘年会"] });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const t = call(ctx, `campTargeted(${JSON.stringify(c)})`);
  assert.equal(t.cur, 700000);
  assert.equal(t.prev, 500000);
  assert.equal(Math.round(t.pct), 40);
});

test("items は bucket より優先される", () => {
  const c = camp({ bucket: "コース", items: ["忘年会"] });
  const ctx = loadApp({ ...base, campaigns: [c] });
  assert.equal(call(ctx, `campTargeted(${JSON.stringify(c)}).cur`), 700000);
});

test("kind から部門を推定する（bounenkai → コース）", () => {
  const c = camp({ kind: "bounenkai" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  assert.equal(call(ctx, `campTargeted(${JSON.stringify(c)}).label`), "コース");
});

test("GM改定は店全体が範囲（メニューを丸ごと入れ替えるため）", () => {
  const c = camp({ kind: "gm" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const t = call(ctx, `campTargeted(${JSON.stringify(c)})`);
  assert.equal(t.storeWide, true);
  assert.equal(t.cur, 8000000);
  assert.equal(t.prev, 10000000);
  // 店全体で判定するが、重なりの件数を必ず添える
  const v = call(ctx, `campVerdict(${JSON.stringify(c)})`);
  assert.equal(v.label, "要改善");
  assert.ok(v.signals.some(x => /重なっ/.test(x)), v.signals.join(" / "));
});

test("bucket も items も無く kind からも決まらなければ null", () => {
  const c = camp({ kind: "osusume" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  assert.equal(call(ctx, `campTargeted(${JSON.stringify(c)})`), null);
  assert.equal(call(ctx, `campBasis(${JSON.stringify(c)})`), null);
});

test("当月（暫定）は入れない", () => {
  const c = camp({ bucket: "コース", start: "2026-09-01", end: "2026-09-30" });
  const data = { ...base, departments_monthly: { 1006: {
    "2025-09": bucket("コース", 100, 1000), "2026-09": bucket("コース", 999, 1000),
  } } };
  const ctx = loadApp({ ...data, campaigns: [c] });
  assert.equal(call(ctx, `campTargeted(${JSON.stringify(c)})`), null);
});

console.log("効果判定（campVerdict）");

test("測り方が未設定なら、効果あり/要改善を出さない", () => {
  const c = camp({ kind: "osusume" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const v = call(ctx, `campVerdict(${JSON.stringify(c)})`);
  assert.equal(v.label, "測り方 未設定");
  assert.equal(v.tone, "flat");
});

test("部門が伸びていれば効果あり（店全体が落ちていても）", () => {
  const c = camp({ bucket: "コース" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const v = call(ctx, `campVerdict(${JSON.stringify(c)})`);
  assert.equal(v.label, "効果あり");
});

test("部門が落ちていれば要改善（店全体が伸びていても）", () => {
  const c = camp({ bucket: "コース" });
  const data = {
    ...base,
    monthly: { 1006: { "2025-01": { sales: 8000000 }, "2026-01": { sales: 10000000 } } },
    departments_monthly: { 1006: {
      "2025-01": bucket("コース", 1000000, 8000000),
      "2026-01": bucket("コース", 800000, 10000000),
    } },
  };
  const ctx = loadApp({ ...data, campaigns: [c] });
  assert.equal(call(ctx, `campVerdict(${JSON.stringify(c)})`).label, "要改善");
});

console.log("重なりの件数（campOverlap）");

test("同じ店・同じ期間の他施策を数える", () => {
  const a = camp({ id: "a", bucket: "コース" });
  const b = camp({ id: "b", start: "2026-01-15", end: "2026-02-15" });
  const far = camp({ id: "far", start: "2026-06-01", end: "2026-06-30" });
  const ctx = loadApp({ ...base, campaigns: [a, b, far] });
  assert.equal(call(ctx, `campOverlap(${JSON.stringify(a)})`), 1);
  assert.match(call(ctx, `overlapNote(${JSON.stringify(a)})`), /重なる施策 1件/);
});

test("重なりが無ければその旨を出す", () => {
  const a = camp({ id: "a" });
  const ctx = loadApp({ ...base, campaigns: [a] });
  assert.equal(call(ctx, `campOverlap(${JSON.stringify(a)})`), 0);
  assert.match(call(ctx, `overlapNote(${JSON.stringify(a)})`), /重なっていません/);
});

console.log("目標達成率（campGoalRate）");

test("画面の指標を切り替えても、達成率は売上のまま", () => {
  const c = camp({ id: "c1", bucket: "コース", target: 10000000 });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const before = call(ctx, `campGoalRate(${JSON.stringify(c)}).cur`);
  call(ctx, `METRIC = "drink_sales";`);
  const after = call(ctx, `campGoalRate(${JSON.stringify(c)}).cur`);
  assert.equal(before, after);
  assert.equal(before, 8000000);
});

console.log(failed ? `\n${failed} 件失敗` : "\nすべて通過");
process.exit(failed ? 1 : 0);
