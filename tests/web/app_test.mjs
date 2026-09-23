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

console.log("達成率の5段階評価（achieveGrade）");

test("◎超優秀110%↑ / 〇優秀100%↑ / △改善90%↑ / ×要改善80%↑ / ××大幅未達80%未満", () => {
  const ctx = loadApp({ ...base });
  const g = r => call(ctx, `achieveGrade(${r})`);
  assert.deepEqual([g(130).mark, g(130).label], ["◎", "超優秀"]);
  assert.deepEqual([g(110).mark, g(105).mark], ["◎", "〇"]);
  assert.deepEqual([g(100).mark, g(100).label], ["〇", "優秀"]);
  assert.deepEqual([g(99).mark, g(90).mark], ["△", "△"]);
  assert.deepEqual([g(89).mark, g(80).mark], ["×", "×"]);
  assert.deepEqual([g(79).mark, g(79).label], ["××", "大幅未達"]);
  assert.equal(g(null), null);
});

console.log("販促の達成状況・日割りペース見込み（campPace）");

// 6ヶ月の実施中販促。確定2ヶ月ぶんの実績を期末まで引き伸ばして見込みを出す。
const paceData = () => ({
  ...base,
  departments_monthly: { 1006: {
    "2026-07": bucket("コース", 600000, 5000000),
    "2026-08": bucket("コース", 600000, 5000000),
  } },
});
const paceCamp = () => {
  const c = camp({ id: "cp", bucket: "コース", start: "2026-07-01", end: "2026-12-31" });
  c.key = "cp@2026";
  return c;
};

test("現時点は確定分の達成率、見込みは日割りペースで期末まで引き伸ばす", () => {
  const c = paceCamp();
  const ctx = loadApp({ ...paceData(), campaigns: [c] }, "2026-09-06");
  call(ctx, `API_OK = true; SERVER_TARGETS = { "cp@2026": { value: 3000000 } };`);
  const p = call(ctx, `campPace(${JSON.stringify(c)})`);
  assert.equal(Math.round(p.nowRate), 40);           // 120万 / 300万
  assert.equal(p.doneMonths, 2);
  assert.equal(p.totalMonths, 6);
  assert.equal(Math.round(p.projRate), 120);         // 120万/2*6=360万 → 300万比 120%
  // 現時点は××（大幅未達）だが、ペース見込みは◎（超優秀）
  assert.equal(call(ctx, `achieveGrade(${p.nowRate}).mark`), "××");
  assert.equal(call(ctx, `achieveGrade(${p.projRate}).mark`), "◎");
});

test("終了した販促は最終の達成率のみ（見込みは出さない）", () => {
  const c = camp({ id: "cd", bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  c.key = "cd@2026";
  const ctx = loadApp({ ...base, campaigns: [c] });
  call(ctx, `API_OK = true; SERVER_TARGETS = { "cd@2026": { value: 1000000 } };`);
  const p = call(ctx, `campPace(${JSON.stringify(c)})`);
  assert.equal(Math.round(p.nowRate), 100);
  assert.equal(p.projRate, null);
});

console.log("販促ページの構成（renderCampaign）");

// 目標運用は2026年10月分から。達成バッジが出るのは目標対象（＝10月以降まで続く）販促。
// 実施中・確定2ヶ月の販促で、目標120万に対しコース計120万＝現時点100%。
const eligCamp = () => {
  const c = camp({ id: "cx", bucket: "コース", start: "2026-07-01", end: "2026-12-31" });
  c.key = "cx@2026";
  return c;
};
const renderElig = (extra = "") => {
  const c = eligCamp();
  const ctx = loadApp({ ...paceData(), campaigns: [c] }, "2026-09-06");
  call(ctx, `API_OK = true; ${extra} SERVER_TARGETS = { "cx@2026": { value: 1200000 } };`);
  return { ctx, html: call(ctx, `renderCampaign(${JSON.stringify("cx")})`) };
};

test("POP・制作物が達成サマリーより上（先頭）に出る", () => {
  const { html } = renderElig("CREATIVES_API_OK = true;");
  const iPop = html.indexOf("POP・制作物");
  const iAch = html.indexOf("達成サマリー");
  assert.ok(iPop >= 0 && iAch >= 0, "両セクションが出る");
  assert.ok(iPop < iAch, "POPが達成サマリーより先");
});

test("5段階の達成バッジと達成率が達成サマリーに出る", () => {
  const { html } = renderElig();
  assert.match(html, /ach-rate/);
  assert.match(html, /〇|◎|△|×/);
  assert.match(html, /100<span class="u">%/);   // コース計120万/目標120万＝現時点100%
});

test("実施中は日割りペースの見込み達成率も出す", () => {
  const { html } = renderElig();
  assert.match(html, /見込み達成率/);
  assert.match(html, /日割りペース概算/);
});

test("年間の部門推移（関連部門の実績・月次）は販促ページに出さない", () => {
  const { html } = renderElig();
  assert.ok(!/関連部門の実績/.test(html), "年間の部門推移カードは販促ページから外す");
});

test("売上目標だけのときは『目標の振り返り』表を出さない（達成サマリーが持つ）", () => {
  // 目標の振り返りに売上を出すと店全体売上÷目標で達成率が跳ねる（例331%）。売上は載せない。
  const { html } = renderElig();
  assert.ok(!/目標の振り返り/.test(html), "売上目標のみなら振り返り表は出さない");
});

test("客数など売上以外の目標があれば『目標の振り返り』を出す（売上行は無し）", () => {
  const c = eligCamp();
  const ctx = loadApp({ ...paceData(), campaigns: [c] }, "2026-09-06");
  call(ctx, `API_OK = true; SERVER_TARGETS = { "cx@2026": { value: 1200000 } };`);
  call(ctx, `SERVER_TARGETS_M = { "cx@2026": { covers: { value: 3000 } } };`);
  const html = call(ctx, `renderCampaign(${JSON.stringify("cx")})`);
  assert.match(html, /目標の振り返り/);
  assert.match(html, /客数/);
  const tbl = html.split("目標の振り返り")[1] || "";
  assert.ok(!/売上目標/.test(tbl), "振り返り表に売上行は出さない");
});

test("1店だけの販促は『対象店ごとの結果』を出さない（結果（全体）と重複）", () => {
  const { html } = renderElig();
  assert.ok(!/対象店ごとの結果/.test(html), "単店は重複するので出さない");
});

test("新商品で商品単位の前年比が無いとき、部門の前年比にフォールバックして見せる", () => {
  const bkt = (name, sales, total) => ({ total_sales: total,
    buckets: [{ name, sales, qty: 100, share: sales / total, cost_rate: 30 }], raw: [] });
  const c = { id: "sp", key: "sp@2026", stores: ["1160"], scope_all: false, title: "スノー",
    kind: "parfait", start: "2026-07-01", end: "2026-12-31", note: "", bucket: "パフェ", items: ["スノー"] };
  const data = { ...base,
    months: ["2025-07", "2025-08", "2026-07", "2026-08"],
    stores: [{ code: "1160", name: "ルクアLargo", region: "大阪", neighbors: [] }],
    monthly: { 1160: { "2025-07": { sales: 9000000 }, "2025-08": { sales: 11000000 },
      "2026-07": { sales: 10000000 }, "2026-08": { sales: 13000000 } } },
    departments_monthly: { 1160: {
      "2025-07": bkt("パフェ", 1600000, 9000000), "2025-08": bkt("パフェ", 3200000, 11000000),
      "2026-07": bkt("パフェ", 2000000, 10000000), "2026-08": bkt("パフェ", 3800000, 13000000) } },
    products_monthly: { 1160: {
      "2026-07": [{ name: "スノーパフェ", sales: 2000000, qty: 1300 }],
      "2026-08": [{ name: "スノーパフェ", sales: 3800000, qty: 2400 }] } },
  };
  const ctx = loadApp({ ...data, campaigns: [c] }, "2026-09-06");
  call(ctx, `API_OK = true; SERVER_TARGETS = { "sp@2026": { value: 7100000 } };`);
  const html = call(ctx, `renderCampaign("sp")`);
  assert.match(html, /部門は前年比/);
  assert.ok(!/前年のABCなし/.test(html), "部門フォールバックが出れば『前年のABCなし』にはしない");
});

test("この販促の部門別内訳（campDeptMix）は販売期間の実データを品目区分で束ねる", () => {
  const c = camp({ id: "cm", start: "2026-03-01", end: "2026-03-31" });
  const data = { ...base,
    store_categories: { 1006: { name: "t", other: "その他",
      categories: [{ name: "ケーキ", keywords: ["ケーキ"] }, { name: "ドリンク", keywords: ["ティー", "ラテ"] }] } },
    campaign_actuals: { cm: { sales: 300000, qty: 30, items: [
      { name: "いちごショートケーキ", sales: 200000, qty: 20 },
      { name: "ダージリンティー", sales: 100000, qty: 10 },
    ] } },
  };
  const ctx = loadApp({ ...data, campaigns: [c] });
  const html = call(ctx, `campDeptMix(${JSON.stringify(c)})`);
  assert.match(html, /部門別の内訳（この販促）/);
  assert.match(html, /ケーキ/);
  assert.match(html, /ドリンク/);
});

test("部門別の内訳があるとき、販売時期の実績（商品一覧）は折りたたむ", () => {
  const c = camp({ id: "cm", start: "2026-03-01", end: "2026-03-31" });
  const data = { ...base,
    store_categories: { 1006: { name: "t", other: "その他",
      categories: [{ name: "ケーキ", keywords: ["ケーキ"] }, { name: "ドリンク", keywords: ["ティー"] }] } },
    campaign_actuals: { cm: { sales: 300000, qty: 30, items: [
      { name: "いちごショートケーキ", sales: 200000, qty: 20 },
      { name: "ダージリンティー", sales: 100000, qty: 10 },
    ] } },
  };
  const ctx = loadApp({ ...data, campaigns: [c] });
  const html = call(ctx, `renderCampaign("cm")`);
  assert.match(html, /部門別の内訳（この販促）/);   // 要約は常時表示
  assert.match(html, /販売時期の実績/);
  assert.match(html, /<details[^>]*><summary>商品ごとの内訳を見る/);  // 商品一覧は折りたたみ
});

test("部門別の内訳が無い（1区分）ときは、商品一覧は折りたたまず開いて出す", () => {
  const c = camp({ id: "ck", start: "2026-03-01", end: "2026-03-31" });
  const data = { ...base,
    store_categories: { 1006: { name: "t", other: "その他",
      categories: [{ name: "ケーキ", keywords: ["ケーキ"] }] } },
    campaign_actuals: { ck: { sales: 300000, qty: 30, items: [
      { name: "いちごショートケーキ", sales: 200000, qty: 20 },
      { name: "モンブランケーキ", sales: 100000, qty: 10 },
    ] } },
  };
  const ctx = loadApp({ ...data, campaigns: [c] });
  const html = call(ctx, `renderCampaign("ck")`);
  assert.ok(!/部門別の内訳/.test(html), "1区分なら部門内訳は出さない");
  assert.match(html, /販売時期の実績/);
  assert.ok(!/商品ごとの内訳を見る/.test(html), "要約が無いので商品一覧は開いたまま");
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

// ── 実原価（棚卸・仕入から。理論原価率とは別物）──────────────────────────
console.log("実原価（actual_cost）");

// 1069 は理論原価率が無い（FWのABC部門が紐付いていない）店。実原価だけがある。
// 1006 は両方ある店。差（不明ロス）が出せる。
const acBase = () => ({
  ...base,
  months: ["2026-06", "2026-07"],
  stores: [
    { code: "1006", name: "A店", region: "大阪", neighbors: [] },
    { code: "1069", name: "ひよこ飯店", region: "大阪", neighbors: [] },
  ],
  monthly: {
    1006: { "2026-07": { sales: 10000000 } },
    1069: { "2026-07": { sales: 5000000 } },
  },
  covers: { 1006: { "2026-07": 2000 }, 1069: { "2026-07": 1000 } },
  cost_rate: { 1006: { "2026-07": 0.30 } },          // 1069 には無い
  actual_cost: {
    1006: { "2026-07": { food: 2400000, drink: 800000, total: 3200000, rate: 0.32 } },
    1069: { "2026-07": { food: 1500000, drink: 500000, total: 2000000, rate: 0.40 } },
  },
  departments_monthly: {}, products_monthly: {},
});

test("actual_cost が無い画面でも落ちない（キーごと欠けた古いJSON）", () => {
  const ctx = loadApp(base);                      // base に actual_cost は無い
  assert.equal(call(ctx, `acRateAt("1006","2026-01")`), undefined);
  assert.equal(call(ctx, `latestActualCost("1006")`), null);
});

test("実原価率は指標として選べる（率なので期間合計にしない）", () => {
  const ctx = loadApp(acBase());
  assert.equal(call(ctx, `isRatioMetric("actual_cost_rate")`), true);
  assert.equal(call(ctx, `METRIC = "actual_cost_rate"; valueAt("1069","2026-07")`), 0.40);
  // 理論原価率とは別の入れ物から読む（混ざっていたら 1069 は undefined のはず）
  assert.equal(call(ctx, `METRIC = "cost_rate"; valueAt("1069","2026-07")`), undefined);
});

test("crossProfit：理論が無い店は実原価で粗利率を出し、根拠を gpSrc に残す", () => {
  const ctx = loadApp(acBase());
  const by = Object.fromEntries(call(ctx, `crossProfit()`).map(r => [r.code, r]));
  assert.equal(by["1006"].gpSrc, "理論");
  assert.ok(Math.abs(by["1006"].gp - 0.70) < 1e-9, "理論がある店は理論のまま（1-0.30）");
  assert.equal(by["1069"].gpSrc, "実原価", "理論が無い店は実原価で埋める");
  assert.ok(Math.abs(by["1069"].gp - 0.60) < 1e-9, "1-0.40");
});

test("店舗詳細：実原価カードと、理論との差＝不明ロスが出る", () => {
  const ctx = loadApp(acBase());
  const html = call(ctx, `renderProfitability("1006")`);
  assert.ok(html.includes("実原価率（2026-07）"), "実原価率のカード");
  assert.ok(html.includes("32.0%"), "実原価率の値");
  assert.ok(html.includes("不明ロス（2026-07）"), "理論と実原価の差");
  assert.ok(html.includes("+2.0"), "32.0% − 30.0% = +2.0pt");
});

// 材料が揃っていても値が嘘の月がある（実測で 342.9% の月があった）。
// 消さずに「要確認」で出し、順位づけや穴埋めには使わない。
const acSuspect = () => {
  const d = acBase();
  d.actual_cost = {
    1006: {
      "2026-06": { food: 2400000, drink: 800000, total: 3200000, rate: 0.32 },
      "2026-07": { food: 9000000, drink: 100000, total: 9100000, rate: 3.429,
                   suspect: "率が高すぎる（棚卸の取り違え・単位違いの疑い）" },
    },
    1069: {
      "2026-07": { food: 1500000, drink: 500000, total: 2000000, rate: 0.40 },
    },
  };
  d.monthly[1006]["2026-06"] = { sales: 9000000 };
  d.cost_rate = { 1006: { "2026-07": 0.30 } };
  return d;
};

test("要確認の月は指標として読まない（342.9%が順位に混ざらない）", () => {
  const ctx = loadApp(acSuspect());
  assert.equal(call(ctx, `METRIC = "actual_cost_rate"; valueAt("1006","2026-07")`), undefined,
    "印の付いた月は率として出さない");
  assert.equal(call(ctx, `valueAt("1006","2026-06")`), 0.32, "ふつうの月はそのまま");
});

test("要確認の月は粗利率の穴埋めにも使わない（ひとつ前の正常な月に下がる）", () => {
  const ctx = loadApp(acSuspect());
  const by = Object.fromEntries(call(ctx, `crossProfit()`).map(r => [r.code, r]));
  // 1006 は理論があるので理論のまま。1069 は実原価（印なし）で埋まる
  assert.equal(by["1006"].gpSrc, "理論");
  assert.equal(by["1069"].gpSrc, "実原価");
});

test("店舗詳細：要確認の月は数字を出したうえで理由を書く（黙って消さない）", () => {
  const ctx = loadApp(acSuspect());
  const html = call(ctx, `renderProfitability("1006")`);
  assert.ok(html.includes("要確認"), "要確認の印");
  assert.ok(html.includes("棚卸の取り違え"), "理由をそのまま出す");
  assert.ok(html.includes("342.9%"), "値も見せる（消すと元データの誤りに気づけない）");
  assert.ok(!html.includes("不明ロス"), "信用できない月に理論との差は出さない");
});

test("店舗詳細：理論が無い店でも実原価だけで節が出る（不明ロスは出さない）", () => {
  const ctx = loadApp(acBase());
  const html = call(ctx, `renderProfitability("1069")`);
  assert.ok(html.includes("実原価率（2026-07）"), "実原価だけでも節を出す");
  assert.ok(html.includes("40.0%"), "実原価率の値");
  assert.ok(!html.includes("不明ロス"), "理論が無い月に差は出さない");
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
  assert.ok(html.includes("販促一覧"), "販促セクションはある");
});

test("renderStore：販促トップは累計サマリー→来月のアクションで、詳細は折りたたみへ", () => {
  const ctx = loadApp(base);
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("累計サマリー"), "上部に累計サマリー（予算/実績・前年対比・客単価）");
  assert.ok(html.includes("のアクション"), "来月のアクション");
  assert.ok(html.includes("詳細データを見る"), "分析系は details（詳細データ）へ格納");
  // 並び順はセクションidで確認（ナビのラベルと混同しないように）。
  assert.ok(html.indexOf('id="hero"') < html.indexOf('id="actions"'), "サマリーがアクションより先");
  assert.ok(html.indexOf('id="actions"') < html.indexOf('id="basics"'), "詳細は下（折りたたみ）");
});

test("来月のアクション：来月動く販促に準備の抜けと去年の学び（メモ・次回提案）を出す", () => {
  const prev = camp({ id: "p-2025", bucket: "パフェ", start: "2025-09-16", end: "2025-10-31", title: "去年パフェ", memo: "去年は立ち上がり弱い" });
  const cur = camp({ id: "p-2026", bucket: "パフェ", start: "2026-09-16", end: "2026-10-31", title: "今年パフェ" });
  const data = { ...base, campaigns: [prev, cur], proposals: { "p-2025": { next: "予告POPを早めに" } } };
  const ctx = loadApp(data, "2026-09-23");
  const html = call(ctx, `storeNextActions("1006")`);
  assert.match(html, /のアクション/);
  assert.match(html, /今年パフェ/, "来月動く販促を出す");
  assert.match(html, /去年「去年パフェ」/, "去年の同じ回を参照");
  assert.match(html, /去年は立ち上がり弱い/, "去年の要因メモを反映");
  assert.match(html, /予告POPを早めに/, "去年の次回提案を反映");
});

test("来月のアクション：終了して振り返り未記入は『やりっぱなし』として出す", () => {
  const done = camp({ id: "d1", bucket: "パフェ", start: "2026-05-01", end: "2026-06-30", title: "春パフェ" });
  const ctx = loadApp({ ...base, campaigns: [done] }, "2026-09-23");
  const html = call(ctx, `storeNextActions("1006")`);
  assert.match(html, /やりっぱなし/);
  assert.match(html, /春パフェ/);
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

test("販促プラン：起票ボタン・計画バッジが販促一覧に出る（本番のみ）", () => {
  const c = camp({ id: "real", stores: ["1160"], bucket: "パフェ", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...luqa, campaigns: [c] });
  const html = call(ctx, `PLANS_API_OK=true; WRITE_OK=true; PLANS=[{id:"plan-x",store_code:"1160",title:"秋パフェ計画",kind:"osusume",bucket:"パフェ",start:"2026-09-01",end:"2026-10-31",goal:1500000,note:"狙い",by:"me"}]; BASE_CAMPAIGNS=null; applyPlans(); renderStore("1160")`);
  assert.ok(html.includes('data-plannew="1160"'), "＋起票ボタン");
  assert.ok(html.includes("秋パフェ計画") && html.includes("plbadge"), "計画がバッジ付きで並ぶ");
  // 複製/編集の細かい操作は販促詳細ページ側へ移動（一覧は簡素化）。
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

test("renderStore：終了して未記入の販促は『振り返り未記入』を強調する", () => {
  const c = camp({ id: "d1", bucket: "コース", title: "終わった企画", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [c] });   // today=2026-09-06 → done、memo/proposal 無し
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("振り返り未記入"), "やりっぱなしを強調");
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

test("renderStore：効果スコア（◎効いた）と、販促一覧の判定バッジが出る", () => {
  const c = camp({ bucket: "コース", start: "2026-01-01", end: "2026-01-31" });
  const ctx = loadApp({ ...base, campaigns: [c] });
  const html = call(ctx, `renderStore("1006")`);
  assert.ok(html.includes("効いた"), "効果スコア（storeScoreStrip）");
  assert.ok(html.includes("cvm good"), "販促一覧の◎判定バッジ");
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
  assert.ok(html.includes("data-compocell"), "構成比の金額セルは押せる（商品内訳の小窓）");
});

test("storeAnnualChart：販促は月カルーセル（上POP・下販促）として見出し付きで出す", () => {
  const ctx = loadApp(annualData);
  const html = call(ctx, `storeAnnualChart("1160","2025")`);
  assert.ok(html.includes("販促 年間チャート"), "販促の年間チャート見出し");
  assert.ok(html.includes('class="pcar"'), "月カルーセルで表示（横スクロール）");
  assert.ok(/class="pcar-mo[^"]*"/.test(html), "月ごとのカード");
  assert.ok(html.includes('data-camp="snow"'), "その月に実施中の販促チップ");
  assert.ok(/data-smonth="1160:2025-09"/.test(html), "月見出しはその月の詳細へ飛べる");
  // 制作物が無い月は「POP・制作物なし」のプレースホルダ（サムネ枠は上に置く設計）
  assert.ok(html.includes("POP・制作物なし"), "POPが無い月はプレースホルダ");
  // 表示切替（月ごと／年間チャート）がある
  assert.ok(/data-pcview="carousel"/.test(html) && /data-pcview="gantt"/.test(html), "販促チャートの表示切替タブ");
});

test("storeAnnualChart：年間チャート（帯・一覧）に切り替えると帯で出る", () => {
  const ctx = loadApp(annualData);
  const html = call(ctx, `PROMO_CHART_VIEW="gantt"; storeAnnualChart("1160","2025")`);
  assert.ok(html.includes('class="panel gantt"'), "帯（ガント）で表示");
  assert.ok(html.includes('data-camp="snow"'), "販促の帯");
  assert.ok(/class="gbar[^"]*"[^>]*data-tip="[^"]*｜/.test(html), "帯に構造化ツールチップ(data-tip)");
});

test("creativesForMonth：実施中の各施策1枚だけ＋同じ資料は月内で被らせない", () => {
  // snow=2025-06〜09、cake9=2025-09〜11（annualData）。snowにPOP2枚（同月でも1枚に絞る）。
  const dup = {
    ...annualData,
    creatives: [
      { id: "a1", campaign_id: "snow", mime: "image/png", url: "/creatives/snow1.png", thumb: "/creatives/snow1.png", title: "snowPOP1" },
      { id: "a2", campaign_id: "snow", mime: "image/png", url: "/creatives/snow2.png", thumb: "/creatives/snow2.png", title: "snowPOP2" },
      { id: "c1", campaign_id: "cake9", mime: "image/png", url: "/creatives/cake.png", thumb: "/creatives/cake.png", title: "cakePOP" },
    ],
  };
  const ctx = loadApp(dup);
  // 実施中の月（snowのみ）は、snowのPOPが何枚あっても1枚だけ。
  assert.equal(call(ctx, `creativesForMonth("1160","2025-07").length`), 1, "実施中でも各施策1枚だけ");
  // 実施中でない月は出さない。
  assert.equal(call(ctx, `creativesForMonth("1160","2025-05").length`), 0, "実施していない月は出さない");
  // snow+cake9 の両方が実施中の月は各1枚＝2枚（施策ごと）。
  assert.equal(call(ctx, `creativesForMonth("1160","2025-09").length`), 2, "実施中の施策ごとに1枚");

  // 同じ資料(URL)が別施策にまたがっても、月内では1枚だけ（被らせない）。
  const shared = {
    ...annualData,
    creatives: [
      { id: "s1", campaign_id: "snow", mime: "image/png", url: "/creatives/same.png", thumb: "/creatives/same.png", title: "共通POP" },
      { id: "s2", campaign_id: "cake9", mime: "image/png", url: "/creatives/same.png", thumb: "/creatives/same.png", title: "共通POP(別施策)" },
    ],
  };
  const ctx2 = loadApp(shared);
  assert.equal(call(ctx2, `creativesForMonth("1160","2025-09").length`), 1, "同一URLは月内で1枚に畳む");

  // 未来の月（当月より先）はPOPを出さない＝まだ販売していない予定販促にPOPが並ばない。
  const future = {
    ...annualData,
    campaigns: [{ id: "xmas", stores: ["1160"], scope_all: false, title: "クリスマス", kind: "dev", bucket: "ケーキ", start: "2099-12-01", end: "2099-12-25" }],
    creatives: [{ id: "x1", campaign_id: "xmas", mime: "image/png", url: "/creatives/xmas.png", thumb: "/creatives/xmas.png", title: "クリスマスPOP" }],
  };
  const cf = loadApp(future);
  assert.equal(call(cf, `creativesForMonth("1160","2099-12").length`), 0, "未来の月はPOPを出さない（予定扱い）");
});

test("creativesForCampaign：同じ資料（画像POPとPDFが同名）はURLが違っても1枚に畳む", () => {
  // 同じ施策の同じデザインが、台帳の画像POPとアップロードPDFで二重登録されるケース。
  const dup = {
    ...annualData,
    creatives: [
      { id: "img", campaign_id: "cake9", mime: "image/png", url: "/creatives/a.png", thumb: "/creatives/a.png", title: "5月ケーキ POP" },
      { id: "pdf", campaign_id: "cake9", mime: "application/pdf", url: "/creatives/a.pdf", title: "5月ケーキ" },
      { id: "other", campaign_id: "cake9", mime: "image/png", url: "/creatives/b.png", thumb: "/creatives/b.png", title: "9月ケーキ 裏面" },
    ],
  };
  const ctx = loadApp(dup);
  // 「5月ケーキ POP」と「5月ケーキ」は同施策×同名(飾り語無視)→1枚。別デザイン(9月ケーキ)は残る＝計2。
  assert.equal(call(ctx, `creativesForCampaign("cake9").length`), 2, "同名の重複は畳み、別資料は残す");
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
