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
  const created = [];
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
      getElementById: getEl, createElement: () => { const e = mkEl(); created.push(e); return e; },
      body: { appendChild() {} },
      documentElement: mkEl(), addEventListener() {}, querySelector: () => mkEl(), querySelectorAll: () => [],
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
  return { ctx, byId, CAP, getEl, call: (e) => vm.runInContext(e, ctx), lastOverlay: () => created[created.length - 1], allCreated: () => created };
}

const base = {
  stores: [{ code: "1160", name: "ルクアLargo" }],
  regions: [{ name: "大阪", stores: ["1160"] }],
  months: ["2026-07", "2026-08"],
  monthly: { "1160": { "2026-07": { sales: 14000000 }, "2026-08": { sales: 15000000 },
    "2025-07": { sales: 13000000 }, "2025-08": { sales: 13500000 } } },
  covers: { "1160": { "2026-07": 29000, "2026-08": 30000, "2025-07": 27000, "2025-08": 28000 } },
  // DATA.cost_rate は割合（0.30＝30%）で入る（店ページの粗利=1-原価率が根拠）。
  cost_rate: { "1160": { "2026-07": 0.30, "2026-08": 0.31, "2025-07": 0.29, "2025-08": 0.30 } },
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

await test("原価率の実績は割合(0.31)を％(31.0)に換算して返す", () => {
  const { call } = loadForm(base);
  const v = call(`actualTargetValue("cost_rate","1160","2026-08","2026-08",false)`);
  assert.equal(v, 31, "0.31→31%（目標は％入力なので揃える）");
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
  h.getEl("pf-pdf").files = [];
  // 目標は「▽で選ぶ行」コントローラの collect() から来る（DOMは軽量モックなので値を直接注入）。
  h.getEl("planedit")._goalRows = { collect: () => ({ map: { sales: 16000000, cost_rate: 28 }, err: null }) };

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

await test("部門別・商品・予算達成率の目標が集計できる（選択付きキー）", () => {
  const d = { ...base,
    departments_monthly: { "1160": {
      "2026-07": { total_sales: 1000000, buckets: [ { name: "コース", sales: 300000, qty: 200 }, { name: "アラカルト", sales: 500000, qty: 400 } ] },
      "2026-08": { total_sales: 1000000, buckets: [ { name: "コース", sales: 300000, qty: 200 }, { name: "アラカルト", sales: 500000, qty: 400 } ] },
    } },
    products_monthly: { "1160": {
      "2026-07": [ { name: "刺身盛合せ", sales: 120000 } ],
      "2026-08": [ { name: "刺身盛合せ", sales: 130000 } ],
    } },
    monthly: { "1160": { "2026-07": { sales: 900000 }, "2026-08": { sales: 1100000 } } },
    budget: { "1160": { "2026-07": 1000000, "2026-08": 1000000 } },
  };
  const { call } = loadForm(d);
  const ms = `["2026-07","2026-08"]`;
  assert.equal(call(`_metricOverMonths("dept_sales#コース","1160",${ms})`), 600000, "部門別売上＝選んだ区分の合算");
  assert.equal(call(`_metricOverMonths("dept_qty#コース","1160",${ms})`), 400, "部門別数量＝qtyの合算");
  assert.equal(call(`_metricOverMonths("dept_share#アラカルト","1160",${ms})`), 50, "部門構成比＝区分売上/総売上");
  assert.equal(call(`_metricOverMonths("dept_avg_check#アラカルト","1160",${ms})`), 1250, "部門別客単価＝売上/数量");
  assert.equal(call(`_metricOverMonths("prod_sales#刺身盛合せ","1160",${ms})`), 250000, "商品の売上＝選んだ商品の合算");
  assert.equal(call(`_metricOverMonths("budget_rate","1160",${ms})`), 100, "予算達成率＝売上/予算×100");
  assert.equal(call(`metricLabel("dept_sales#コース")`), "部門別売上（コース）", "ラベルに選択が付く");
  assert.equal(call(`subKind("prod_sales")`), "prod", "商品指標は商品ピッカー");
});

console.log("目標の変更ログ（誰がいつ）");

await test("設定者・日付が達成サマリーの目標欄に出る", () => {
  const { call } = loadForm(base);
  // 売上目標は達成サマリーが持つ（振り返り表からは外した）。変更ログもそこに出す。
  call(`API_OK = true;
        SERVER_TARGETS = {"c1@2026":{value:16000000}};
        SERVER_TARGETS_M = {"c1@2026":{sales:{value:16000000, by:"店長A", at:"2026-09-20T10:00:00Z"}}};
        DATA.campaigns=[{id:"c1",stores:["1160"],scope_all:false,title:"ログ確認",kind:"osusume",start:"2026-11-01",end:"2026-12-31",note:"",bucket:null,items:[]}];`);
  const meta = JSON.parse(call(`JSON.stringify(targetMetaOf(DATA.campaigns[0],"sales"))`));
  assert.equal(meta.by, "店長A", "設定者を引ける");
  assert.equal(call(`shortYmd("2026-09-20T10:00:00Z")`), "2026/9/20", "ISO→年/月/日");
  const html = call(`renderCampaign("c1")`);
  assert.ok(html.includes("店長A") && html.includes("tr-meta"), "設定者名と変更ログ行が達成サマリーに出る");
});

console.log("目標フォームの詰め（妥当性チェック・現状比）");

await test("目標の範囲チェック：原価率は0〜100%、その他は0以上", () => {
  const { call, getEl } = loadForm(base);
  getEl("pf-tg-cost_rate").value = "120";
  assert.match(call("validateTargetInputs()"), /原価率.*0〜100/, "原価率120%は弾く");
  getEl("pf-tg-cost_rate").value = "28"; getEl("pf-tg-sales").value = "-5";
  assert.match(call("validateTargetInputs()"), /販促の売上.*0以上/, "負の売上目標は弾く");
  getEl("pf-tg-sales").value = "16000000";
  assert.equal(call("validateTargetInputs()"), null, "妥当な値は通る");
});

await test("現状比：客単価↑は+で緑、原価率↓は−で緑（反転）", () => {
  const { call, getEl } = loadForm(base);
  call(`TG_CUR = { sales: 14000000, cost_rate: 30 };`);
  getEl("pf-tg-sales").value = "16000000"; call(`updateTargetDiff("sales")`);
  assert.equal(getEl("pf-tgdiff-sales").className, "pf-tg-diff good", "売上+14%は緑");
  getEl("pf-tg-cost_rate").value = "28"; call(`updateTargetDiff("cost_rate")`);
  assert.equal(getEl("pf-tgdiff-cost_rate").className, "pf-tg-diff good", "原価率−6.7%は緑（反転）");
  getEl("pf-tg-cost_rate").value = "33"; call(`updateTargetDiff("cost_rate")`);
  assert.equal(getEl("pf-tgdiff-cost_rate").className, "pf-tg-diff bad", "原価率+10%は赤");
});

await test("スコアボードの担当者フィルタで絞れる", () => {
  const { call } = loadForm(base);
  call(`SERVER_TARGETS_M={"a@2026":{sales:{value:16000000}},"b@2026":{sales:{value:17000000}}};
    DATA.campaigns=[
      {id:"a",stores:["1160"],title:"Aの販促",kind:"osusume",start:"2026-11-01",end:"2026-12-31",owner:"田中"},
      {id:"b",stores:["1160"],title:"Bの販促",kind:"osusume",start:"2026-11-01",end:"2026-12-31",owner:"佐藤"}];`);
  let h = call(`BOARD_OWNER="all"; renderTargetBoard()`);
  assert.ok(h.includes("Aの販促") && h.includes("Bの販促") && h.includes("担当者") && h.includes("田中"), "全員＝両方＋担当者バー");
  h = call(`BOARD_OWNER="田中"; renderTargetBoard()`);
  assert.ok(h.includes("Aの販促") && !h.includes("Bの販促"), "田中で絞るとAだけ");
  call(`BOARD_OWNER="all"`);   // 後続テストに影響させない
});

await test("担当者サマリ：担当者別の平均達成率が色つきで出る", () => {
  const { call } = loadForm(base);
  call(`BOARD_OWNER="all";
    SERVER_TARGETS_M={"a@2026":{sales:{value:14000000}},"b@2026":{sales:{value:20000000}}};
    DATA.campaigns=[
      {id:"a",stores:["1160"],title:"A",kind:"osusume",start:"2026-08-01",end:"2026-08-01",open_ended:true,owner:"田中"},
      {id:"b",stores:["1160"],title:"B",kind:"osusume",start:"2026-08-01",end:"2026-08-01",open_ended:true,owner:"佐藤"}];`);
  const h = call(`renderTargetBoard()`);
  assert.ok(h.includes("担当者別 平均達成率"), "サマリ見出しが出る");
  // 実績 2026-08 の売上 15M。田中 目標14M→107%（緑）、佐藤 目標20M→75%（赤）
  assert.match(h, /class="tb-sum good[^"]*"[^>]*data-boardowner="田中"/, "田中は達成＝緑");
  assert.match(h, /class="tb-sum bad[^"]*"[^>]*data-boardowner="佐藤"/, "佐藤は未達＝赤");
  assert.ok(h.includes('tb-sum-v">107%') && h.includes('tb-sum-v">75%'), "平均達成率の数値");
  call(`BOARD_OWNER="all"`);
});

await test("複製起票：複製元の目標が初期値として引き継がれる", () => {
  const h = loadForm(base);
  h.call(`SERVER_TARGETS_M = {"src@2025":{sales:{value:16000000}, cost_rate:{value:28}}};
    DATA.campaigns=[{id:"src",stores:["1160"],title:"昨年の秋パフェ",kind:"osusume",start:"2025-11-01",end:"2025-12-31"}];`);
  h.call(`duplicatePlan("src","1160")`);
  // 目標行は動的生成（各行が別の created 要素）。オーバーレイ＋全行の innerHTML をまとめて確認。
  const html = h.allCreated().map(e => e.innerHTML || "").join("\n");
  assert.ok(html.includes("複製元から") && html.includes("引き継ぎ"), "引き継ぎ注記が出る");
  assert.ok(html.includes('value="16000000"'), "売上目標が初期値に入る");
  assert.ok(html.includes('value="28"'), "原価率目標が初期値に入る");
  assert.ok(html.includes("昨年の秋パフェ"), "販促名も複製される");
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
