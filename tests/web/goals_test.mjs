// 目標まわり（① 振り返り／② 入力フォーム／③ 損益の理由表示／目標スコアボード）の
// 計算と配線を、ブラウザ無しで固定する回帰テスト。
//
// いちばん守りたいのは「フォームで立てた目標が、保存キーと再読込キーのズレで迷子に
// ならない」こと。savePlanFromForm→/api/plans→campKey→/api/targets→再読込→
// targetMetricOf→スコアボード表示、までを実際の関数を呼んで通す。
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const src = fs.readFileSync(path.join(ROOT, "web/app.js"), "utf8");

// 値を持てる可変DOM＋状態を持つ fetch モック（POST を CAP に記録）。
function loadForm(data) {
  const byId = {};
  const CAP = { plans: [], targets: [] };
  let seq = 0;
  const mkEl = (id) => ({
    id: id || "", _html: "", className: "", style: {}, files: [], value: "",
    placeholder: "", textContent: "", hidden: false,
    set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
    setAttribute() {}, getAttribute() { return null; }, hasAttribute() { return false; },
    addEventListener() {}, appendChild() {}, remove() {}, focus() {},
    querySelector() { return mkEl(); }, querySelectorAll() { return []; },
    classList: { add() {}, remove() {}, contains() { return false; } },
  });
  const getEl = (id) => (byId[id] = byId[id] || mkEl(id));
  const okJson = (obj) => Promise.resolve({
    ok: true, status: 200, headers: { get: () => "application/json" }, json: () => Promise.resolve(obj),
  });
  const sandbox = {
    console, Math, Date, JSON, Intl, Number, String, Object, Array, setTimeout,
    document: {
      getElementById: getEl, createElement: () => mkEl(), body: { appendChild() {} },
      documentElement: mkEl(), addEventListener() {}, querySelectorAll: () => [],
    },
    window: { prompt: () => null, confirm: () => true, matchMedia: () => ({ matches: false }), addEventListener() {}, scrollTo() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    alert() {},
    fetch: (url, opts) => {
      const method = (opts && opts.method) || "GET";
      const body = opts && opts.body ? JSON.parse(opts.body) : {};
      if (url === "/api/plans" && method === "POST") {
        const id = body.id && body.id.trim() ? body.id : `plan-test-${++seq}`;
        const plan = { ...body, id };
        CAP.plans.push(plan);
        return okJson({ ok: true, plan });
      }
      if (url === "/api/targets" && method === "POST") { CAP.targets.push(body); return okJson({ ok: true, ...body }); }
      return Promise.resolve({ ok: false, status: 404, headers: { get: () => "" }, json: () => Promise.resolve({}) });
    },
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(src, ctx, { filename: "app.js" });
  vm.runInContext(`DATA=${JSON.stringify(data)}; PLANS=[]; PLANS_API_OK=true; ME={name:"テスト担当"}; BASE_CAMPAIGNS=null; render=function(){};`, ctx);
  return { ctx, byId, CAP, getEl, call: (e) => vm.runInContext(e, ctx) };
}

const base = {
  stores: [{ code: "1160", name: "ルクアLargo" }],
  regions: [{ name: "大阪", stores: ["1160"] }],
  months: ["2026-07", "2026-08"],
  monthly: { "1160": { "2026-07": { sales: 14000000 }, "2026-08": { sales: 15000000 },
    "2025-07": { sales: 13000000 }, "2025-08": { sales: 13500000 } } },
  covers: { "1160": { "2026-07": 29000, "2026-08": 30000, "2025-07": 27000, "2025-08": 28000 } },
  cost_rate: { "1160": { "2026-07": 30, "2026-08": 31, "2025-07": 29, "2025-08": 30 } },
  hourly: { "1160": { "12": { sales: 100000, covers: 200 } } },
  hourly_month: "2026-08", metrics: ["sales"], campaigns: [], cost_status: {},
};

let failed = 0;
// 同期・非同期どちらの本体も await して確実に集計する。
async function test(name, fn) {
  try { await fn(); console.log("  ok   " + name); }
  catch (e) { failed += 1; console.log("  FAIL " + name + "\n       " + e.message); }
}

async function main() {
console.log("目標の計算（達成率と反転色）");

await test("達成率：売上は 実績/目標、原価率は 目標/実績（低いほど達成＝緑）", () => {
  const { call } = loadForm(base);
  const up = call(`targetAchievement(TARGET_METRICS.find(m=>m.key==="sales"), 16000000, 14000000)`);
  assert.equal(up.good, false, "売上14M<目標16M は未達");
  const cr = call(`targetAchievement(TARGET_METRICS.find(m=>m.key==="cost_rate"), 28, 30)`);
  assert.equal(cr.good, false, "原価率30%>目標28% は未達（反転）");
  const cr2 = call(`targetAchievement(TARGET_METRICS.find(m=>m.key==="cost_rate"), 32, 30)`);
  assert.equal(cr2.good, true, "原価率30%<目標32% は達成（反転で緑）");
});

console.log("損益の理由表示（cost_status → costReason）");

await test("cost_status の各状態が理由ラベルに変換される", () => {
  const d = { ...base, cost_status: {
    "1766": { status: "pos", months: 0 }, "1743": { status: "new", months: 0 },
    "1151": { status: "partial", months: 1 }, "1160": { status: "none", months: 0 } } };
  const { call } = loadForm(d);
  assert.equal(call(`costReason("1766").label`), "新レジ未接続");
  assert.equal(call(`costReason("1743").label`), "新店・反映待ち");
  assert.equal(call(`costReason("1151").label`), "直近のみ");
  assert.equal(call(`costReason("1160").label`), "FW未反映");
  assert.equal(call(`costReason("9999")`), null, "未登録店は理由なし");
});

console.log("入力フォーム→保存→再読込→スコアボード（e2e）");

await test("常設で起票：目標が採番後IDのキーで保存され、再読込しても引ける", async () => {
  const h = loadForm(base);
  Object.assign(h.getEl("pf-title"), { value: "秋の実地テスト販促" });
  Object.assign(h.getEl("pf-store"), { value: "1160" });
  Object.assign(h.getEl("pf-start"), { value: "2026-08" });   // 実績のある月から
  Object.assign(h.getEl("pf-end"), { value: "" });            // 空＝常設（confirm=trueで進む）
  Object.assign(h.getEl("pf-kind"), { value: "osusume" });
  Object.assign(h.getEl("pf-bucket"), { value: "" });
  Object.assign(h.getEl("pf-owner"), { value: "テスト担当" });
  Object.assign(h.getEl("pf-note"), { value: "実地テスト" });
  Object.assign(h.getEl("pf-tg-sales"), { value: "16,000,000" });  // カンマ入り
  Object.assign(h.getEl("pf-tg-cost_rate"), { value: "28" });
  h.getEl("pf-pdf").files = [];

  await h.call("savePlanFromForm({})");

  // 1) /api/plans に常設・担当者つきで送られた
  const plan = h.CAP.plans[0];
  assert.equal(plan.open_ended, true, "終了日空欄→常設");
  assert.equal(plan.end, plan.start, "常設は end=start");
  assert.equal(plan.owner, "テスト担当");
  // 2) 目標は「採番後のプランID＠開始年」で送られた（空IDのままにならない）
  const key = `${plan.id}@2026`;
  const salesPost = h.CAP.targets.find(t => t.metric === "sales");
  assert.equal(salesPost.id, key, "目標キー＝採番後プランID@年");
  assert.equal(salesPost.target, 16000000, "カンマ入りが数値化");
  assert.ok(h.CAP.targets.some(t => t.metric === "cost_rate" && t.target === 28), "原価率目標も送信");
  // 3) 再読込を模した campaigns から、同じ施策の目標を引ける（キー一致）
  const back = JSON.parse(h.call(`(function(){
    const c = DATA.campaigns.find(x=>x.title==="秋の実地テスト販促");
    return JSON.stringify({key:campKey(c), oe:c.open_ended, owner:c.owner,
      sales:targetMetricOf(c,"sales"), cost:targetMetricOf(c,"cost_rate")});
  })()`));
  assert.equal(back.key, key, "再読込キーと保存キーが一致（目標が迷子にならない）");
  assert.equal(back.oe, true);
  assert.equal(back.owner, "テスト担当");
  assert.equal(back.sales, 16000000);
  assert.equal(back.cost, 28);
  // 4) スコアボードに載る
  const boardBad = h.call(`renderTargetBoard()`);
  assert.ok(boardBad.includes("秋の実地テスト販促"), "スコアボードに新施策が出る");
});

console.log("時間帯目標（月別プロファイル→期間内1日平均）");

await test("時間帯売上/集客は対象月の1日平均（A/V）、前年同期は前年の1日平均", () => {
  const d = { ...base, hourly_by_month: { "1160": {
    // 当期2ヶ月：1日あたり 売上 (100+80)=180k / (120+90)=210k → 平均195k、客数 (200+150)=350 / (240+180)=420 → 平均385
    "2026-07": { "12": { sales: 100000, covers: 200 }, "13": { sales: 80000, covers: 150 } },
    "2026-08": { "12": { sales: 120000, covers: 240 }, "13": { sales: 90000, covers: 180 } },
    // 前年同期：1日 売上 (90+70)=160k、客数 (180+140)=320
    "2025-07": { "12": { sales: 90000, covers: 180 }, "13": { sales: 70000, covers: 140 } },
    "2025-08": { "12": { sales: 90000, covers: 180 }, "13": { sales: 70000, covers: 140 } } } } };
  const { call } = loadForm(d);
  // 当期（2026-07..08）の1日平均売上＝(180k+210k)/2=195k
  const act = call(`actualTargetValue("hour_sales","1160","2026-07","2026-08",false)`);
  assert.equal(act, 195000, "当期の時間帯売上は1日平均（合算でなく）");
  const cov = call(`actualTargetValue("hour_covers","1160","2026-07","2026-08",false)`);
  assert.equal(cov, 385, "当期の時間帯集客は1日平均");
  // 薄字＝前年同期（2025-07..08）の1日平均売上＝160k
  const ref = call(`currentTargetValue("hour_sales","1160","2026-07","2026-08",false)`);
  assert.equal(ref, 160000, "前年同期の時間帯売上1日平均");
});

console.log("目標の変更ログ（誰がいつ）");

await test("設定者・日付が振り返り表の目標欄に出る", () => {
  const { call } = loadForm(base);
  call(`SERVER_TARGETS_M = {"c1@2026":{sales:{value:16000000, by:"店長A", at:"2026-09-20T10:00:00Z"}}};
        DATA.campaigns=[{id:"c1",stores:["1160"],title:"ログ確認",kind:"osusume",start:"2026-11-01",end:"2026-12-31"}];`);
  const meta = JSON.parse(call(`JSON.stringify(targetMetaOf(DATA.campaigns[0],"sales"))`));
  assert.equal(meta.by, "店長A", "設定者を引ける");
  assert.equal(call(`shortYmd("2026-09-20T10:00:00Z")`), "2026/9/20", "ISO→年/月/日");
  const html = call(`renderTargetReview(DATA.campaigns[0])`);
  assert.ok(html.includes("店長A") && html.includes("tr-meta"), "設定者名と変更ログ行が表に出る");
});

await test("目標未入力の販促だけならスコアボードは非表示", () => {
  const { call } = loadForm({ ...base, campaigns: [
    { id: "x", stores: ["1160"], title: "目標なし", kind: "osusume", start: "2026-11-01", end: "2026-12-31" } ] });
  assert.equal(call(`renderTargetBoard()`), "", "目標ゼロならパネルごと出さない");
});

  console.log(failed ? `\n${failed} 件失敗` : "\nすべて通過");
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.log("実行時エラー:", e.stack || e.message); process.exit(1); });
