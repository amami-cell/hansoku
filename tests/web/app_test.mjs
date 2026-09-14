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

test("達成率の分子は主指標。店全体の売上で割らない", () => {
  // コース部門100万に対して目標100万 → 100%。店全体(800万)で割ると800%になる。
  const c = camp({ id: "c1", bucket: "コース" });
  c.key = "c1@2026";
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; SERVER_TARGETS = { "c1@2026": { value: 1000000 } };`);
  const gr = call(ctx, `campGoalRate(${JSON.stringify(c)})`);
  assert.equal(gr.cur, 1000000);
  assert.equal(Math.round(gr.rate), 100);
  assert.equal(gr.label, "コース");
});

test("画面の指標を切り替えても、達成率は動かない", () => {
  const c = camp({ id: "c1", bucket: "コース" });
  c.key = "c1@2026";
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; SERVER_TARGETS = { "c1@2026": { value: 1000000 } };`);
  const before = call(ctx, `campGoalRate(${JSON.stringify(c)}).cur`);
  call(ctx, `METRIC = "drink_sales";`);
  assert.equal(call(ctx, `campGoalRate(${JSON.stringify(c)}).cur`), before);
});

test("終了日を書かない施策は「実施中」で、効果は直近まで見る", () => {
  const c = camp({ id: "gm1", kind: "gm", start: "2026-01-01", end: "2026-01-01" });
  c.open_ended = true;
  const ctx = loadApp({ ...base, campaigns: [c] });
  assert.equal(call(ctx, `campStatus(${JSON.stringify(c)}).label`), "実施中");
  assert.match(call(ctx, `campRange(${JSON.stringify(c)})`), /継続中/);
  assert.equal(call(ctx, `campProgress(${JSON.stringify(c)})`), null);
});

console.log("目標・メモの鍵（回ごとに分ける）");

const withKey = (over) => {
  const c = camp(over);
  c.key = `${c.id}@${c.start.slice(0, 4)}`;
  return c;
};

test("鍵は id@開始年", () => {
  const c = withKey({ id: "r1006-osusume", start: "2026-09-15", end: "2026-11-30" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  assert.equal(call(ctx, `campKey(${JSON.stringify(c)})`), "r1006-osusume@2026");
});

test("来年の同じ施策は今年の目標を引き継がない", () => {
  const y26 = withKey({ id: "r1006-osusume", start: "2026-09-15", end: "2026-11-30" });
  const y27 = withKey({ id: "r1006-osusume", start: "2027-09-15", end: "2027-11-30" });
  const ctx = loadApp({ ...base, campaigns: [y26, y27] });
  call(ctx, `API_OK = true; SERVER_TARGETS = { "r1006-osusume@2026": { value: 16000000 } };`);
  assert.equal(call(ctx, `targetOf(${JSON.stringify(y26)})`), 16000000);
  assert.equal(call(ctx, `targetOf(${JSON.stringify(y27)})`), null);
});

test("鍵が付く前に素のidで入った目標も読める（移行の橋渡し）", () => {
  const c = withKey({ id: "r1006-osusume", start: "2026-09-15", end: "2026-11-30" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; SERVER_TARGETS = { "r1006-osusume": { value: 16000000 } };`);
  assert.equal(call(ctx, `targetOf(${JSON.stringify(c)})`), 16000000);
});

test("鍵つきの値があれば、素のidの古い値より優先する", () => {
  const c = withKey({ id: "r1006-osusume", start: "2026-09-15", end: "2026-11-30" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; SERVER_TARGETS = {
    "r1006-osusume": { value: 16000000 },
    "r1006-osusume@2026": { value: 20000000 },
  };`);
  assert.equal(call(ctx, `targetOf(${JSON.stringify(c)})`), 20000000);
});

test("メモも同じ鍵で分かれる", () => {
  const y26 = withKey({ id: "r-o", start: "2026-09-15", end: "2026-11-30" });
  const y27 = withKey({ id: "r-o", start: "2027-09-15", end: "2027-11-30" });
  const ctx = loadApp({ ...base, campaigns: [y26, y27] });
  call(ctx, `API_OK = true; SERVER_NOTES = { "r-o@2026": { note: "客足が伸びた" } };`);
  assert.equal(call(ctx, `memoOf(${JSON.stringify(y26)})`), "客足が伸びた");
  assert.equal(call(ctx, `memoOf(${JSON.stringify(y27)})`), "");
});

test("台帳の target はもう読まない（目標はアプリに一本化）", () => {
  const c = withKey({ id: "x", target: 9999999 });
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; SERVER_TARGETS = {};`);
  assert.equal(call(ctx, `targetOf(${JSON.stringify(c)})`), null);
});

test("店ごとの主指標を出せる（同じ店の施策が全部同じ数字にならない）", () => {
  const data = {
    ...base,
    stores: [
      { code: "1006", name: "A店", region: "大阪", neighbors: [] },
      { code: "1015", name: "B店", region: "大阪", neighbors: [] },
    ],
    monthly: {
      1006: { "2025-01": { sales: 10000000 }, "2026-01": { sales: 8000000 } },
      1015: { "2025-01": { sales: 10000000 }, "2026-01": { sales: 8000000 } },
    },
    departments_monthly: {
      1006: { "2025-01": bucket("コース", 800000, 10000000), "2026-01": bucket("コース", 1000000, 8000000) },
      1015: { "2025-01": bucket("コース", 800000, 10000000), "2026-01": bucket("コース", 400000, 8000000) },
    },
  };
  const c = camp({ bucket: "コース", stores: ["1006", "1015"] });
  const ctx = loadApp({ ...data, campaigns: [c] });
  const a = call(ctx, `campTargeted(${JSON.stringify(c)}, "1006")`);
  const b = call(ctx, `campTargeted(${JSON.stringify(c)}, "1015")`);
  assert.equal(a.cur, 1000000);
  assert.equal(b.cur, 400000);
  assert.equal(Math.round(a.pct), 25);
  assert.equal(Math.round(b.pct), -50);
  // 店を指定しなければ2店の合計
  assert.equal(call(ctx, `campTargeted(${JSON.stringify(c)}).cur`), 1400000);
});

test("終了日未定の施策の目標は「1ヶ月あたり」。積み上がらない", () => {
  // 開始月から今月まで実績が積み上がるのに目標は1つ。そのまま割ると
  // 達成率が伸び続ける（実測 4533%）。直近確定月と比べる。
  const months = [];
  for (let y = 2025; y <= 2026; y++) for (let m = 1; m <= 12; m++)
    months.push(`${y}-${String(m).padStart(2, "0")}`);
  const dm = {}, mon = {};
  for (const m of months) { dm[m] = bucket("コース", 1000000, 8000000); mon[m] = { sales: 8000000 }; }
  const data = { ...base, months, monthly: { 1006: mon }, departments_monthly: { 1006: dm } };

  const open = camp({ id: "g1", bucket: "コース", start: "2025-04-01", end: "2025-04-01" });
  open.key = "g1@2025"; open.open_ended = true;
  const ctx = loadApp({ ...data, campaigns: [open] });
  call(ctx, `API_OK = true; SERVER_TARGETS = { "g1@2025": { value: 3000000 } };`);
  const gr = call(ctx, `campGoalRate(${JSON.stringify(open)})`);
  assert.equal(gr.monthly, true);
  assert.equal(gr.cur, 1000000, "1ヶ月ぶんであるべき");
  assert.equal(Math.round(gr.rate), 33);
  assert.ok(gr.rate < 100, `積み上がっている: ${gr.rate}%`);

  // 終了日があるものは従来どおり期間ぜんぶの合計
  const closed = camp({ id: "g2", bucket: "コース", start: "2025-04-01", end: "2025-05-31" });
  closed.key = "g2@2025";
  const ctx2 = loadApp({ ...data, campaigns: [closed] });
  call(ctx2, `API_OK = true; SERVER_TARGETS = { "g2@2025": { value: 3000000 } };`);
  const gr2 = call(ctx2, `campGoalRate(${JSON.stringify(closed)})`);
  assert.equal(gr2.monthly, false);
  assert.equal(gr2.cur, 2000000, "2ヶ月ぶんの合計であるべき");
});

console.log("原価率の前月比（costTrend）");

const crBase = (cost_rate, months) => ({
  ...base,
  months: months || ["2025-08", "2026-06", "2026-07"],
  cost_rate: { 1006: cost_rate },
});

test("隣り合った月なら前月比を出す", () => {
  const ctx = loadApp(crBase({ "2026-06": 0.30, "2026-07": 0.32 }));
  const t = call(ctx, `costTrend("1006")`);
  assert.equal(t.cur, "2026-07");
  assert.equal(t.prev, "2026-06");
  assert.ok(Math.abs(t.deltaPt - 2) < 1e-9, String(t.deltaPt));
});

test("月が飛んでいたら前月比を出さない（10ヶ月差を前月比と呼ばない）", () => {
  const ctx = loadApp(crBase({ "2025-08": 0.30, "2026-07": 0.32 }));
  assert.equal(call(ctx, `costTrend("1006")`), null);
});

test("飛んだ月では原価アラートも鳴らさない", () => {
  const ctx = loadApp(crBase({ "2025-08": 0.28, "2026-07": 0.36 }));  // 見かけ +8pt
  assert.equal(call(ctx, `costAlerts().length`), 0);
});

test("隣接していれば原価アラートは鳴る", () => {
  const ctx = loadApp(crBase({ "2026-06": 0.28, "2026-07": 0.36 }));
  assert.equal(call(ctx, `costAlerts().length`), 1);
});

console.log("横断の収益性ランキング（crossProfit）");

test("行ごとに対象月を持つ（店で直近確定月が違う）", () => {
  const data = {
    ...base,
    months: ["2026-06", "2026-07"],
    stores: [
      { code: "1006", name: "A店", region: "大阪", neighbors: [] },
      { code: "1015", name: "B店", region: "東京", neighbors: [] },
    ],
    monthly: {
      1006: { "2026-07": { sales: 1000000 } },   // 7月まで
      1015: { "2026-06": { sales: 2000000 } },   // 6月まで
    },
    covers: { 1006: { "2026-07": 200 }, 1015: { "2026-06": 500 } },
    cost_rate: { 1006: { "2026-07": 0.30 } },
  };
  const ctx = loadApp(data);
  const rows = call(ctx, `crossProfit()`);
  const by = Object.fromEntries(rows.map(r => [r.code, r]));
  assert.equal(by["1006"].ktM, "2026-07");
  assert.equal(by["1015"].ktM, "2026-06");
  // 客単価そのものは各店の対象月で計算される
  assert.equal(by["1006"].kt, 5000);
  assert.equal(by["1015"].kt, 4000);
});

// ── 制作物カード（多形式・アップロード統合）──────────────────────────────
test("制作物カード：画像はサムネ、PDFは種別バッジ、Excelは表バッジ", () => {
  const ctx = loadApp({ ...base, campaigns: [] });
  const img = call(ctx, `creativeCard(${JSON.stringify({ title: "夏POP", kind: "osusume", mime: "image/jpeg", url: "/creatives/uploads/x/a.jpg", uploaded: true, id: "z1" })})`);
  assert.ok(img.includes("<img"), "画像は img タグ");
  assert.ok(img.includes('data-crdel="z1"'), "アップロード品は削除ボタン付き");
  const pdf = call(ctx, `creativeCard(${JSON.stringify({ title: "チラシ", kind: "dev", mime: "application/pdf", url: "/creatives/y/b.pdf", stores: ["1160"] })})`);
  assert.ok(pdf.includes(">PDF<"), "PDFは種別バッジ");
  assert.ok(!pdf.includes("data-crdel"), "台帳ぶんは削除ボタン無し");
  const xls = call(ctx, `creativeCard(${JSON.stringify({ title: "原価表", kind: "dev", mime: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", url: "/creatives/z/c.xlsx", uploaded: true, id: "z2" })})`);
  assert.ok(xls.includes(">表<"), "Excelは『表』バッジ");
});

test("制作物：台帳とアップロードを統合して施策/店に出す", () => {
  const ctx = loadApp({
    ...base,
    campaigns: [{ id: "r1160-cake", stores: ["1160"], scope_all: false, title: "ケーキ", kind: "osusume", start: "2025-05-26" }],
    stores: [{ code: "1160", name: "ルクアLargo", region: "大阪", neighbors: [] }],
    creatives: [{ campaign_id: "r1160-cake", title: "5月ケーキ(台帳)", kind: "osusume", stores: ["1160"], scope_all: false, url: "/creatives/2025/x.pdf" }],
  });
  call(ctx, `UPLOADED_CREATIVES = ${JSON.stringify([{ id: "u1", campaign_id: "r1160-cake", store_code: "1160", title: "追加POP", kind: "osusume", mime: "image/png", url: "/creatives/uploads/r1160-cake/u.png", uploaded: true }])}`);
  const camp = call(ctx, `creativesForCampaign("r1160-cake").length`);
  assert.equal(camp, 2, "施策に台帳＋アップロードの2件");
  const store = call(ctx, `creativesFor("1160").length`);
  assert.ok(store >= 2, "店にも施策経由＋直付けで出る");
});

// ── 品目区分ドリル（店ページ 年間→月→区分→商品）───────────────────────────
console.log("品目区分ドリル（categories / storemonth / storecat）");

const luqa = {
  ...base,
  months: ["2024-08", "2025-08", "2026-08"],
  stores: [{ code: "1160", name: "ルクアLargo", region: "大阪", neighbors: [] }],
  monthly: { 1160: { "2025-08": { sales: 9000000 }, "2026-08": { sales: 10000000 } } },
  covers: { 1160: { "2025-08": 18000, "2026-08": 20000 } },
  store_categories: {
    1160: {
      name: "ルクアLargo", other: "その他",
      categories: [
        { name: "コラボ", keywords: ["コラボ", "くまモン", "白桃Days"] },
        { name: "ジェラート", keywords: ["ジェラート"] },
        { name: "パフェ", keywords: ["パフェ", "スノー", "サンデー"] },
        { name: "ケーキ", keywords: ["ケーキ", "フレジェ", "タルト"] },
        { name: "ドリンク", keywords: ["ラテ", "カフェ"] },
      ],
    },
  },
  categories_monthly: {
    1160: {
      "2025-08": [
        { name: "パフェ", sales: 4000000, count: 4, share: 0.5 },
        { name: "ケーキ", sales: 2000000, count: 3, share: 0.25 },
      ],
      "2026-08": [
        { name: "パフェ", sales: 5000000, count: 4, share: 0.5 },
        { name: "ケーキ", sales: 2500000, count: 3, share: 0.25 },
      ],
    },
  },
  products_monthly: {
    1160: {
      "2025-08": [
        { name: "マンゴーのパフェスノー", sales: 1400000, rank: "A" },
        { name: "苺とピスタチオのフレジェ", sales: 900000, rank: "A" },
        { name: "TOジェラートダブル", sales: 300000, rank: "A" },
        { name: "カフェラテ", sales: 150000, rank: "B" },
      ],
      "2026-08": [
        { name: "マンゴーのパフェスノー", sales: 1541280, rank: "A" },
        { name: "メロンのパフェスノー", sales: 1094800, rank: "A" },
        { name: "苺とピスタチオのフレジェ", sales: 1032300, rank: "A" },
        { name: "TOジェラートダブル", sales: 444000, rank: "A" },
        { name: "カフェラテ", sales: 170280, rank: "B" },
        { name: "謎の新商品", sales: 5000, rank: "C" },
      ],
    },
  },
};

test("classifyCat: 商品名を区分に割り当てる（先に一致した区分が勝ち）", () => {
  const ctx = loadApp(luqa);
  assert.equal(call(ctx, `classifyCat("マンゴーのパフェスノー","1160")`), "パフェ");
  assert.equal(call(ctx, `classifyCat("苺とピスタチオのフレジェ","1160")`), "ケーキ");
  assert.equal(call(ctx, `classifyCat("TOジェラートダブル","1160")`), "ジェラート");
  assert.equal(call(ctx, `classifyCat("謎の新商品","1160")`), "その他");
  // ルールの無い店は null（従来どおり部門で見る）
  assert.equal(call(ctx, `classifyCat("何か","1006")`), null);
});

test("catsAtM: 焼き込み（categories_monthly）を優先して返す", () => {
  const ctx = loadApp(luqa);
  const cats = call(ctx, `catsAtM("1160","2026-08")`);
  assert.equal(cats[0].name, "パフェ");
  assert.equal(cats[0].sales, 5000000);
});

test("catsAtM: 焼き込みが無い月は商品から都度算出する", () => {
  const noBake = { ...luqa, categories_monthly: { 1160: {} } };
  const ctx = loadApp(noBake);
  const cats = call(ctx, `catsAtM("1160","2026-08")`);
  const pafe = cats.find(c => c.name === "パフェ");
  // パフェスノー2種の合算
  assert.equal(pafe.sales, 1541280 + 1094800);
  assert.equal(pafe.count, 2);
  const other = cats.find(c => c.name === "その他");
  assert.equal(other.sales, 5000);
});

test("prodsInCat: その区分の商品だけを返す", () => {
  const ctx = loadApp(luqa);
  const ps = call(ctx, `prodsInCat("1160","2026-08","パフェ").map(p=>p.name).sort().join("|")`);
  assert.equal(ps, ["マンゴーのパフェスノー", "メロンのパフェスノー"].sort().join("|"));
});

test("bucketOrCatAtM: 部門で引けなければ区分で引く（qtyは出数でないのでnull）", () => {
  const ctx = loadApp(luqa);
  const b = call(ctx, `bucketOrCatAtM("1160","2026-08","パフェ")`);
  assert.equal(b.sales, 5000000);
  assert.equal(b.qty, null);
  assert.equal(b.viaCategory, true);
});

test("campTargeted: bucket=パフェ が区分で解決し昨対比が出る", () => {
  const c = { id: "c1160-p", stores: ["1160"], scope_all: false, title: "夏パフェ", kind: "dev", bucket: "パフェ", items: [], start: "2026-08-01", end: "2026-08-31" };
  const ctx = loadApp({ ...luqa, campaigns: [c] });
  const t = call(ctx, `campTargeted(${JSON.stringify(c)})`);
  assert.equal(t.cur, 5000000);
  assert.equal(t.prev, 4000000);
  assert.equal(Math.round(t.pct), 25);
});

test("campsInMonth: その月に走っている施策を拾う（重なりも）", () => {
  const camps = [
    { id: "a", stores: ["1160"], scope_all: false, title: "パフェスノー", kind: "dev", bucket: "パフェ", start: "2025-06-01", end: "2025-09-30" },
    { id: "b", stores: ["1160"], scope_all: false, title: "9月ケーキ", kind: "dev", bucket: "ケーキ", start: "2025-09-22", end: "2025-11-26" },
    { id: "c", stores: ["1160"], scope_all: false, title: "冬", kind: "dev", bucket: "ケーキ", start: "2025-12-01", end: "2025-12-31" },
  ];
  const ctx = loadApp({ ...luqa, campaigns: camps });
  const ids = call(ctx, `campsInMonth("1160","2025-09").map(c=>c.id).sort().join(",")`);
  assert.equal(ids, "a,b");
});

test("campPrevOccurrence: 同じ区分の前回の回を返す", () => {
  const camps = [
    { id: "snow", stores: ["1160"], scope_all: false, title: "パフェスノー", kind: "dev", bucket: "パフェ", start: "2025-06-01", end: "2025-09-30" },
    { id: "lychee", stores: ["1160"], scope_all: false, title: "ライチ", kind: "dev", bucket: "パフェ", start: "2025-05-08", end: "2025-05-31" },
    { id: "cake", stores: ["1160"], scope_all: false, title: "ケーキ", kind: "dev", bucket: "ケーキ", start: "2025-05-26", end: "2025-09-21" },
  ];
  const ctx = loadApp({ ...luqa, campaigns: camps });
  assert.equal(call(ctx, `campPrevOccurrence(${JSON.stringify(camps[0])}).id`), "lychee");
});

// ── 年間スケジュール（チャート/カレンダー切替・年選択・販促クリック）─────────────
console.log("年間スケジュール（storeAnnual）");

const annualData = {
  ...luqa,
  monthly: { 1160: { "2025-08": { sales: 9000000 }, "2025-09": { sales: 8500000 }, "2026-08": { sales: 10000000 } } },
  covers: { 1160: { "2025-08": 18000, "2025-09": 17000, "2026-08": 20000 } },
  campaigns: [
    { id: "snow", stores: ["1160"], scope_all: false, title: "パフェスノー", kind: "dev", bucket: "パフェ", start: "2025-06-01", end: "2025-09-30" },
    { id: "cake9", stores: ["1160"], scope_all: false, title: "9月ケーキ", kind: "dev", bucket: "ケーキ", start: "2025-09-22", end: "2025-11-26" },
  ],
};

test("既定は月次一覧（スクロールで月ごとに読める）で、両方の切替がある", () => {
  const ctx = loadApp(annualData);
  // 既定（グローバル初期値）のまま：明示セットしない
  const html = call(ctx, `storeAnnual("1160")`);
  assert.ok(html.includes('class="vtab on" data-savw="chart"'), "既定は月次一覧がon");
  assert.ok(html.includes('data-savw="chart"') && html.includes('data-savw="calendar"'), "両方の切替がある");
  assert.ok(html.includes("年間スケジュール"));
  assert.ok(/一覧（縦＝指標／横＝月）/.test(html), "年間×月の一覧が最初から出る");
});

test("チャート：販促が帯（data-camp）で出て、月見出しは data-smonth", () => {
  const ctx = loadApp(annualData);
  const html = call(ctx, `STORE_YEAR="2025"; storeAnnualChart("1160","2025")`);
  assert.ok(html.includes('data-camp="snow"'), "パフェスノーの帯");
  assert.ok(html.includes('data-camp="cake9"'), "9月ケーキの帯");
  assert.ok(html.includes('data-smonth="1160:2025-09"'), "月見出しから月ドリル");
});

test("カレンダー一覧：月は開閉式（既定は畳んで data-mtoggle）", () => {
  const ctx = loadApp(annualData);
  call(ctx, `ANNUAL_OPEN = {};`);
  const html = call(ctx, `storeAnnualCalendar("1160","2025")`);
  assert.ok(html.includes('data-mtoggle="1160:2025-09"'), "月の開閉トグル");
  assert.ok(html.includes("予算") && html.includes("客単価"), "予算・客単価の見出し");
  assert.ok(!html.includes('data-camp="snow"'), "畳んでいる間は販促チップを出さない");
});

test("カレンダー一覧：月を開くと販促チップ（data-camp）と詳細リンクが出る", () => {
  const ctx = loadApp(annualData);
  call(ctx, `ANNUAL_OPEN = {"1160:2025-09": true};`);
  const html = call(ctx, `storeAnnualCalendar("1160","2025")`);
  assert.ok(html.includes('data-camp="snow"'), "パフェスノー");
  assert.ok(html.includes('data-camp="cake9"'), "9月ケーキ");
  assert.ok(html.includes('data-smonth="1160:2025-09"'), "この月の詳細→");
});

test("カレンダー一覧：区分データのある月を開くと区分トグルが出る", () => {
  const ctx = loadApp(annualData);
  call(ctx, `ANNUAL_OPEN = {"1160:2026-08": true};`);
  const html = call(ctx, `storeAnnualCalendar("1160","2026")`);
  assert.ok(html.includes('data-cattoggle="1160:2026-08:'), "区分の開閉トグル");
  // さらにパフェ区分を開くと商品が並ぶ
  call(ctx, `ANNUAL_OPEN = {"1160:2026-08": true, "1160:2026-08:パフェ": true};`);
  const html2 = call(ctx, `storeAnnualCalendar("1160","2026")`);
  assert.ok(html2.includes("マンゴーのパフェスノー"), "パフェの商品一覧が展開");
});

test("月ナビ：前月/次月と月ピッカーが出る", () => {
  const ctx = loadApp(annualData);
  call(ctx, `MONTH_PICK_OPEN = false;`);
  const nav = call(ctx, `storeMonthNav("1160","2026-08")`);
  assert.ok(nav.includes('data-smonth="1160:2025-09"'), "前月（実績のある直前の月）へ");
  assert.ok(nav.includes("data-mpick"), "月ピッカーのトグル");
  call(ctx, `MONTH_PICK_OPEN = true;`);
  const nav2 = call(ctx, `storeMonthNav("1160","2026-08")`);
  assert.ok(nav2.includes("mpick"), "ピッカー展開");
});

test("年タブが選べる（実績年＋販促年）", () => {
  const ctx = loadApp(annualData);
  const html = call(ctx, `storeAnnual("1160")`);
  assert.ok(html.includes('data-syear="2025"'));
  assert.ok(html.includes('data-syear="2026"'));
});

// ── 手動ステータス（保留/中止/今季なし/完了）─────────────────────────────
console.log("手動ステータス（manualStatusOf / statusControl）");

test("手動ステータス：サーバ値を回ごとに拾う", () => {
  const c = camp({ id: "c1", start: "2026-01-01" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; SERVER_STATUS = { "c1@2026": { value: "保留" } };`);
  assert.equal(call(ctx, `manualStatusOf(${JSON.stringify({ ...c, key: "c1@2026" })})`), "保留");
});

test("手動ステータス：無ければ null（自動判定を使う）", () => {
  const c = camp({ id: "c2", start: "2026-01-01" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; SERVER_STATUS = {};`);
  assert.equal(call(ctx, `manualStatusOf(${JSON.stringify({ ...c, key: "c2@2026" })})`), null);
});

test("statusControl：手動があれば手動バッジ＋自動を小さく、編集ボタンはWRITE_OK時のみ", () => {
  const c = camp({ id: "c3", start: "2026-01-01" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; WRITE_OK = true; SERVER_STATUS = { "c3@2026": { value: "中止" } };`);
  const html = call(ctx, `statusControl(${JSON.stringify({ ...c, key: "c3@2026" })}, "実施中", "live")`);
  assert.ok(html.includes("cstat man") && html.includes("中止"), "手動バッジ");
  assert.ok(html.includes("実施中"), "自動も添える");
  assert.ok(html.includes('data-status="c3@2026"'), "編集ボタン");
  // 閲覧専用では編集ボタンを出さない
  call(ctx, `WRITE_OK = false;`);
  const ro = call(ctx, `statusControl(${JSON.stringify({ ...c, key: "c3@2026" })}, "実施中", "live")`);
  assert.ok(!ro.includes("data-status"), "閲覧専用では編集不可");
});

// ── 店長ダッシュボード（storeHero）─────────────────────────────────────────
console.log("店長ダッシュボード（storeHero）");

test("storeHero：予算があれば達成率、無ければ直近売上を主に出す", () => {
  const withBud = { ...annualData, budget: { 1160: { "2026-08": 12000000 } } };
  let ctx = loadApp(withBud);
  let html = call(ctx, `storeHero("1160")`);
  assert.ok(html.includes("予算達成率"), "予算があれば達成率");
  // 予算が無い店はフォールバック（直近売上＋予算未登録）
  ctx = loadApp(annualData);
  html = call(ctx, `storeHero("1160")`);
  assert.ok(html.includes("直近売上") && html.includes("予算未登録"), "予算無しは売上＋未登録");
});

test("storeHero：要対応（POP未登録など）を出し、主役販促は data-camp", () => {
  const ctx = loadApp(annualData);
  const html = call(ctx, `storeHero("1160")`);
  assert.ok(html.includes("要対応"));
  assert.ok(html.includes("主役の販促"));
});

// ── 販促エンジンビュー（storeEngines）─────────────────────────────────────
console.log("販促エンジンビュー（storeEngines）");

test("storeEngines：bucket指定のある区分を主役として、バー＋回ごと（data-camp）を出す", () => {
  const ctx = loadApp(annualData);  // snow=パフェ, cake9=ケーキ
  const html = call(ctx, `storeEngines("1160")`);
  assert.ok(html.includes("販促エンジン"), "見出し");
  assert.ok(html.includes("data-camp=\"snow\"") || html.includes("data-camp=\"cake9\""), "回ごとが施策リンク");
  assert.ok(html.includes("data-scat=\"1160:2026-08:"), "バーは商品ドリルへ");
});

test("storeEngines：区分ルールの無い店では出さない", () => {
  const ctx = loadApp(base);
  assert.equal(call(ctx, `storeEngines("1006")`), "");
});

// ── 店舗ページ全体（renderStore）の健全性 ────────────────────────────────
console.log("店舗ページ全体（renderStore）");

test("renderStore：目標対象外の実施中販促でも literal 'undefined' を出さない", () => {
  // 実施中・目標対象外（osusume は測り方も未設定）＝ goalHtml 分岐に入らない。
  // 以前は goalHtml が undefined のまま文字列で描画されていた。
  const c = camp({ id: "live1", kind: "osusume", title: "秋おすすめ",
    start: "2026-09-01", end: "2026-09-30" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(!/\bundefined\b/.test(html), "renderStore に undefined が混じらない");
  assert.ok(html.includes("この店の販促"), "販促セクションはある");
});

test("renderStore：先頭サマリはヒーローに統合され、基礎データは折りたたみに入る", () => {
  const ctx = loadApp(base);
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("店の基礎データを見る"), "基礎データは details に格納");
  assert.ok(html.includes("class=\"hnote\""), "ヒーローにデータ鮮度の一言");
});

test("storeProductSearch：商品横断検索（商品名→月・区分・売上）が出る", () => {
  const ctx = loadApp(luqa);
  const html = call(ctx, `storeProductSearch("1160")`);
  assert.ok(html.includes("商品を探す") && html.includes('data-prodsearch="1160"'), "検索ボックス");
  assert.ok(html.includes("マンゴーのパフェスノー") && html.includes('data-pn='), "商品行＋絞り込み用データ");
  assert.ok(html.includes('data-scat="1160:'), "商品行から月×区分の詳細へ");
});

test("storeProductSearch：商品データが無ければ出さない", () => {
  const ctx = loadApp({ ...base, products_monthly: {} });
  assert.equal(call(ctx, `storeProductSearch("1006")`), "");
});

test("販促プラン：applyPlans で計画が DATA.campaigns に統合される", () => {
  const c = camp({ id: "real", stores: ["1160"], bucket: "パフェ", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...luqa, campaigns: [c] });
  call(ctx, `PLANS=[{id:"plan-x",store_code:"1160",title:"秋パフェ計画",kind:"osusume",bucket:"パフェ",start:"2026-09-01",end:"2026-10-31",goal:1500000,note:"狙い",by:"me"}]; BASE_CAMPAIGNS=null; applyPlans();`);
  assert.equal(call(ctx, `DATA.campaigns.length`), 2);
  assert.equal(call(ctx, `DATA.campaigns.find(c=>c.id==="plan-x").planned`), true);
});

test("販促プラン：起票ボタン・計画バッジ・複製/編集が販促リストに出る（本番のみ）", () => {
  const c = camp({ id: "real", stores: ["1160"], bucket: "パフェ", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...luqa, campaigns: [c] });
  const html = call(ctx, `PLANS_API_OK=true; WRITE_OK=true; PLANS=[{id:"plan-x",store_code:"1160",title:"秋パフェ計画",kind:"osusume",bucket:"パフェ",start:"2026-09-01",end:"2026-10-31",goal:1500000,note:"狙い",by:"me"}]; BASE_CAMPAIGNS=null; applyPlans(); renderStore("1160")`);
  assert.ok(html.includes('data-plannew="1160"'), "＋販促を起票ボタン");
  assert.ok(html.includes("秋パフェ計画") && html.includes("plbadge"), "計画がバッジ付きで並ぶ");
  assert.ok(html.includes('data-planedit="plan-x"'), "計画は編集ボタン");
  assert.ok(html.includes('data-plandup="real"'), "確定販促は複製ボタン");
});

test("販促プラン：閲覧専用（WRITE_OK=false）では起票・複製ボタンを出さない", () => {
  const c = camp({ id: "real", stores: ["1160"], bucket: "パフェ", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...luqa, campaigns: [c] });
  const html = call(ctx, `PLANS_API_OK=true; WRITE_OK=false; renderStore("1160")`);
  assert.ok(!html.includes("data-plannew") && !html.includes("data-plandup"), "閲覧専用では書き込み導線を出さない");
});

test("renderStore：今月の共有カード（会議/LINE用・コピー用テキスト）が出る", () => {
  const c = camp({ bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  const withBud = { ...base, budget: { 1006: { "2026-01": 10000000 } },
    covers: { 1006: { "2026-01": 3000 } }, campaigns: [c] };
  const ctx = loadApp(withBud);
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("今月の共有カード"), "共有カードの見出し");
  assert.ok(html.includes("予算達成") && html.includes("sharecard"), "1枚カード");
  assert.ok(html.includes('data-sharecopy="sharetext-1006"') && html.includes('id="sharetext-1006"'), "コピー用テキスト＋ボタン");
});

test("renderStore：販促カードに前回（同じ枠）の学び＝要因メモ/次回提案を引き継ぎ表示", () => {
  const prev = camp({ id: "old", bucket: "コース", title: "去年の夏コース",
    start: "2025-01-01", end: "2025-01-31", memo: "去年は品切れで機会損失。仕込み増やす。" });
  const cur = camp({ id: "new", bucket: "コース", title: "今年の夏コース", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [prev, cur], proposals: { old: { next: "開始1週間前にPOP掲出。" } } });
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("前回「去年の夏コース」の学び"), "前回の学びの見出し");
  assert.ok(html.includes("仕込み増やす") && html.includes("開始1週間前にPOP"), "前回メモ＋次回提案を引き継ぐ");
});

test("renderStore：終了して未記入の販促は『振り返り未記入』を強調する", () => {
  const c = camp({ id: "d1", bucket: "コース", title: "終わった企画", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [c] });   // today=2026-09-06 → done、memo/proposal 無し
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("振り返り未記入"), "やりっぱなしを強調");
});

test("renderStore：販促カードに その販促のPOP（制作物）が結果と並んで出る", () => {
  const c = camp({ id: "p1", bucket: "コース", title: "夏コース", start: "2026-01-01", end: "2026-01-31" });
  const withCr = { ...base, campaigns: [c],
    creatives: [{ id: "cr1", campaign_id: "p1", store_code: "1006", title: "夏POP", mime: "image/png", url: "x.png" }] };
  const ctx = loadApp(withCr);
  const html = call(ctx, `CREATIVES_API_OK=true; renderStore("1006")`);
  assert.ok(html.includes("POP・資料") && html.includes("夏POP"), "販促カードにPOPが束ねて出る");
});

test("renderStore：縦長対策のジャンプナビ（各セクションへ飛べる）が出る", () => {
  const c = camp({ bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes('class="snav"'), "ジャンプナビ本体");
  assert.ok(html.includes('data-jump="hero"'), "今の状況へ飛べる");
  assert.ok(html.includes('data-jump="promos"'), "販促リストへ飛べる");
  assert.ok(html.includes('data-jump="basics"'), "基礎データへ飛べる");
  assert.ok(html.includes('id="hero"') && html.includes('id="promos"') && html.includes('id="basics"'),
    "飛び先のアンカーIDがある");
});

// ── 販促の効果まとめ・並べ替え ────────────────────────────────────────────
console.log("販促の効果まとめ（storeCampEffect / renderStore）");

test("storeCampEffect：bucket指定の販促は対象区分の前年比で ◎/△ を返す", () => {
  const c = camp({ bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const e = call(ctx, `storeCampEffect(${JSON.stringify(c)}, "1006")`);
  assert.equal(e.measured, true);
  assert.equal(e.mark, "◎");
  assert.equal(Math.round(e.pct), 25);
});

test("storeCampEffect：測り方未設定の販促は measured=false で理由を返す", () => {
  const c = camp({ kind: "osusume", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const e = call(ctx, `storeCampEffect(${JSON.stringify(c)}, "1006")`);
  assert.equal(e.measured, false);
  assert.equal(e.state, "測り方 未設定");
});

test("renderStore：この店の販促に効果サマリ（◎効いた）と並べ替え・判定バッジが出る", () => {
  const c = camp({ bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("効いた"), "効果サマリ");
  assert.ok(html.includes('data-psort="effect"'), "効果順の並べ替え");
  assert.ok(html.includes('data-pfilter="live"'), "状態タブ");
  assert.ok(html.includes("cvm good"), "◎判定バッジ");
});

test("renderStore：前回比が一覧に併記される（同じ枠の前回の回と比較）", () => {
  const prev = camp({ id: "p", bucket: "コース", start: "2025-01-01", end: "2025-01-31" });
  const curr = camp({ id: "c2", bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [prev, curr] });
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("前回比"), "前回比が一覧に出る");
});

test("storeAnnualChart：年サマリ＋月次の推移（一覧・1行=1ヶ月）が頭に出る", () => {
  const c = camp({ bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const html = call(ctx, `storeAnnualChart("1006","2026")`);
  assert.ok(html.includes("販促の効き"), "年サマリ");
  assert.ok(/一覧（縦＝指標／横＝月）/.test(html), "年間×月マトリクスの見出し");
  assert.ok(html.includes('class="ymxt"'), "マトリクス表");
  assert.ok(html.includes("前年比") && html.includes("予算") && html.includes("年計"), "指標行＋年計列");
});

test("storeYearMatrix：縦＝指標・横＝月・右端に年計。月見出しから月詳細へ", () => {
  const c = camp({ bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  const withBud = { ...base, budget: { 1006: { "2026-01": 10000000 } }, campaigns: [c] };
  const ctx = loadApp(withBud);
  const html = call(ctx, `storeYearMatrix("1006","2026")`);
  assert.ok(html.includes('data-smonth="1006:2026-01"'), "列見出しを押すと月の詳細へ");
  assert.ok(html.includes("ymxsum") && html.includes("年計"), "年計列");
  assert.ok(/予算/.test(html) && /客単価/.test(html), "予算・客単価の指標行");
});

test("storeYearMatrix：品目構成比を『区分×月』の行で一覧に出す（月ごとに読める）", () => {
  const ctx = loadApp(annualData);
  const html = call(ctx, `storeYearMatrix("1160","2025")`);
  assert.ok(!html.includes("ymxvbar"), "小さくて見にくい構成比の縦バー行は撤去");
  assert.ok(!/<th>販促<\/th>/.test(html), "販促の行はマトリクスに持たない（年間チャートへ）");
  assert.ok(html.includes("ymxsec") && /品目構成比/.test(html), "品目構成比のセクション見出しがある");
  assert.ok(html.includes("ymxcompo-row") && html.includes("パフェ"), "区分ごとの行（例：パフェ）が月別で並ぶ");
  assert.ok(html.includes("ymxcdot"), "区分は色ドット付きで示す（チャートと同色）");
});

test("storeAnnualChart：販促は年間チャート（帯）として見出し付きで出す", () => {
  const ctx = loadApp(annualData);
  const html = call(ctx, `storeAnnualChart("1160","2025")`);
  assert.ok(html.includes("販促 年間チャート"), "販促の年間チャート見出し");
  assert.ok(html.includes('class="panel gantt"'), "帯（ガント）で表示");
  assert.ok(html.includes('data-camp="snow"'), "販促の帯");
  // 帯はネイティブ title ではなく、暗色ツールチップ用の data-tip（｜区切り）を持つ
  assert.ok(/class="gbar[^"]*"[^>]*data-tip="[^"]*｜/.test(html), "帯に構造化ツールチップ(data-tip)");
});

test("renderStoreMonth：予算があれば売上カードに予算比ピル＋予算サブが出る（Steppy風）", () => {
  const withBud = { ...annualData, budget: { 1160: { "2025-08": 8000000 } } };
  const ctx = loadApp(withBud);
  const html = call(ctx, `renderStoreMonth("1160","2025-08")`);
  assert.ok(html.includes("kpill") && html.includes("予算比"), "予算比ピル");
  assert.ok(/予算 [\d,]+万?円/.test(html), "予算金額のサブ表示");
});

test("renderStoreMonth：予算が無ければ予算KPIカードは出さない（代わりに入力ナビ）", () => {
  const ctx = loadApp(annualData);   // 1160 に budget 無し
  const html = call(ctx, `renderStoreMonth("1160","2025-08")`);
  assert.ok(!html.includes('<div class="lbl">予算達成率</div>'), "予算未登録なら予算達成率KPIカードは省く");
  assert.ok(html.includes("予算未入力"), "代わりに予算未入力の入力ナビを出す");
});

test("renderStoreMonth：前年差の内訳（区分ごとの前年差を大きい順に）が出る", () => {
  const ctx = loadApp(luqa);   // 2026-08 vs 2025-08 は両方に区分あり
  const html = call(ctx, `renderStoreMonth("1160","2026-08")`);
  assert.ok(html.includes("前年差の内訳"), "内訳の見出し");
  assert.ok(html.includes("ybd-list") && html.includes("パフェ"), "区分別の差の行");
  assert.ok(/[+＋]\d/.test(html) || html.includes("＋"), "増減の符号つき差額");
});

test("renderStoreMonth：前年実績が無い月は内訳を出さない", () => {
  const noPrev = { ...luqa, monthly: { 1160: { "2025-08": { sales: 9000000 } } } };  // 2024-08 の売上なし
  const ctx = loadApp(noPrev);
  const html = call(ctx, `renderStoreMonth("1160","2025-08")`);
  assert.ok(!html.includes("前年差の内訳"), "前年が無ければ内訳は省く");
});

test("renderStoreMonth：確定月で予算が無ければ『予算未入力』の入力ナビを出す", () => {
  const ctx = loadApp(luqa);   // 1160 2025-08 は売上あり・予算なし（確定月）
  const html = call(ctx, `renderStoreMonth("1160","2025-08")`);
  assert.ok(html.includes("予算未入力"), "予算未入力ナビ");
});

test("storeYearMatrix：予算未入力の確定月があれば一覧にヒントを出す", () => {
  const ctx = loadApp(luqa);   // 予算 budget:{} のまま
  const html = call(ctx, `storeYearMatrix("1160","2025")`);
  assert.ok(html.includes("予算未入力の月あり"), "予算未入力ヒント");
});

test("renderStoreMonth：品目区分は data-cattoggle でその場開閉（モバイルもページ移動なし）", () => {
  const ctx = loadApp(luqa);
  const closed = call(ctx, `ANNUAL_OPEN = {}; renderStoreMonth("1160","2025-08")`);
  assert.ok(closed.includes("data-cattoggle=") && !closed.includes('class="catgo"'), "区分はその場トグル（別ページ遷移の商品→は無し）");
  const opened = call(ctx, `ANNUAL_OPEN = {"1160:2025-08:パフェ": true}; renderStoreMonth("1160","2025-08")`);
  assert.ok(opened.includes("mprods"), "開くと下に商品リストが出る");
});

// ── 一覧画面の管理しやすさ（店舗一覧の要注意・並べ替え／全店予算タイル）──────
console.log("一覧画面（renderList / reviewProgressStrip）");

test("renderList：要注意サマリ（前年割れ）と並べ替えコントロールが出る", () => {
  const ctx = loadApp(base);   // 1006 は 2026-01 が前年割れ
  const html = call(ctx, `renderList()`);
  assert.ok(html.includes("前年割れ"), "前年割れサマリ");
  assert.ok(html.includes('data-listsort="budget"'), "並べ替え（予算達成順）");
  assert.ok(html.includes('data-listsort="yoy"'), "並べ替え（前年比順）");
});

test("reviewProgressStrip：全店 予算達成タイルを出す（予算があるとき）", () => {
  const withBud = { ...base, budget: { 1006: { "2026-01": 10000000 } },
    campaigns: [camp({ bucket: "コース", start: "2026-01-01", end: "2026-01-31" })] };
  const ctx = loadApp(withBud);
  const html = call(ctx, `reviewProgressStrip()`);
  assert.ok(html.includes("全店 予算達成"), "予算達成タイル");
  assert.ok(html.includes('data-listsort="budget"'), "予算達成順で店舗一覧へ");
});

console.log(failed ? `\n${failed} 件失敗` : "\nすべて通過");
process.exit(failed ? 1 : 0);
