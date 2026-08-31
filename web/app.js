/* 販促マネジメント・ダッシュボード（閲覧SPA）
   dashboard.json を読むだけ。表示のたびに DB は叩かない。

   主役は「店舗 × 販促」。
     店一覧（グリッド）… 開いた直後。全店をカードで、売上ミニ推移つき
     店舗詳細        … 1店の売上推移＋打った施策＋近隣（同エリア）比較
     総合／エリア     … 控えめ。下部に畳む */
"use strict";

const METRIC_LABELS = {
  sales: "売上", food_sales: "フード売上", drink_sales: "ドリンク売上",
  food_theory_cost: "フード理論原価", drink_theory_cost: "ドリンク理論原価",
  cost_rate: "理論原価率",
};
const yen = n => "¥" + Math.round(n).toLocaleString("ja-JP");
const man = n => (n / 10000).toFixed(0) + "万";
const pct = n => (n * 100).toFixed(1) + "%";
const signed = n => (n >= 0 ? "+" : "") + n.toFixed(1);

function axisLabel(months, i) {
  const [y, mo] = months[i].split("-");
  const prevYear = i > 0 ? months[i - 1].split("-")[0] : null;
  return (i === 0 || y !== prevYear) ? `${y.slice(2)}年${+mo}月` : `${+mo}月`;
}

const REGION_COLORS = {
  "大阪": "#2E4A7D", "東京": "#1F7A5C", "京都": "#8A5A2B",
  "兵庫": "#6E4B8A", "福岡": "#9A3B54", "未分類": "#6B7280",
};
const regionColor = r => REGION_COLORS[r] || "#6B7280";

// 施策の種類（色分け）。config/schedule.yaml の kind と対応。
// 計画表の語彙に合わせている（GM改定／ランチ変更／おすすめ／忘年会／その他開発／休業）。
const KIND = {
  gm:        { label: "GM改定",    color: "#7A4FA0" },
  lunch:     { label: "ランチ変更", color: "#1F7A8C" },
  osusume:   { label: "おすすめ",   color: "#C8791E" },
  bounenkai: { label: "忘年会",     color: "#2E4A7D" },
  dev:       { label: "その他開発", color: "#2E8B57" },
  closure:   { label: "休業",       color: "#8A8F99" },
};
const kindOf = k => KIND[k] || KIND.dev;

// 日付まわり（"YYYY-MM" と "YYYY-MM-DD" を扱う。文字列比較で前後が分かる）
const parseDate = s => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d || 1); };
const monthStart = ym => { const [y, m] = ym.split("-").map(Number); return new Date(y, m - 1, 1); };
const monthEnd = ym => { const [y, m] = ym.split("-").map(Number); return new Date(y, m, 1); }; // 翌月1日（排他）
const addMonth = (ym, delta) => {
  const [y, m] = ym.split("-").map(Number);
  const t = new Date(y, m - 1 + delta, 1);
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}`;
};
const monthRange = (a, b) => { const out = []; let cur = a; while (cur <= b) { out.push(cur); cur = addMonth(cur, 1); } return out; };
// 人が入力した文字（要因メモ等）を安全に埋め込む。改行は <br> に。
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const escBr = s => esc(s).replace(/\n/g, "<br>");

let DATA = null;
let VIEW = { kind: "schedule" };   // schedule(TOP) | calendar | store,code | list | overview
let METRIC = "sales";
let CAL_MONTH = null;              // カレンダー表示中の月（"YYYY-MM"）
let YEAR = null;                   // 年間販促ビューで表示中の年（数値）
let CAMP_FILTER = { status: "all", kind: "all" };   // 施策の効果ビューの絞り込み
// 販促の目標（施策id→円）。本番は Neon（/api/targets）に共有保存、
// プレビュー等 API が無い所では端末内（localStorage）に保存する。
let API_OK = false;              // 目標APIが使えるか（本番=true）
let SERVER_TARGETS = {};         // id → {value, by, at}（サーバ値）
let GOALS = {};                  // id → 円（端末内フォールバック）

function loadGoals() { try { return JSON.parse(localStorage.getItem("hansoku_goals") || "{}"); } catch (e) { return {}; } }
function saveGoals() { try { localStorage.setItem("hansoku_goals", JSON.stringify(GOALS)); } catch (e) { /* 保存不可でも表示は続ける */ } }

async function fetchServerTargets() {
  try {
    const res = await fetch("/api/targets", { headers: { accept: "application/json" }, cache: "no-store" });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || !ct.includes("application/json")) return;   // プレビューはHTMLが返る→端末内保存へ
    const data = await res.json();
    if (data && data.targets) { SERVER_TARGETS = data.targets; API_OK = true; }
  } catch (e) { /* API 無し → 端末内保存で動く */ }
}

// 有効な目標＝サーバ値（本番）→ 端末内 → schedule.yaml の順
function targetOf(c) {
  if (API_OK) {
    const s = SERVER_TARGETS[c.id];
    if (s && typeof s.value === "number") return s.value;
    return c.target != null ? c.target : null;
  }
  if (c.id in GOALS) return GOALS[c.id];
  return c.target != null ? c.target : null;
}

async function editGoal(id) {
  const cur = API_OK ? (SERVER_TARGETS[id] && SERVER_TARGETS[id].value) : GOALS[id];
  const v = window.prompt("この販促の目標売上（円）を入力してください（空欄で削除）", cur == null ? "" : String(cur));
  if (v === null) return;
  const cleaned = String(v).replace(/[,，円\s]/g, "");
  let value = null;
  if (cleaned !== "") { const n = parseInt(cleaned, 10); if (isNaN(n)) return; value = n; }

  if (API_OK) {
    try {
      const res = await fetch("/api/targets", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id, target: value }),
      });
      if (res.status === 401) { alert("目標の保存にはログインが必要です。"); return; }
      if (!res.ok) { alert("目標の保存に失敗しました。時間をおいて再度お試しください。"); return; }
      if (value === null) delete SERVER_TARGETS[id];
      else SERVER_TARGETS[id] = { value, by: "自分", at: new Date().toISOString() };
    } catch (e) { alert("目標の保存に失敗しました（通信エラー）。"); return; }
  } else {
    if (value === null) delete GOALS[id]; else GOALS[id] = value;
    saveGoals();
  }
  render();
}

// 販促の要因メモ（施策id→本文）。目標と同じく本番=Neon(/api/notes)共有、
// API が無い所では端末内(localStorage)に保存する。
let SERVER_NOTES = {};           // id → {note, by, at}（サーバ値）
let NOTES = {};                  // id → 本文（端末内フォールバック）
function loadNotes() { try { return JSON.parse(localStorage.getItem("hansoku_notes") || "{}"); } catch (e) { return {}; } }
function saveNotes() { try { localStorage.setItem("hansoku_notes", JSON.stringify(NOTES)); } catch (e) { /* 保存不可でも表示は続ける */ } }

async function fetchServerNotes() {
  try {
    const res = await fetch("/api/notes", { headers: { accept: "application/json" }, cache: "no-store" });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || !ct.includes("application/json")) return;
    const data = await res.json();
    if (data && data.notes) SERVER_NOTES = data.notes;
  } catch (e) { /* API 無し → 端末内保存で動く */ }
}

// 有効な要因メモ＝サーバ値（本番）→ 端末内 → 書き出し時に焼いた memo の順
function memoOf(c) {
  if (API_OK) {
    const s = SERVER_NOTES[c.id];
    if (s && s.note) return s.note;
    return c.memo || "";
  }
  if (NOTES[c.id]) return NOTES[c.id];
  return c.memo || "";
}

async function editMemo(id) {
  const cur = API_OK ? (SERVER_NOTES[id] && SERVER_NOTES[id].note) : NOTES[id];
  const v = window.prompt("この販促の要因メモ（なぜ動いた/動かなかったか）。空欄で削除", cur || "");
  if (v === null) return;
  const note = String(v).trim();

  if (API_OK) {
    try {
      const res = await fetch("/api/notes", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id, note }),
      });
      if (res.status === 401) { alert("メモの保存にはログインが必要です。"); return; }
      if (!res.ok) { alert("メモの保存に失敗しました。時間をおいて再度お試しください。"); return; }
      if (!note) delete SERVER_NOTES[id];
      else SERVER_NOTES[id] = { note, by: "自分", at: new Date().toISOString() };
    } catch (e) { alert("メモの保存に失敗しました（通信エラー）。"); return; }
  } else {
    if (!note) delete NOTES[id]; else NOTES[id] = note;
    saveNotes();
  }
  render();
}

const CURRENT_MONTH = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
})();
const isProvisional = m => m === CURRENT_MONTH;
const TODAY = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
})();

// 施策の状態（今日基準）。予定 / 実施中 / 終了
function campStatus(c) {
  if (TODAY < c.start) return { k: "soon", label: "予定" };
  if (TODAY > c.end) return { k: "done", label: "終了" };
  return { k: "live", label: "実施中" };
}
// 施策期間の効果（月次・確定分のみ）。施策が掛かる確定月の値を、前年同月と比べる。
// 月次データしか無いので月単位の概算。当月（暫定）と未来月は含めない。
// accessor(code, month) で「売上」でも「客数」でも同じ計算を使い回す。
function effectOver(code, c, accessor) {
  const sM = c.start.slice(0, 7), eM = c.end.slice(0, 7);
  let cur = 0, prev = 0, months = 0, prevOk = true;
  for (const m of DATA.months) {
    if (m >= CURRENT_MONTH || m < sM || m > eM) continue;
    const a = accessor(code, m);
    if (typeof a !== "number") continue;
    const [y, mo] = m.split("-");
    const b = accessor(code, `${+y - 1}-${mo}`);
    cur += a; months += 1;
    if (typeof b === "number") prev += b; else prevOk = false;
  }
  if (!months) return null;
  // 前月比：施策開始の直前・同じ月数ぶんと比べる（季節性は前年比で見る前提の補助）
  let momPrev = 0, momOk = true, m = sM;
  for (let k = 0; k < months; k++) {
    m = addMonth(m, -1);
    const a = accessor(code, m);
    if (typeof a === "number") momPrev += a; else momOk = false;
  }
  return {
    months, cur,
    prev: prevOk ? prev : null,
    pct: (prevOk && prev) ? (cur / prev - 1) * 100 : null,
    momPrev: momOk ? momPrev : null,
    momPct: (momOk && momPrev) ? (cur / momPrev - 1) * 100 : null,
  };
}
function campEffect(code, c) {
  if (METRIC === "cost_rate") return null;
  return effectOver(code, c, valueAt);
}
// 施策期間の集客（客数）効果。売上と別枠（DATA.covers）。指標選択に依らず常に客数。
function campCovers(code, c) {
  if (!DATA.covers || !DATA.covers[code]) return null;
  return effectOver(code, c, coversAt);
}

// ── 起動 ─────────────────────────────────────────────────────────────────
async function boot() {
  document.getElementById("today").textContent = formatToday();
  wireTheme();
  try {
    const res = await fetch("data/dashboard.json", { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    DATA = await res.json();
  } catch (e) {
    document.getElementById("app").innerHTML =
      `<div class="empty">データを読み込めませんでした（${e.message}）。<br>夜間バッチの書き出しをお待ちください。</div>`;
    return;
  }
  CAL_MONTH = CURRENT_MONTH;
  YEAR = new Date().getFullYear();
  GOALS = loadGoals();
  NOTES = loadNotes();
  await fetchServerTargets();   // 本番は Neon の共有目標を読む。無ければ端末内保存で動く
  await fetchServerNotes();     // 要因メモも同様（本番=共有、無ければ端末内）
  buildMetricSelect();
  buildStoreJump();
  render();
  fillNotice();
}

function formatToday() {
  const d = new Date();
  const w = "日月火水木金土"[d.getDay()];
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日（${w}）`;
}
function wireTheme() {
  document.getElementById("themebtn").addEventListener("click", () => {
    const root = document.documentElement;
    let now = root.getAttribute("data-theme");
    if (!now) now = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    root.setAttribute("data-theme", now === "dark" ? "light" : "dark");
  });
}

// ── データ小物 ───────────────────────────────────────────────────────────
const store = code => DATA.stores.find(s => s.code === code) || {};
const storeName = code => store(code).name || code;
const hasData = code => !!DATA.monthly[code];
// 店舗の月次売上予算（FW月別予算登録）。未取込なら undefined。
const budgetAt = (code, month) => ((DATA.budget || {})[code] || {})[month];
const hasBudget = code => DATA.budget && DATA.budget[code] && Object.values(DATA.budget[code]).some(v => v > 0);
// 直近確定月の予算達成率（％）。売上のときだけ、予算が正のときだけ。無ければ null。
function budgetRate(code) {
  if (METRIC !== "sales") return null;
  const latest = latestConfirmed(code);
  if (!latest) return null;
  const b = budgetAt(code, latest.m);
  if (typeof b !== "number" || b <= 0) return null;
  return { m: latest.m, rate: latest.v / b * 100 };
}

function valueAt(code, month) {
  if (METRIC === "cost_rate") return (DATA.cost_rate[code] || {})[month];
  return ((DATA.monthly[code] || {})[month] || {})[METRIC];
}
function series(code, months) {
  return months.map(m => {
    const v = METRIC === "cost_rate" ? (DATA.cost_rate[code] || {})[m] : valueAt(code, m);
    return typeof v === "number" ? v : null;
  });
}
const periodTotal = code => {
  let sum = 0;
  for (const m of DATA.months) {
    const v = ((DATA.monthly[code] || {})[m] || {})[METRIC];
    if (typeof v === "number" && m < CURRENT_MONTH) sum += v;
  }
  return sum;
};
// 直近の確定月（当月・未来月・空月は除く）。データのある最新の締め済み月を返す
function latestConfirmed(code) {
  for (let i = DATA.months.length - 1; i >= 0; i--) {
    const m = DATA.months[i];
    if (m >= CURRENT_MONTH) continue;      // 当月＝暫定、未来月＝未締め
    const v = valueAt(code, m);
    if (typeof v === "number") return { m, v };
  }
  return null;
}
// 前年同月比。直近の確定月と、その1年前を比べる
function yoy(code) {
  const last = latestConfirmed(code);
  if (!last) return null;
  const [y, mo] = last.m.split("-");
  const prev = `${+y - 1}-${mo}`;
  const b = valueAt(code, prev);
  if (typeof b !== "number" || !b) return null;
  return { month: last.m, cur: last.v, prev: b, pct: (last.v / b - 1) * 100 };
}

// ── 集客（客数）。売上と別枠の DATA.covers を読む。人数なので円と混ぜない ──
const nin = n => Math.round(n).toLocaleString("ja-JP") + "人";
const coversAt = (code, month) => ((DATA.covers || {})[code] || {})[month];
// 店の集客サマリ：確定月の期間合計客数と、直近確定月の前年同月比
function coversSummary(code) {
  const per = DATA.covers && DATA.covers[code];
  if (!per) return null;
  let total = 0, has = false, last = null;
  for (const m of DATA.months) {
    const v = per[m];
    if (typeof v !== "number" || m >= CURRENT_MONTH) continue;
    total += v; has = true; last = { m, v };
  }
  if (!has) return null;
  let yoyPct = null;
  if (last) {
    const [y, mo] = last.m.split("-");
    const b = per[`${+y - 1}-${mo}`];
    if (typeof b === "number" && b) yoyPct = (last.v / b - 1) * 100;
  }
  return { total, last, yoyPct };
}

// ── コントロール ─────────────────────────────────────────────────────────
function buildMetricSelect() {
  const sel = document.getElementById("metricsel");
  sel.innerHTML = DATA.metrics.concat(["cost_rate"])
    .map(m => `<option value="${m}">${METRIC_LABELS[m] || m}</option>`).join("");
  sel.value = METRIC;
  sel.addEventListener("change", () => { METRIC = sel.value; render(); });
}
function buildStoreJump() {
  const sel = document.getElementById("storesel");
  const opts = ['<option value="">店舗をさがす…</option>']
    .concat(DATA.stores.filter(s => hasData(s.code))
      .map(s => `<option value="${s.code}">${s.name}（${s.region}）</option>`));
  sel.innerHTML = opts.join("");
  sel.addEventListener("change", () => {
    if (sel.value) { VIEW = { kind: "store", code: sel.value }; render(); sel.value = ""; }
  });
}

// ── ルーティング描画 ─────────────────────────────────────────────────────
function render() {
  const app = document.getElementById("app");
  if (VIEW.kind === "store") app.innerHTML = renderStore(VIEW.code);
  else if (VIEW.kind === "lunch") app.innerHTML = renderLunch(VIEW.code);
  else if (VIEW.kind === "campaign") app.innerHTML = renderCampaign(VIEW.id);
  else if (VIEW.kind === "manage") app.innerHTML = renderManage();
  else if (VIEW.kind === "overview") app.innerHTML = renderOverview();
  else if (VIEW.kind === "list") app.innerHTML = renderList();
  else if (VIEW.kind === "campaigns") app.innerHTML = renderCampaigns();
  else if (VIEW.kind === "year") app.innerHTML = renderYear();
  else if (VIEW.kind === "cross") app.innerHTML = renderCross();
  else if (VIEW.kind === "gallery") app.innerHTML = renderGallery();
  else if (VIEW.kind === "calendar") app.innerHTML = renderCalendar();
  else app.innerHTML = renderSchedule();

  app.querySelectorAll("[data-camp]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); go({ kind: "campaign", id: el.dataset.camp }); }));
  app.querySelectorAll("[data-store]").forEach(el =>
    el.addEventListener("click", () => go({ kind: "store", code: el.dataset.store })));
  app.querySelectorAll("[data-lunch]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); go({ kind: "lunch", code: el.dataset.lunch }); }));
  app.querySelectorAll("[data-view]").forEach(el =>
    el.addEventListener("click", () => go({ kind: el.dataset.view })));
  app.querySelectorAll("[data-cal]").forEach(el =>
    el.addEventListener("click", () => { CAL_MONTH = addMonth(CAL_MONTH, el.dataset.cal === "next" ? 1 : -1); render(); }));
  app.querySelectorAll("[data-year]").forEach(el =>
    el.addEventListener("click", () => { YEAR = +el.dataset.year; render(); }));
  app.querySelectorAll("[data-goal]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); editGoal(el.dataset.goal); }));
  app.querySelectorAll("[data-memo]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); editMemo(el.dataset.memo); }));
  app.querySelectorAll("[data-cfilter]").forEach(el =>
    el.addEventListener("click", () => {
      const [dim, val] = el.dataset.cfilter.split(":");
      CAMP_FILTER = { ...CAMP_FILTER, [dim]: val };
      render();
    }));
  wireEmphasis(app);
}
function go(v) { VIEW = v; render(); window.scrollTo({ top: 0, behavior: "smooth" }); }

// タイムライン⇄カレンダーの切替
function viewToggle(active) {
  const t = (k, label) => `<button class="vtab${active === k ? " on" : ""}" data-view="${k}">${label}</button>`;
  return `<div class="viewtabs">${t("schedule", "タイムライン")}${t("calendar", "カレンダー")}</div>`;
}

// ── 直近のアクション（TOPの一番上・人がやることを促す）──────────────────
const daysBetween = (a, b) => Math.round((parseDate(b) - parseDate(a)) / 86400000);
function actionPanel() {
  const camps = DATA.campaigns || [];
  // まもなく開始（今日〜21日先）
  const soon = camps
    .map(c => ({ c, d: daysBetween(TODAY, c.start) }))
    .filter(x => campStatus(x.c).k === "soon" && x.d >= 0 && x.d <= 21)
    .sort((a, b) => a.d - b.d).slice(0, 8);
  // 実施中なのに目標が未入力（その場で入れられる）
  const noGoal = camps.filter(c => campStatus(c).k === "live" && targetOf(c) == null).slice(0, 8);
  // 終了したのに振り返り(要因メモ/次回提案)が未記入＝やりっぱなし
  const review = camps.filter(needsReview).sort((a, b) => a.end < b.end ? 1 : -1).slice(0, 8);
  if (!soon.length && !noGoal.length && !review.length) return "";

  const scopeOf = c => c.scope_all ? "全店" : `${c.stores.length}店`;
  const soonHtml = soon.map(({ c, d }) => {
    const k = kindOf(c.kind);
    return `<li><span class="kdot" style="background:${k.color}"></span>
      <span class="amain">${c.title}</span><span class="atag">${scopeOf(c)}</span>
      <span class="aday${d <= 3 ? " near" : ""}">${d === 0 ? "本日開始" : `あと${d}日`}</span></li>`;
  }).join("");
  const goalHtml = noGoal.map(c =>
    `<li><span class="kdot" style="background:${kindOf(c.kind).color}"></span>
      <span class="amain">${c.title}</span><span class="atag">${scopeOf(c)}</span>
      <button class="goalbtn add" data-goal="${c.id}">＋ 目標を入力</button></li>`).join("");

  const reviewHtml = review.map(c =>
    `<li><span class="kdot" style="background:${kindOf(c.kind).color}"></span>
      <span class="amain">${c.title}</span><span class="atag">${scopeOf(c)}</span>
      <button class="goalbtn add" data-camp="${c.id}">振り返る →</button></li>`).join("");

  const cols = [];
  if (soon.length) cols.push(`<div class="acol"><div class="ahd">まもなく開始</div><ul class="alist">${soonHtml}</ul></div>`);
  if (noGoal.length) cols.push(`<div class="acol"><div class="ahd">目標が未入力（実施中）</div><ul class="alist">${goalHtml}</ul></div>`);
  if (review.length) cols.push(`<div class="acol"><div class="ahd">要振り返り（終了・未記入）</div><ul class="alist">${reviewHtml}</ul></div>`);
  return `<section class="actions"><div class="ahead">直近のアクション</div>
    <div class="acols">${cols.join("")}</div></section>`;
}

// ── 全店スケジュール（TOP・主役）────────────────────────────────────────
// 重なる施策を段（レーン）に振り分ける。start順に、空いた段へ置いていく。
function packLanes(list) {
  const laneEnd = [];   // 段ごとの「最後の終了日」
  const placed = list.map(c => {
    let li = laneEnd.findIndex(end => end < c.start);
    if (li === -1) { li = laneEnd.length; laneEnd.push(c.end); }
    else laneEnd[li] = c.end;
    return { c, lane: li };
  });
  return { placed, laneCount: Math.max(1, laneEnd.length) };
}

function renderSchedule() {
  const camps = DATA.campaigns || [];
  const activeCodes = new Set(DATA.stores.map(s => s.code));

  // 表示する月の窓：今月±3。施策の端がはみ出すなら広げる
  let lo = addMonth(CURRENT_MONTH, -3), hi = addMonth(CURRENT_MONTH, 3);
  for (const c of camps) {
    const s = c.start.slice(0, 7), e = c.end.slice(0, 7);
    if (s < lo) lo = s;
    if (e > hi) hi = e;
  }
  const months = monthRange(lo, hi);
  const winStart = monthStart(months[0]);
  const span = monthEnd(months[months.length - 1]) - winStart;
  const frac = s => Math.max(0, Math.min(1, (parseDate(s) - winStart) / span));
  const P = f => (f * 100).toFixed(2) + "%";

  const gridlines = months.map(m => `<div class="gl" style="left:${P(frac(m + "-01"))}"></div>`).join("");
  const monthLabels = months.map((m, i) => {
    const mid = (frac(m + "-01") + frac(addMonth(m, 1) + "-01")) / 2;
    return `<div class="mlab" style="left:${P(mid)}">${axisLabel(months, i)}</div>`;
  }).join("");
  const todayF = (new Date() - winStart) / span;
  const inWin = todayF > 0 && todayF < 1;
  const todayLine = inWin ? `<div class="tdl" style="left:${P(todayF)}"></div>` : "";
  const todayLab = inWin ? `<div class="tdlab" style="left:${P(todayF)}">今日</div>` : "";

  const byStore = {};
  for (const c of camps) for (const code of c.stores) (byStore[code] ||= []).push(c);

  const barFor = ({ c, lane }) => {
    const k = kindOf(c.kind);
    const tip = `${c.title}（${c.start === c.end ? c.start : c.start + "〜" + c.end}）`;
    if (c.start === c.end) {
      return `<div class="cmk" data-camp="${c.id}" style="left:${P(frac(c.start))};--lane:${lane};--kc:${k.color}" title="${tip}">
        <span class="cmk-t">${c.title}</span></div>`;
    }
    const l = frac(c.start), w = Math.max(frac(c.end) - l, 0.015);
    return `<div class="cbar" data-camp="${c.id}" style="left:${P(l)};width:${P(w)};--lane:${lane};--kc:${k.color}" title="${tip}">${c.title}</div>`;
  };

  const rowsHtml = DATA.regions.map(r => {
    const codes = r.stores.filter(c => activeCodes.has(c));
    if (!codes.length) return "";
    const rows = codes.map(code => {
      const list = (byStore[code] || []).slice().sort((a, b) => a.start < b.start ? -1 : 1);
      const { placed, laneCount } = packLanes(list);
      const lane = list.length
        ? placed.map(barFor).join("")
        : `<span class="none">―</span>`;
      return `
        <div class="srow${list.length ? "" : " is-empty"}" style="--lanes:${laneCount}" data-store="${code}">
          <div class="snm"><span class="rtag" style="--rc:${regionColor(r.name)}">${r.name}</span>
            <span class="snm-t">${storeName(code)}</span></div>
          <div class="strack">${gridlines}${todayLine}${lane}</div>
        </div>`;
    }).join("");
    return `<div class="sgrp"><span>${r.name}</span><span class="sgrp-n">${codes.length}店</span></div>${rows}`;
  }).join("");

  const legend = Object.values(KIND)
    .map(v => `<span class="klg"><i style="background:${v.color}"></i>${v.label}</span>`).join("");
  const emptyBanner = camps.length === 0
    ? `<div class="empty">施策はまだ登録されていません。config/schedule.yaml に追記すると、ここに帯で並びます。</div>`
    : "";

  return `
    ${actionPanel()}
    ${viewToggle("schedule")}
    <section class="block">
      <div class="bhead"><h2>全店スケジュール</h2>
        <span class="bnote">${months[0]}〜${months[months.length - 1]}　店を選ぶと詳細へ</span></div>
      ${emptyBanner}
      <div class="klgrow">${legend}</div>
      <div class="sched"><div class="sched-inner">
        <div class="srow shead-row">
          <div class="snm"></div>
          <div class="strack">${gridlines}${monthLabels}${todayLine}${todayLab}</div>
        </div>
        ${rowsHtml}
      </div></div>
    </section>
    ${renderGroupProducts()}
    <div class="ovrlink">
      <button class="linkbtn" data-view="campaigns">施策の効果 →</button>
      <span class="sep">／</span>
      <button class="linkbtn" data-view="year">年間販促 →</button>
      <span class="sep">／</span>
      <button class="linkbtn" data-view="cross">販促ターゲット（横断）→</button>
      <span class="sep">／</span>
      <button class="linkbtn" data-view="manage">店舗管理 →</button>
      <span class="sep">／</span>
      <button class="linkbtn" data-view="list">店舗カードで見る →</button>
      <span class="sep">／</span>
      <button class="linkbtn" data-view="overview">エリア・全店の表 →</button>
      <span class="sep">／</span>
      <button class="linkbtn" data-view="gallery">制作物ギャラリー →</button>
    </div>`;
}

// グループ全体の売れ筋商品（FW ABC分析・全店）。おすすめ料理候補。
function renderGroupProducts() {
  const items = DATA.products_group || [];
  if (!items.length) return "";
  const max = Math.max(1, ...items.map(p => p.sales));
  const rankColor = r => r === "A" ? "var(--good-ink)" : r === "B" ? "var(--accent)" : "var(--ink-3)";
  const rows = items.map((p, i) => {
    const pct = Math.max(3, Math.round(p.sales / max * 100));
    const rank = p.rank
      ? `<span class="prank" style="--pc:${rankColor(p.rank)}">${esc(p.rank)}</span>` : "";
    return `<li class="prow">
      <span class="pno">${i + 1}</span>
      <span class="pname">${esc(p.name)}</span>${rank}
      <span class="pbar"><span class="pfill" style="width:${pct}%"></span></span>
      <span class="psales">${yen(p.sales)}</span></li>`;
  }).join("");
  const monthLbl = DATA.products_month ? `（${DATA.products_month}）` : "";
  return `
    <section class="block">
      <div class="bhead"><h2>売れ筋商品（グループ全店・ABC）</h2>
        <span class="bnote">売上上位${items.length}品${monthLbl}　ランクはFWのABC　おすすめ料理の検討に</span></div>
      <div class="panel"><ul class="plist">${rows}</ul></div>
    </section>`;
}

function legendHtml() {
  return Object.values(KIND).map(v => `<span class="klg"><i style="background:${v.color}"></i>${v.label}</span>`).join("");
}

// ── カレンダー（月表示・打ち出し日中心）─────────────────────────────────
function renderCalendar() {
  const camps = DATA.campaigns || [];
  const [Y, M] = CAL_MONTH.split("-").map(Number);
  const first = new Date(Y, M - 1, 1);
  const startDow = first.getDay();
  const daysInMonth = new Date(Y, M, 0).getDate();
  const weeks = Math.ceil((startDow + daysInMonth) / 7);
  const gridStart = new Date(Y, M - 1, 1 - startDow);
  const key = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

  // 打ち出し日（開始日）と単日イベントを、その日に置く
  const byDay = {};
  for (const c of camps) for (const code of c.stores) (byDay[c.start] ||= []).push({ code, c });

  const todayKey = key(new Date());
  const dows = ["日", "月", "火", "水", "木", "金", "土"];
  const head = dows.map((w, i) => `<div class="caldow${i === 0 ? " sun" : i === 6 ? " sat" : ""}">${w}</div>`).join("");

  let cells = "";
  for (let i = 0; i < weeks * 7; i++) {
    const d = new Date(gridStart); d.setDate(gridStart.getDate() + i);
    const inMonth = d.getMonth() === M - 1;
    const dow = d.getDay();
    const k = key(d);
    const evs = byDay[k] || [];
    const shown = evs.slice(0, 3);
    const chips = shown.map(({ code, c }) => {
      const kc = kindOf(c.kind);
      const pt = c.start === c.end;
      const range = pt ? c.start : `${c.start}〜${c.end}`;
      return `<button class="cev${pt ? " pt" : ""}" data-store="${code}" style="--kc:${kc.color}"
         title="${storeName(code)}｜${c.title}（${range}）">${storeName(code)} ${c.title}</button>`;
    }).join("");
    const more = evs.length > shown.length ? `<div class="cmore">＋${evs.length - shown.length}件</div>` : "";
    cells += `<div class="calcell${inMonth ? "" : " other"}${dow === 0 ? " sun" : dow === 6 ? " sat" : ""}${k === todayKey ? " today" : ""}">
      <div class="cdno">${d.getDate()}</div>${chips}${more}</div>`;
  }

  return `
    ${viewToggle("calendar")}
    <section class="block">
      <div class="calnav">
        <button class="linkbtn" data-cal="prev">◀ 前の月</button>
        <div class="caltitle">${Y}年${M}月</div>
        <button class="linkbtn" data-cal="next">次の月 ▶</button>
      </div>
      <div class="bnote" style="margin-bottom:10px">打ち出し日・単日イベント（おすすめ開始／忘年会開始／ランチ変更／GM改定 等）を表示。期間の帯はタイムラインで。</div>
      <div class="klgrow">${legendHtml()}</div>
      <div class="cal"><div class="cal-inner">
        <div class="calgrid calhead">${head}</div>
        <div class="calgrid calbody">${cells}</div>
      </div></div>
    </section>
    <div class="ovrlink">
      <button class="linkbtn" data-view="list">店舗カードで見る →</button>
      <span class="sep">／</span>
      <button class="linkbtn" data-view="overview">エリア・全店の表 →</button>
    </div>`;
}

// ── 店一覧（店舗カード）──────────────────────────────────────────────────
// 店の「実施中の販促」件数と、目標が入っている分の達成率（確定分）
function storePromoSummary(code) {
  const live = (DATA.campaigns || []).filter(c => c.stores.includes(code) && campStatus(c).k === "live");
  if (!live.length) return null;
  let a = 0, t = 0;
  for (const c of live) {
    const tg = targetOf(c), e = campEffect(code, c);
    if (tg && e && e.cur) { a += e.cur; t += tg; }
  }
  return { count: live.length, rate: t ? a / t * 100 : null };
}

function renderList() {
  const months = DATA.months;
  // エリア順に並べる（大阪→東京→…）。エリアは見出しの小さなラベルに留める
  const cards = DATA.regions.flatMap(r =>
    r.stores.filter(hasData).map(code => {
      const color = regionColor(r.name);
      const total = periodTotal(code);
      const y = yoy(code);
      const spark = sparkline(series(code, months), color);
      const yline = y
        ? `<span class="yoy ${y.pct >= 0 ? "up" : "down"}">前年比 ${signed(y.pct)}%</span>`
        : `<span class="yoy flat">前年比 ―</span>`;
      const promo = storePromoSummary(code);
      const promoLine = promo
        ? `<div class="scamp">実施中 ${promo.count}件${promo.rate != null ? ` ・ 達成 <span class="${promo.rate >= 100 ? "up" : "down"}">${promo.rate.toFixed(0)}%</span>` : ""}</div>`
        : `<div class="scamp muted">実施中の販促なし</div>`;
      const br = budgetRate(code);
      const budLine = br
        ? `<span class="budg ${br.rate >= 100 ? "up" : "down"}" title="${br.m} の 実績÷予算">予算 ${br.rate.toFixed(0)}%</span>`
        : "";
      return `
        <button class="scard" data-store="${code}" style="--rc:${color}">
          <div class="stop"><span class="rtag">${r.name}</span>${storeName(code)}</div>
          <div class="sbig">${man(total)}<span class="unit">円</span></div>
          ${spark}
          ${promoLine}
          <div class="sfoot">${yline}${budLine}<span class="more">詳しく →</span></div>
        </button>`;
    })).join("");

  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <section class="block">
      <div class="bhead"><h2>店舗</h2>
        <span class="bnote">${METRIC_LABELS[METRIC]}・期間合計／前年同月比（当月の暫定は除く）</span></div>
      <div class="sgrid">${cards}</div>
    </section>
    <div class="ovrlink"><button class="linkbtn" data-view="overview">エリア・全店の一覧を見る →</button></div>
  `;
}

// ── 施策の効果ランキング（施策を全店横断で集計）──────────────────────────
// 1施策を、対象店それぞれの campEffect（確定月のみ）で合算する。
function campaignSummary(c) {
  let cur = 0, prev = 0, prevOk = true, stores = 0, monthsMax = 0, tot = 0;
  let mom = 0, momOk = true;
  // 集客（客数）も同じ期間で合算する。売上と別枠（DATA.covers）
  let cCur = 0, cPrev = 0, cPrevOk = true, cOk = false;
  for (const code of c.stores) {
    if (!hasData(code)) continue;
    tot += 1;
    const e = campEffect(code, c);
    if (!e) continue;
    stores += 1; cur += e.cur; monthsMax = Math.max(monthsMax, e.months);
    if (e.prev != null) prev += e.prev; else prevOk = false;
    if (e.momPrev != null) mom += e.momPrev; else momOk = false;
    const cv = campCovers(code, c);
    if (cv) {
      cOk = true; cCur += cv.cur;
      if (cv.prev != null) cPrev += cv.prev; else cPrevOk = false;
    }
  }
  return {
    total: tot, stores, cur, months: monthsMax,
    prev: prevOk ? prev : null,
    pct: (prevOk && prev) ? (cur / prev - 1) * 100 : null,
    momPct: (momOk && mom) ? (cur / mom - 1) * 100 : null,
    covers: cOk ? cCur : null,
    coversPct: (cOk && cPrevOk && cPrev) ? (cCur / cPrev - 1) * 100 : null,
  };
}

function renderCampaigns() {
  const isRatio = METRIC === "cost_rate";
  // 状態順（実施中→予定→終了）→ 同状態内は前年比の良い順（データ無しは後ろ）
  const STORD = { live: 0, soon: 1, done: 2 };
  const all = (DATA.campaigns || []).map(c => ({ c, s: campStatus(c), sum: campaignSummary(c) }));
  const rows = all.filter(r =>
    (CAMP_FILTER.status === "all" || r.s.k === CAMP_FILTER.status) &&
    (CAMP_FILTER.kind === "all" || r.c.kind === CAMP_FILTER.kind));
  rows.sort((a, b) => {
    const d = STORD[a.s.k] - STORD[b.s.k];
    if (d) return d;
    const pa = a.sum.pct == null ? -Infinity : a.sum.pct;
    const pb = b.sum.pct == null ? -Infinity : b.sum.pct;
    if (pa !== pb) return pb - pa;
    return b.sum.cur - a.sum.cur;
  });

  const body = rows.map(({ c, s, sum }) => {
    const k = kindOf(c.kind);
    const range = c.start === c.end ? c.start : `${c.start} 〜 ${c.end}`;
    const scope = c.scope_all ? "全店" : `${sum.total}店`;
    const tgt = targetOf(c);
    // 効果（確定分の実績合計・前年比）。売上のときだけ意味を持つ
    let effHtml = `<span class="muted">確定待ち</span>`;
    if (isRatio) {
      effHtml = `<span class="muted">―</span>`;
    } else if (sum.stores) {
      const yoy = sum.pct != null
        ? `<span class="${sum.pct >= 0 ? "up" : "down"}">前年比 ${signed(sum.pct)}%</span>`
        : `<span class="muted">前年比 ―</span>`;
      const mom = sum.momPct != null
        ? `　<span class="${sum.momPct >= 0 ? "up" : "down"}">前月比 ${signed(sum.momPct)}%</span>` : "";
      effHtml = `<b>${man(sum.cur)}円</b>　${yoy}${mom}<span class="sub">（確定${sum.months}ヶ月・${sum.stores}店）</span>`;
      // 集客（客数）。売上表示のときだけ、同期間の客数を前年比つきで添える
      if (METRIC === "sales" && sum.covers != null) {
        const cy = sum.coversPct != null
          ? `<span class="${sum.coversPct >= 0 ? "up" : "down"}">前年比 ${signed(sum.coversPct)}%</span>` : "前年 ―";
        effHtml += `<div class="ceff sub2">集客 <b>${nin(sum.covers)}</b>・${cy}</div>`;
      }
    }
    const goalHtml = tgt != null
      ? `<span class="cgtag">目標 ${man(tgt)}円</span>` : "";
    const memo = memoOf(c);
    const memoHtml = memo
      ? `<div class="cmemo">${escBr(memo)} <button class="goalbtn" data-memo="${c.id}" title="メモを編集">✎</button></div>`
      : `<div class="cmemo muted"><button class="goalbtn add" data-memo="${c.id}">＋ 要因メモ</button></div>`;
    return `<li data-camp="${c.id}">
      <span class="kchip" style="--kc:${k.color}">${k.label}</span>
      <div class="cbody">
        <div class="ctitle">${c.title}<span class="tagx">${scope}</span>
          <span class="cstat ${s.k}">${s.label}</span>${goalHtml}</div>
        ${c.note ? `<div class="cnote">${c.note}</div>` : ""}
        <div class="ceff">${effHtml}</div>
        ${campHeadline(c)}
        ${memoHtml}
        <div class="cgo">詳細を確認 →</div>
      </div>
      <span class="crange">${range}</span>
    </li>`;
  }).join("");

  const withEff = rows.filter(r => r.sum.stores && r.sum.pct != null);
  const plus = withEff.filter(r => r.sum.pct >= 0).length;

  const chip = (dim, val, label) =>
    `<button class="fchip${CAMP_FILTER[dim] === val ? " on" : ""}" data-cfilter="${dim}:${val}">${label}</button>`;
  const statusChips = [["all", "すべて"], ["live", "実施中"], ["soon", "予定"], ["done", "終了"]]
    .map(([v, l]) => chip("status", v, l)).join("");
  const kindChips = [chip("kind", "all", "すべて")]
    .concat(Object.entries(KIND).map(([k, v]) => chip("kind", k, v.label))).join("");
  const fbar = `<div class="fbar">
    <span class="flabel">状態</span>${statusChips}
    <span class="fsep"></span><span class="flabel">種類</span>${kindChips}</div>`;

  const empty = all.length
    ? `<div class="empty">この条件の施策はありません。</div>`
    : `<div class="empty">施策がまだ登録されていません。</div>`;
  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <section class="block">
      <div class="bhead"><h2>施策の効果</h2>
        <span class="bnote">${METRIC_LABELS[METRIC]}・確定月の全店合算／前年同月比　${withEff.length ? `前年比プラス ${plus}/${withEff.length}` : ""}</span></div>
      ${fbar}
      ${rows.length ? `<ul class="clist">${body}</ul>` : empty}
    </section>`;
}

// ── 施策詳細（スケジュール・一覧からタップして遷移）────────────────────────
// 施策内容／進捗（状態・期間の進み）／結果（対象店ごとの前年比・前月比・集客・目標達成）
// ／要因メモ／POP・制作物を1画面に。ランチ施策は各店の効果一覧への導線も出す。
const campById = id => (DATA.campaigns || []).find(c => c.id === id) || null;
const creativesForCampaign = id => (DATA.creatives || []).filter(cr => cr.campaign_id === id);

// 期間の進み具合（%）。予定=0／終了=100／実施中はstart〜endの経過割合。
function campTimeProgress(c) {
  const st = parseDate(c.start), en = parseDate(c.end), now = new Date();
  const k = campStatus(c).k;
  if (k === "soon") return 0;
  if (k === "done") return 100;
  if (c.start === c.end) return 100;
  return Math.max(1, Math.min(99, Math.round((now - st) / (en - st) * 100)));
}

// 施策の種類に対応する部門バケット（効果が最も表れる区分）。
function campKindBucket(kind) {
  return kind === "lunch" ? "ランチ" : kind === "bounenkai" ? "コース" : null;
}
const bucketOf = (code, name) => {
  const d = deptFor(code);
  return d ? (d.buckets || []).find(b => b.name === name) || null : null;
};

// 施策×データ紐づけ: 種類に応じて「関連部門の直近実績」または「売れ筋」を出す。
// lunch→ランチ部門、bounenkai→コース部門、osusume/gm→売れ筋上位。dev は環境効果(別)。
function campDeptCard(c) {
  const monthLbl = DATA.products_month ? `（${DATA.products_month}）` : "";
  const bname = campKindBucket(c.kind);
  if (bname) {
    const rows = c.stores.map(code => {
      const d = deptFor(code);
      if (!d) return "";
      const b = bucketOf(code, bname);
      if (!b || (!b.sales && !b.qty)) return `<li class="cdrow is-muted"><span class="cdnm">${storeName(code)}</span>
        <span class="sub">${bname}の計上なし</span></li>`;
      const share = Math.round((b.share || 0) * 100);
      const cr = b.cost_rate != null ? `<span class="cdcr">原価${b.cost_rate}%</span>` : "";
      const q = b.qty ? `・${b.qty.toLocaleString("ja-JP")}点` : "";
      return `<li class="cdrow" data-store="${code}">
        <span class="cdnm">${storeName(code)}</span>
        <span class="cdmet"><b>${yen(b.sales)}</b>　構成比 ${share}%${q}　${cr}</span></li>`;
    }).filter(Boolean).join("");
    if (!rows) return "";
    return `<section class="block">
      <div class="bhead"><h2>関連部門の実績（${bname}）</h2>
        <span class="bnote">FW ABC 部門${monthLbl}・この施策が効く区分</span></div>
      <div class="panel"><ul class="cdlist">${rows}</ul>
        <div class="cdnote">${bname}の売上・構成比が施策後に伸びているかを、確定月ごとに追ってください。</div></div>
    </section>`;
  }
  if (c.kind === "osusume" || c.kind === "gm") {
    const rows = c.stores.map(code => {
      const items = ((DATA.products || {})[code] || []).slice(0, 3);
      if (!items.length) return "";
      const chips = items.map((p, i) => `<span class="cdtop">${i + 1}. ${esc(p.name)} ${yen(p.sales)}</span>`).join("");
      return `<li class="cdrow" data-store="${code}"><span class="cdnm">${storeName(code)}</span>
        <span class="cdtops">${chips}</span></li>`;
    }).filter(Boolean).join("");
    if (!rows) return "";
    return `<section class="block">
      <div class="bhead"><h2>売れ筋（対象店）</h2>
        <span class="bnote">FW ABC 商品${monthLbl}・フェア/改定の主役候補</span></div>
      <div class="panel"><ul class="cdlist">${rows}</ul></div>
    </section>`;
  }
  return "";
}

function renderCampaign(id) {
  const c = campById(id);
  if (!c) return `<div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <div class="empty">施策が見つかりません。</div>`;
  const k = kindOf(c.kind);
  const st = campStatus(c);
  const range = c.start === c.end ? c.start : `${c.start} 〜 ${c.end}`;
  const sum = campaignSummary(c);
  const tgt = targetOf(c);
  const prog = campTimeProgress(c);
  const isRatio = METRIC === "cost_rate";

  // 目標（進捗欄で編集）
  const goalBtn = tgt != null
    ? `<button class="goalbtn" data-goal="${c.id}" title="目標を編集">目標 ${man(tgt)}円 ✎</button>`
    : `<button class="goalbtn add" data-goal="${c.id}">＋ 目標を入力</button>`;

  // 全体結果（確定月・全店合算）
  let overall;
  if (isRatio) {
    overall = `<div class="empty">原価率では施策の効果集計を出しません。売上に切り替えてご覧ください。</div>`;
  } else if (sum.stores) {
    const yoy = sum.pct != null
      ? `<span class="${sum.pct >= 0 ? "up" : "down"}">前年比 ${signed(sum.pct)}%</span>`
      : `<span class="muted">前年比 ―</span>`;
    const mom = sum.momPct != null
      ? `<span class="${sum.momPct >= 0 ? "up" : "down"}">前月比 ${signed(sum.momPct)}%</span>` : "";
    const rate = (tgt && sum.cur) ? sum.cur / tgt * 100 : null;
    const goalKpi = tgt != null
      ? `<div class="kpi"><div class="lbl">目標達成</div>
          <div class="big ${rate != null && rate >= 100 ? "up" : "down"}">${rate != null ? rate.toFixed(0) + "%" : "―"}</div>
          <div class="delta">目標 ${man(tgt)} → 実績 ${man(sum.cur)}</div></div>` : "";
    const covKpi = (METRIC === "sales" && sum.covers != null)
      ? `<div class="kpi"><div class="lbl">集客（確定分）</div><div class="big">${nin(sum.covers)}</div>
          <div class="delta">${sum.coversPct != null ? `<span class="${sum.coversPct >= 0 ? "up" : "down"}">前年比 ${signed(sum.coversPct)}%</span>` : "前年比 ―"}</div></div>` : "";
    overall = `<div class="kpis">
      <div class="kpi"><div class="lbl">実績（確定${sum.months}ヶ月・${sum.stores}/${sum.total}店）</div>
        <div class="big">${man(sum.cur)}<span class="unit">円</span></div>
        <div class="delta">${yoy}　${mom}</div></div>
      ${goalKpi}${covKpi}
    </div>`;
  } else {
    overall = `<div class="empty">確定した月の売上が出たら、前年同月比などの結果を表示します（月単位で集計）。</div>`;
  }

  // 対象店ごとの結果（クリックで店舗詳細へ／ランチ施策は効果一覧への導線）
  const rowsHtml = c.stores.map(code => {
    if (!hasData(code)) return `<li class="cmrow is-muted"><span class="cmnm">${storeName(code)}</span>
      <span class="cmeff sub">実績データなし</span></li>`;
    const lunchLink = (c.kind === "lunch" && lunchFor(code))
      ? `<button class="linkbtn cmlunch" data-lunch="${esc(code)}">新ランチ効果 →</button>` : "";
    let effHtml = `<span class="sub">確定待ち</span>`;
    if (!isRatio) {
      const eff = campEffect(code, c);
      if (eff) {
        const yoy = eff.pct != null
          ? `<span class="${eff.pct >= 0 ? "up" : "down"}">前年比 ${signed(eff.pct)}%</span>` : `<span class="muted">前年比 ―</span>`;
        const mom = eff.momPct != null
          ? `・<span class="${eff.momPct >= 0 ? "up" : "down"}">前月比 ${signed(eff.momPct)}%</span>` : "";
        const cov = (METRIC === "sales") ? campCovers(code, c) : null;
        const cv = cov ? `・集客 ${nin(cov.cur)}${cov.pct != null ? `(<span class="${cov.pct >= 0 ? "up" : "down"}">${signed(cov.pct)}%</span>)` : ""}` : "";
        effHtml = `<b>${man(eff.cur)}円</b>　${yoy}${mom}${cv}`;
      }
    } else {
      effHtml = `<span class="muted">―</span>`;
    }
    return `<li class="cmrow" data-store="${code}">
      <span class="cmnm">${storeName(code)}</span>
      <span class="cmeff">${effHtml}</span>${lunchLink}</li>`;
  }).join("");

  // 要因メモ
  const memo = memoOf(c);
  const memoHtml = memo
    ? `<div class="cmemo">${escBr(memo)} <button class="goalbtn" data-memo="${c.id}" title="メモを編集">✎</button></div>`
    : `<div class="cmemo muted"><button class="goalbtn add" data-memo="${c.id}">＋ 要因メモ</button></div>`;

  // POP・制作物（この施策に紐づくもの）
  const crs = creativesForCampaign(id);
  const crBlock = crs.length
    ? `<section class="block"><div class="bhead"><h2>POP・制作物</h2><span class="bnote">${crs.length}件</span></div>
        <div class="cgrid">${crs.map(creativeCard).join("")}</div></section>` : "";

  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button>
      <span class="sep">／</span><button class="linkbtn" data-view="campaigns">施策の効果</button></div>
    <header class="chd">
      <div class="chd-top">
        <span class="kchip" style="--kc:${k.color}">${k.label}</span>
        <span class="cstat ${st.k}">${st.label}</span>
        <span class="tagx">${c.scope_all ? "全店" : c.stores.length + "店"}</span>
      </div>
      <h1 class="cname">${c.title}</h1>
      <div class="csub">${range}</div>
      ${c.note ? `<div class="cnote">${c.note}</div>` : ""}
    </header>

    <section class="block">
      <div class="bhead"><h2>進捗</h2>
        <span class="bnote">${st.label}${st.k === "live" ? `・期間の ${prog}% 経過` : ""}</span></div>
      <div class="panel">
        <div class="cprog"><span class="cprog-fill ${st.k}" style="width:${prog}%"></span></div>
        <div class="cprog-lbl"><span>${c.start}</span><span class="cprog-now ${st.k}">${st.label}</span><span>${c.end}</span></div>
        <div class="cgoalbar">${goalBtn}</div>
      </div>
    </section>

    <section class="block">
      <div class="bhead"><h2>結果（全体）</h2>
        <span class="bnote">${METRIC_LABELS[METRIC]}・確定月の全店合算／前年同月比（当月の暫定は除く）</span></div>
      <div class="panel">${overall}</div>
    </section>

    ${renderReview(c)}
    ${campDeptCard(c)}

    <section class="block">
      <div class="bhead"><h2>対象店ごとの結果</h2>
        <span class="bnote">${c.stores.length}店　店をタップで詳細へ</span></div>
      <div class="panel"><ul class="cmlist">${rowsHtml}</ul></div>
    </section>
    ${renderEnvEffect(id)}
    ${crBlock}`;
}

// ── 店舗管理（店舗ごとの施策一覧・進捗・結果）─────────────────────────────
// 各店の施策を「実施中→予定→終了」で並べ、実施中を目立たせる。
// 施策バーをタップで施策詳細、店名タップで店舗詳細へ。
function renderManage() {
  const STATUS_ORDER = { live: 0, soon: 1, done: 2 };
  const isRatio = METRIC === "cost_rate";
  const blocks = DATA.regions.map(r => {
    const codes = r.stores.filter(hasData);
    if (!codes.length) return "";
    const color = regionColor(r.name);
    const storeBlocks = codes.map(code => {
      const camps = (DATA.campaigns || []).filter(c => c.stores.includes(code))
        .sort((a, b) => {
          const d = STATUS_ORDER[campStatus(a).k] - STATUS_ORDER[campStatus(b).k];
          return d !== 0 ? d : (a.start < b.start ? -1 : 1);
        });
      const live = camps.filter(c => campStatus(c).k === "live").length;
      const items = camps.length
        ? camps.map(c => {
            const k = kindOf(c.kind), s = campStatus(c);
            let eff = null;
            if (!isRatio) eff = campEffect(code, c);
            const effHtml = eff && eff.pct != null
              ? `<span class="${eff.pct >= 0 ? "up" : "down"}">前年比 ${signed(eff.pct)}%</span>`
              : `<span class="sub">${s.k === "soon" ? "開始前" : "―"}</span>`;
            const prog = campTimeProgress(c);
            return `<li class="mgrow ${s.k}" data-camp="${c.id}">
              <span class="kdot" style="background:${k.color}"></span>
              <span class="mgnm">${c.title}</span>
              <span class="cstat ${s.k}">${s.label}</span>
              <span class="mgbar" title="期間の${prog}%経過"><span class="mgfill ${s.k}" style="width:${prog}%"></span></span>
              <span class="mgeff">${effHtml}</span></li>`;
          }).join("")
        : `<li class="mgrow is-empty"><span class="sub">施策なし</span></li>`;
      return `<div class="mgstore${live ? " has-live" : ""}">
        <button class="mghd" data-store="${code}">
          <span class="rtag" style="--rc:${color}">${r.name}</span>
          <span class="mgsnm">${storeName(code)}</span>
          ${live ? `<span class="mglive">実施中 ${live}件</span>` : `<span class="mgnone">実施中なし</span>`}
          <span class="more" style="--rc:${color}">詳しく →</span>
        </button>
        <ul class="mglist">${items}</ul>
      </div>`;
    }).join("");
    return `<div class="sgrp"><span>${r.name}</span><span class="sgrp-n">${codes.length}店</span></div>${storeBlocks}`;
  }).join("");

  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button>
      <span class="sep">／</span><button class="linkbtn" data-view="campaigns">施策の効果</button></div>
    <section class="block">
      <div class="bhead"><h2>店舗管理</h2>
        <span class="bnote">店舗ごとの施策一覧・進捗・結果　実施中を上に　施策/店名をタップで詳細</span></div>
      <div class="klgrow">${legendHtml()}</div>
      ${blocks}
    </section>`;
}

// ── 施策前後の時間帯別効果（config/env_effects.json 由来）──────────────────
// 電子タバコ・空調改善など環境系施策の前後を、集客／客単価／品数／単価で見せる。
// 深夜(22-1時)は「1人品数 × 1品単価」に分解（FW 時間帯別メニュー出数＋時間帯別売上）。
const envEffectFor = id => (DATA.env_effects || []).find(e => (e.campaign_ids || []).includes(id)) || null;
function envMetrics(p) {
  const paidDrink = (p.drink_items || 0) - (p.nomihoudai || 0);
  return {
    perDay: p.days ? p.covers / p.days : 0,
    avgCheck: p.covers ? p.sales / p.covers : 0,
    itemsPer: p.covers ? p.items / p.covers : 0,
    foodUnit: p.food_items ? p.food_sales / p.food_items : 0,
    drinkUnit: paidDrink > 0 ? p.drink_sales / paidDrink : 0,
    nPerDay: p.days ? p.night_covers / p.days : 0,
    nItemsPer: p.night_covers ? p.night_items / p.night_covers : 0,
    nUnit: p.night_items ? p.night_sales / p.night_items : 0,
    nCheck: p.night_covers ? p.night_sales / p.night_covers : 0,
  };
}
function renderEnvEffect(id) {
  const e = envEffectFor(id);
  if (!e || !(e.periods || []).length) return "";
  const per = e.periods;
  const base = per.find(p => p.label === e.base_label) || per[0];
  const after = per.find(p => p.label === e.after_label) || per[per.length - 1];
  if (!base || !after) return "";
  const mb = envMetrics(base), ma = envMetrics(after);
  const dp = (a, b) => a ? (b / a - 1) * 100 : null;
  const arrow = v => v == null ? "―" : `<span class="${v >= 0 ? "up" : "down"}">${signed(v)}%</span>`;
  const n2 = v => (Math.round(v * 100) / 100).toFixed(2);
  const row = (label, bv, av, fmt) =>
    `<tr><td class="ek">${label}</td><td class="num">${fmt(bv)}</td><td class="num">${fmt(av)}</td><td class="num">${arrow(dp(bv, av))}</td></tr>`;
  const dayRows = [
    row("集客/日", mb.perDay, ma.perDay, v => per1(v) + "人"),
    row("客単価", mb.avgCheck, ma.avgCheck, yen),
    row("品数/客", mb.itemsPer, ma.itemsPer, n2),
    row("一品単価(フード)", mb.foodUnit, ma.foodUnit, yen),
    row("有料1杯単価", mb.drinkUnit, ma.drinkUnit, yen),
  ].join("");
  const nightRows = per.map(p => {
    const m = envMetrics(p);
    const key = p.label === e.base_label || p.label === e.after_label;
    return `<tr class="${key ? "ekey" : "esub2"}"><td>${esc(p.label)}${p.note ? `<span class="enote">${esc(p.note)}</span>` : ""}</td>
      <td class="num">${per1(m.nPerDay)}人</td><td class="num">${n2(m.nItemsPer)}</td>
      <td class="num">${yen(m.nUnit)}</td><td class="num">${yen(m.nCheck)}</td></tr>`;
  }).join("");
  return `
    <section class="block">
      <div class="bhead"><h2>効果（時間帯別・前後）</h2>
        <span class="bnote">${esc(e.title || "")}</span></div>
      <div class="panel">
        <div class="ehd">全日　${esc(e.base_label)} → ${esc(e.after_label)}</div>
        <div class="chartwrap"><table class="etbl">
          <thead><tr><th></th><th class="num">${esc(e.base_label)}</th><th class="num">${esc(e.after_label)}</th><th class="num">変化</th></tr></thead>
          <tbody>${dayRows}</tbody></table></div>
        <div class="ehd" style="margin-top:16px">深夜（22〜1時）　1人品数 × 1品単価</div>
        <div class="chartwrap"><table class="etbl">
          <thead><tr><th>期間</th><th class="num">客/日</th><th class="num">品数/客</th><th class="num">1品単価</th><th class="num">客単価</th></tr></thead>
          <tbody>${nightRows}</tbody></table></div>
        ${e.caveat ? `<div class="lnote" style="margin-top:12px">${esc(e.caveat)}</div>` : ""}
      </div>
    </section>`;
}

// 販促集計の行に出す「前後比較のワンライン」。見たいときは行から詳細へ。
function envHeadline(id) {
  const e = envEffectFor(id);
  if (!e || !(e.periods || []).length) return "";
  const per = e.periods;
  const b = per.find(p => p.label === e.base_label) || per[0];
  const a = per.find(p => p.label === e.after_label) || per[per.length - 1];
  if (!b || !a) return "";
  const mb = envMetrics(b), ma = envMetrics(a);
  const d = (x, y) => x ? (y / x - 1) * 100 : null;
  // 実績（＝空調改善後の値）＋ 直近差異（基準→後の変化率）を並べる
  const chip = (lab, val, v, fmt) => {
    const delta = v == null ? "" : ` <span class="${v >= 0 ? "up" : "down"}">${signed(v)}%</span>`;
    return `<span class="ccmp-i">${lab} <b>${fmt(val)}</b>${delta}</span>`;
  };
  const n2 = x => (Math.round(x * 100) / 100).toFixed(2);
  return `<div class="ccmp"><span class="ccmp-h">${esc(e.base_label)}→${esc(e.after_label)}</span>` +
    chip("客単価", ma.avgCheck, d(mb.avgCheck, ma.avgCheck), yen) +
    chip("集客", ma.perDay, d(mb.perDay, ma.perDay), x => per1(x) + "人/日") +
    chip("品数/客", ma.itemsPer, d(mb.itemsPer, ma.itemsPer), n2) + "</div>";
}
function lunchHeadline(c) {
  const code = (c.stores || []).find(s => lunchFor(s));
  const e = code && lunchFor(code);
  if (!e) return "";
  const m = lunchMetrics(e);
  return `<div class="ccmp"><span class="ccmp-h">${esc(e.menu_title || "新ランチ")}</span>` +
    `<span class="ccmp-i">${per1(m.dailyPerDay)}食/日</span>` +
    `<span class="ccmp-i">原価 ${pct(m.dailyCost)}</span>` +
    `<span class="ccmp-i">ランチ構成比 ${pct(m.lunchShare)}</span></div>`;
}
// 施策1件の比較ワンライン（環境系→前後、ランチ→ランチ要点）。無ければ空。
const campHeadline = c => envHeadline(c.id) || (c.kind === "lunch" ? lunchHeadline(c) : "");

// ── PDCA（やりっぱなしにしない：実績→判定→近隣→次の一手）──────────────────
const proposalFor = id => (DATA.proposals || {})[id] || null;

// 実績からの自動判定（good/warn/flat/wait）＋根拠シグナル。近隣・提案と合わせて使う。
function campVerdict(c) {
  const e = envEffectFor(c.id);
  if (e && (e.periods || []).length) {
    const per = e.periods;
    const b = per.find(p => p.label === e.base_label) || per[0];
    const a = per.find(p => p.label === e.after_label) || per[per.length - 1];
    const mb = envMetrics(b), ma = envMetrics(a);
    const d = (x, y) => x ? (y / x - 1) * 100 : 0;
    const dc = d(mb.avgCheck, ma.avgCheck), dp = d(mb.perDay, ma.perDay);
    const sig = [`客単価 ${signed(dc)}%`, `集客 ${signed(dp)}%`];
    if (dc >= 3 || dp >= 3) return { tone: "good", label: "効果あり", signals: sig };
    if (dc <= -3 && dp <= -3) return { tone: "warn", label: "要改善", signals: sig };
    return { tone: "flat", label: "横ばい", signals: sig };
  }
  if (c.kind === "lunch") {
    const code = (c.stores || []).find(s => lunchFor(s));
    const le = code && lunchFor(code);
    if (le) {
      const m = lunchMetrics(le);
      const sig = [`${per1(m.dailyPerDay)}食/日`, `原価 ${pct(m.dailyCost)}`, `構成比 ${pct(m.lunchShare)}`];
      if (m.dailyCost > 0.32) return { tone: "warn", label: "原価高め", signals: sig };
      if (m.lunchShare >= 0.12) return { tone: "good", label: "定着", signals: sig };
      return { tone: "flat", label: "様子見", signals: sig };
    }
  }
  if (METRIC !== "cost_rate") {
    const sum = campaignSummary(c);
    if (sum.stores && sum.pct != null) {
      const sig = [`前年比 ${signed(sum.pct)}%`];
      if (sum.momPct != null) sig.push(`前月比 ${signed(sum.momPct)}%`);
      return sum.pct >= 0
        ? { tone: "good", label: "効果あり", signals: sig }
        : { tone: "warn", label: "要改善", signals: sig };
    }
  }
  return { tone: "wait", label: campStatus(c).k === "soon" ? "開始前" : "計測中", signals: [] };
}

// 近隣（同エリア）の同種類施策の実績を拾って比較材料にする
function campNeighbor(c) {
  const out = [], seen = new Set();
  for (const code of c.stores) {
    const region = store(code).region;
    const nb = (DATA.stores || []).filter(x => x.region === region && x.code !== code && hasData(x.code));
    for (const n of nb) {
      for (const cc of (DATA.campaigns || [])) {
        if (cc.id === c.id || cc.kind !== c.kind || !cc.stores.includes(n.code)) continue;
        const eff = campEffect(n.code, cc);
        if (eff && eff.pct != null && !seen.has(cc.id + n.code)) {
          seen.add(cc.id + n.code);
          out.push({ store: n.name || n.code, title: cc.title, pct: eff.pct });
        }
      }
    }
  }
  return out.sort((a, b) => b.pct - a.pct).slice(0, 3);
}

// 終了したのに振り返り(要因メモ)も次回提案も無い＝やりっぱなし
function needsReview(c) {
  return campStatus(c).k === "done" && !memoOf(c) && !(proposalFor(c.id) && proposalFor(c.id).next);
}

function renderReview(c) {
  const v = campVerdict(c);
  const memo = memoOf(c);
  const prop = proposalFor(c.id);
  const nb = campNeighbor(c);
  const sig = v.signals.length ? `<span class="rvsig">${v.signals.map(esc).join("　")}</span>` : "";
  const nbHtml = nb.length
    ? `<div class="rvnb"><b>近隣の同種施策</b>　${nb.map(x =>
        `${esc(x.store)}「${esc(x.title)}」<span class="${x.pct >= 0 ? "up" : "down"}">${signed(x.pct)}%</span>`).join("　")}</div>`
    : `<div class="rvnb muted">近隣（同エリア）に同種類の実績はまだありません。</div>`;
  const memoHtml = memo
    ? `<div class="cmemo">${escBr(memo)} <button class="goalbtn" data-memo="${c.id}" title="メモを編集">✎</button></div>`
    : `<div class="cmemo muted"><button class="goalbtn add" data-memo="${c.id}">＋ 要因メモ</button></div>`;
  const nextHtml = prop && prop.next
    ? `<div class="rvnext">${escBr(prop.next)}<div class="rvby">— ${esc(prop.by || "AI")}${prop.at ? "・" + esc(prop.at) : ""}</div></div>`
    : `<div class="rvnext muted">次回提案は未記入です。config/proposals.json に追記（AIに依頼も可）。</div>`;
  return `<section class="block">
    <div class="bhead"><h2>振り返り＆次回提案（PDCA）</h2>
      <span class="bnote">やりっぱなしにしない：実績→判定→次の一手${needsReview(c) ? "　⚠ 要振り返り" : ""}</span></div>
    <div class="panel">
      <div class="rvline">判定 <span class="rvbadge ${v.tone}">${v.label}</span> ${sig}</div>
      ${nbHtml}
      <div class="rvhd">要因（Check）</div>${memoHtml}
      <div class="rvhd">次回への一手（Act）</div>${nextHtml}
    </div></section>`;
}

// ── 年間販促（振り返り）─────────────────────────────────────────────────
// 年（2024〜今年）を選び、四半期別に施策を並べる。各行に実績＋直近差異、詳細へ。
// 施策1件の実績＋直近差異の1行（環境/ランチはワンライン、月次施策は実績＋前年比/前月比）。
function campReviewLine(c) {
  const hl = campHeadline(c);
  if (hl) return hl;
  if (METRIC === "cost_rate") return "";
  const sum = campaignSummary(c);
  if (sum.stores) {
    const yoy = sum.pct != null ? `<span class="${sum.pct >= 0 ? "up" : "down"}">前年比 ${signed(sum.pct)}%</span>` : "";
    const mom = sum.momPct != null ? `・<span class="${sum.momPct >= 0 ? "up" : "down"}">前月比 ${signed(sum.momPct)}%</span>` : "";
    return `<div class="ccmp"><span class="ccmp-i">実績 <b>${man(sum.cur)}円</b></span>` +
      (yoy ? `<span class="ccmp-i">${yoy}${mom}</span>` : "") +
      `<span class="ccmp-i sub">確定${sum.months}ヶ月・${sum.stores}店</span></div>`;
  }
  return "";
}
function renderYear() {
  const camps = DATA.campaigns || [];
  const yNow = new Date().getFullYear();
  const years = []; for (let y = 2024; y <= yNow; y++) years.push(y);
  const year = YEAR || yNow;
  const ys = `${year}-01-01`, ye = `${year}-12-31`;
  const inYear = c => c.start.slice(0, 10) <= ye && (c.end || c.start).slice(0, 10) >= ys;
  const list = camps.filter(inYear).sort((a, b) => a.start < b.start ? -1 : 1);
  // 四半期グループ（開始月ベース。前年から続く施策は「前年から継続」）
  const q = c => c.start.slice(0, 4) < String(year) ? 0 : Math.ceil(+c.start.slice(5, 7) / 3);
  const groups = [[0, "前年から継続"], [1, "1〜3月"], [2, "4〜6月"], [3, "7〜9月"], [4, "10〜12月"]];
  const kindCount = {};
  list.forEach(c => { kindCount[c.kind] = (kindCount[c.kind] || 0) + 1; });
  const kindSummary = Object.entries(kindCount)
    .map(([k, n]) => `<span class="ysum-i"><i style="background:${kindOf(k).color}"></i>${kindOf(k).label} ${n}</span>`).join("");
  const body = groups.map(([qi, label]) => {
    const gs = list.filter(c => q(c) === qi);
    if (!gs.length) return "";
    const items = gs.map(c => {
      const k = kindOf(c.kind), st = campStatus(c);
      const range = c.start === c.end ? c.start : `${c.start} 〜 ${c.end}`;
      const scope = c.scope_all ? "全店" : `${c.stores.length}店`;
      return `<li data-camp="${c.id}">
        <span class="kchip" style="--kc:${k.color}">${k.label}</span>
        <div class="cbody">
          <div class="ctitle">${c.title}<span class="tagx">${scope}</span><span class="cstat ${st.k}">${st.label}</span></div>
          ${c.note ? `<div class="cnote">${c.note}</div>` : ""}
          ${campReviewLine(c)}
          <div class="cgo">詳細を確認 →</div>
        </div>
        <span class="crange">${range}</span></li>`;
    }).join("");
    return `<div class="ygrp"><span>${label}</span><span class="sgrp-n">${gs.length}件</span></div><ul class="clist">${items}</ul>`;
  }).join("");
  const tabs = years.map(y => `<button class="ytab${y === year ? " on" : ""}" data-year="${y}">${y}年</button>`).join("");
  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button>
      <span class="sep">／</span><button class="linkbtn" data-view="campaigns">施策の効果</button></div>
    <section class="block">
      <div class="bhead"><h2>年間販促（振り返り）</h2>
        <span class="bnote">${year}年・${list.length}件　四半期別　実績＋直近差異　施策をタップで詳細</span></div>
      <div class="ytabs">${tabs}</div>
      ${kindSummary ? `<div class="ysum">${kindSummary}</div>` : ""}
      ${list.length ? body : `<div class="empty">${year}年の施策は登録されていません。config/schedule.yaml に追記すると、ここに年間で並びます（過去分は分かる範囲で足せます）。</div>`}
    </section>`;
}

// ── 販促ターゲット（横断）────────────────────────────────────────────────
// 全店・ブランド横断で部門構成を並べ、ランチ/飲み放題/コースの構成比が
// 同ブランドの平均より弱い店を「販促ターゲット」として自動抽出する。
// 近隣＝同ブランド（多くは同商圏）の実績を相手に置いて示す。
const DEPT_ORDER = ["コース", "ランチ", "アラカルト", "飲み放題", "食べ放題"];

function crossStores() {
  return (DATA.stores || [])
    .map(s => ({ s, d: deptFor(s.code) }))
    .filter(x => x.d && x.d.buckets && x.d.buckets.length)
    .map(({ s, d }) => {
      const share = {};
      d.buckets.forEach(b => (share[b.name] = b.share || 0));
      const top = ((DATA.products || {})[s.code] || [])[0];
      return { code: s.code, name: s.name, brand: s.brand, brand_name: s.brand_name, d, share, top };
    });
}

function crossByBrand() {
  const map = new Map();
  crossStores().forEach(x => {
    if (!map.has(x.brand)) map.set(x.brand, { brand: x.brand, brand_name: x.brand_name, rows: [] });
    map.get(x.brand).rows.push(x);
  });
  // ブランド内は売上規模の大きい順
  return [...map.values()].map(g => {
    g.rows.sort((a, b) => (b.d.total_sales || 0) - (a.d.total_sales || 0));
    return g;
  }).sort((a, b) => b.rows.length - a.rows.length);
}

// ターゲット判定: 同ブランド平均に対し構成比が半分未満、かつ平均が意味のある水準。
const CROSS_DIMS = [
  { key: "ランチ", flag: "ランチ強化候補", why: "昼の集客と回転を作る余地" },
  { key: "飲み放題", flag: "宴会・飲み放題プッシュ候補", why: "宴会需要の取り込み余地" },
  { key: "コース", flag: "コース訴求候補", why: "客単価・宴会の底上げ余地" },
];

function crossTargets() {
  const out = [];
  crossByBrand().forEach(g => {
    if (g.rows.length < 2) return;  // 比較相手がいないブランドは平均比較しない
    CROSS_DIMS.forEach(dim => {
      const vals = g.rows.map(r => r.share[dim.key] || 0);
      const avg = vals.reduce((a, v) => a + v, 0) / vals.length;
      if (avg < 0.03) return;  // ブランド自体がその区分をほぼ持たないならスキップ
      g.rows.forEach(r => {
        const v = r.share[dim.key] || 0;
        if (v < avg * 0.5) {
          out.push({
            code: r.code, name: r.name, brand_name: r.brand_name,
            dim: dim.key, flag: dim.flag, why: dim.why,
            v, avg, gap: avg - v,
          });
        }
      });
    });
  });
  return out.sort((a, b) => b.gap - a.gap);
}

function renderCross() {
  const brands = crossByBrand();
  const nStores = crossStores().length;
  if (!nStores) {
    return `<div class="crumb"><button class="linkbtn" data-view="schedule">← 予定表</button></div>
      <section class="block"><div class="bhead"><h2>販促ターゲット（横断）</h2></div>
      <div class="panel"><p class="muted">部門データがまだありません。abc-store-ingest で店舗別ABCを取り込むと表示されます。</p></div></section>`;
  }
  const monthLbl = DATA.products_month ? `（${DATA.products_month}）` : "";

  // 部門構成マトリクス（ブランド別・積み上げバー）
  const bar = r => DEPT_ORDER.filter(n => (r.share[n] || 0) > 0.004).map(n => {
    const pct = Math.round((r.share[n] || 0) * 100);
    return `<span class="xseg" style="width:${(r.share[n] * 100).toFixed(1)}%;--dc:${DEPT_COLORS[n]}" title="${n} ${pct}%"></span>`;
  }).join("");
  const legend = DEPT_ORDER.map(n =>
    `<span class="xlg"><i style="--dc:${DEPT_COLORS[n]}"></i>${n}</span>`).join("");
  const brandBlocks = brands.map(g => {
    const rows = g.rows.map(r => {
      const shareTxt = DEPT_ORDER.filter(n => (r.share[n] || 0) >= 0.05)
        .map(n => `${n[0]}${Math.round(r.share[n] * 100)}`).join(" ");
      const topTxt = r.top ? `<span class="xtop" title="売れ筋1位">${esc(r.top.name)}</span>` : "";
      return `<li class="xrow" data-store="${r.code}">
        <span class="xname">${esc(r.name)}</span>
        <span class="xbar">${bar(r)}</span>
        <span class="xshare">${shareTxt}</span>
        ${topTxt}
        <span class="xsales">${yen(r.d.total_sales)}</span></li>`;
    }).join("");
    return `<div class="xbrand"><div class="xbh">${esc(g.brand_name || g.brand)}<span class="xbn">${g.rows.length}店</span></div>
      <ul class="xlist">${rows}</ul></div>`;
  }).join("");

  // 販促ターゲット自動抽出
  const targets = crossTargets();
  const tByStore = new Map();
  targets.forEach(t => {
    if (!tByStore.has(t.code)) tByStore.set(t.code, { code: t.code, name: t.name, brand_name: t.brand_name, items: [] });
    tByStore.get(t.code).items.push(t);
  });
  const tcards = [...tByStore.values()].map(x => {
    const chips = x.items.map(t =>
      `<span class="xtag" title="${t.why}">${t.flag}<b>${Math.round(t.v * 100)}%</b><small>平均${Math.round(t.avg * 100)}%</small></span>`).join("");
    return `<li class="xtcard" data-store="${x.code}">
      <div class="xtc-h"><b>${esc(x.name)}</b><span class="muted">${esc(x.brand_name)}</span></div>
      <div class="xtc-b">${chips}</div>
      <div class="cgo">店舗詳細で確認 →</div></li>`;
  }).join("");
  const targetBlock = targets.length
    ? `<ul class="xtlist">${tcards}</ul>`
    : `<p class="muted">同ブランド平均に対して大きく弱い区分は見つかりませんでした（バランス良好）。</p>`;

  return `
    <div class="crumb"><button class="linkbtn" data-view="schedule">← 予定表</button></div>
    <section class="block">
      <div class="bhead"><h2>販促ターゲット（横断）</h2>
        <span class="bnote">同ブランドの平均より弱い区分を自動抽出${monthLbl}　${nStores}店</span></div>
      <div class="panel">
        <div class="xlead">同ブランド（＝多くは同商圏）を相手に、<b>ランチ・飲み放題・コース</b>の構成比が
          平均の半分未満の店を「次にやると効く販促」として並べています。</div>
        ${targetBlock}
      </div>
    </section>
    <section class="block">
      <div class="bhead"><h2>部門構成マトリクス</h2><span class="bnote">${legend}</span></div>
      <div class="panel xmatrix">${brandBlocks}</div>
    </section>`;
}

// ── 制作物ギャラリー（config/creatives.yaml 由来）──────────────────────────
// この店に掛かる制作物（全店ものも含む）。掲出日の新しい順は export 側で済み。
const creativesFor = code => (DATA.creatives || []).filter(cr => cr.scope_all || cr.stores.includes(code));

function creativeCard(cr) {
  const k = kindOf(cr.kind);
  const meta = [
    cr.date || "",
    cr.campaign_title ? "施策: " + cr.campaign_title : "",
    cr.scope_all ? "全店" : cr.stores.length + "店",
  ].filter(Boolean).join("　·　");
  // PDFは同一ドメイン（Access内）/creatives/… から配信。新規タブで開く。
  return `<div class="ccard">
    <a class="cthumb" style="--kc:${k.color}" href="${cr.url}" target="_blank" rel="noopener" title="PDFを開く">
      <span class="cext">PDF</span></a>
    <div class="ccbody">
      <span class="kchip" style="--kc:${k.color}">${k.label}</span>
      <div class="cctitle">${cr.title}</div>
      <div class="ccmeta">${meta}</div>
      <a class="pdfbtn" href="${cr.url}" target="_blank" rel="noopener">PDFを開く ↗</a>
    </div>
  </div>`;
}

function renderGallery() {
  const all = DATA.creatives || [];
  const body = all.length
    ? `<div class="cgrid">${all.map(creativeCard).join("")}</div>`
    : `<div class="empty">まだ制作物が登録されていません。<br>
        PDF を <code>hansoku creatives-upload &lt;PDF&gt; --campaign &lt;施策id&gt;</code> でアップロードし、
        <code>config/creatives.yaml</code> に1行足すと、ここと各店の詳細に並びます。</div>`;
  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <section class="block">
      <div class="bhead"><h2>制作物ギャラリー</h2>
        <span class="bnote">チラシ・POP・メニュー等のPDF${all.length ? "　" + all.length + "件" : ""}</span></div>
      ${body}
    </section>`;
}

// ── 店舗詳細（販促×売上×近隣）───────────────────────────────────────────
// 店舗詳細トップの「販促の狙いどころ」チップ。横断分析でこの店が
// ターゲット判定された区分（同ブランド平均より弱い）を要点として出す。
function storeTargetChip(code) {
  const items = crossTargets().filter(t => t.code === code);
  if (!items.length) return "";
  const chips = items.map(t =>
    `<span class="stchip" title="この店 ${Math.round(t.v * 100)}%・同ブランド平均 ${Math.round(t.avg * 100)}%／${t.why}">${t.flag}</span>`).join("");
  return `<div class="stargets"><span class="stlbl">販促の狙いどころ</span>${chips}
    <button class="linkbtn stlink" data-view="cross">横断で見る →</button></div>`;
}

function renderStore(code) {
  const s = store(code);
  const months = DATA.months;
  const color = regionColor(s.region);
  const isRatio = METRIC === "cost_rate";
  const y = yoy(code);

  // 見出しKPI
  const total = periodTotal(code);
  const latest = latestConfirmed(code);
  // 予算対比（FW月別予算）。売上のときだけ、直近確定月の実績÷予算。
  const budKpi = (() => {
    if (METRIC !== "sales" || !latest) return "";
    const b = budgetAt(code, latest.m);
    if (typeof b !== "number" || !b) return "";
    const rate = latest.v / b * 100;
    return `<div class="kpi"><div class="lbl">予算対比（${latest.m}）</div>
        <div class="big ${rate >= 100 ? "up" : "down"}">${rate.toFixed(0)}%</div>
        <div class="delta">予算 ${man(b)} → 実績 ${man(latest.v)}</div></div>`;
  })();
  // 前月比（直近確定月とその前月を比べる）
  const mom = (() => {
    if (isRatio || !latest) return null;
    const pm = addMonth(latest.m, -1);
    const pv = valueAt(code, pm);
    if (typeof pv !== "number" || !pv) return null;
    return { pm, prev: pv, pct: (latest.v / pv - 1) * 100 };
  })();
  // 集客（客数）。売上指標のときだけ出す（原価率などと並べても意味が薄いため）
  const covKpi = (() => {
    if (METRIC !== "sales") return "";
    const cs = coversSummary(code);
    if (!cs) return "";
    const yc = cs.yoyPct != null
      ? `<span class="${cs.yoyPct >= 0 ? "up" : "down"}">前年比 ${signed(cs.yoyPct)}%</span>`
      : "前年比 ―";
    return `<div class="kpi"><div class="lbl">集客（期間合計客数）</div>
        <div class="big">${nin(cs.total)}</div>
        <div class="delta">${yc}${cs.last ? `（直近 ${cs.last.m}）` : ""}</div></div>`;
  })();
  const kpis = `
    <div class="kpis">
      <div class="kpi"><div class="lbl">期間合計（${METRIC_LABELS[METRIC]}）</div>
        <div class="big">${isRatio ? "―" : man(total)}<span class="unit">${isRatio ? "" : "円"}</span></div></div>
      <div class="kpi"><div class="lbl">直近確定月${latest ? "（" + latest.m + "）" : ""}</div>
        <div class="big">${latest && latest.v != null ? (isRatio ? pct(latest.v) : yen(latest.v)) : "―"}</div></div>
      <div class="kpi"><div class="lbl">前年同月比</div>
        <div class="big ${y ? (y.pct >= 0 ? "up" : "down") : ""}">${y ? signed(y.pct) + "%" : "―"}</div>
        <div class="delta">${y ? `${man(y.prev)} → ${man(y.cur)}` : "前年データなし"}</div></div>
      <div class="kpi"><div class="lbl">前月比</div>
        <div class="big ${mom ? (mom.pct >= 0 ? "up" : "down") : ""}">${mom ? signed(mom.pct) + "%" : "―"}</div>
        <div class="delta">${mom ? `${man(mom.prev)} → ${man(latest.v)}` : "前月データなし"}</div></div>
      ${budKpi}
      ${covKpi}
    </div>`;

  // この店の施策（config/schedule.yaml 由来）
  const myCamps = (DATA.campaigns || [])
    .filter(c => c.stores.includes(code))
    .sort((a, b) => a.start < b.start ? -1 : 1);

  // 自店の売上推移。施策期間はグラフに帯として重ねる
  const budNote = (METRIC === "sales" && hasBudget(code)) ? "　破線は月予算（FW）。" : "";
  const own = `
    <div class="panel"><div class="chartwrap">${singleLine(code, months, color, myCamps)}</div>
      <figcaption>当月は締め前の暫定値（点線）。色帯は施策期間です。${budNote}</figcaption>
    </div>`;

  // 近隣（同エリア）比較
  const neigh = (s.neighbors || []).filter(hasData);
  let neighBlock = "";
  if (neigh.length) {
    const codes = [code, ...neigh];
    neighBlock = `
      <section class="block">
        <div class="bhead"><h2><span class="dot" style="background:${color}"></span>近隣比較</h2>
          <span class="bnote">${s.region}エリア ${codes.length}店の${METRIC_LABELS[METRIC]}（自店を濃く）</span></div>
        <div class="panel"><div class="chartwrap">${multiLine(codes, months, s.region, code)}</div>
          <div class="lg">${legend(codes, s.region, code)}</div>
        </div>
      </section>`;
  } else {
    neighBlock = `
      <section class="block">
        <div class="bhead"><h2>近隣比較</h2></div>
        <div class="empty">${s.region}エリアには他に実績のある店舗がありません。</div>
      </section>`;
  }

  // この店の制作物（PDF）。1件以上あるときだけ節を出す
  const myCreatives = creativesFor(code);
  const myCreativesBlock = myCreatives.length
    ? `<section class="block">
        <div class="bhead"><h2>この店の制作物</h2><span class="bnote">${myCreatives.length}件</span></div>
        <div class="cgrid">${myCreatives.map(creativeCard).join("")}</div>
      </section>`
    : "";

  // この店の販促（施策の一覧）。実施中→予定→終了 の順、同状態内は日付順
  const STATUS_ORDER = { live: 0, soon: 1, done: 2 };
  const sortedCamps = myCamps.slice().sort((a, b) => {
    const d = STATUS_ORDER[campStatus(a).k] - STATUS_ORDER[campStatus(b).k];
    return d !== 0 ? d : (a.start < b.start ? -1 : 1);
  });
  const promoBlock = sortedCamps.length
    ? `<ul class="clist">${sortedCamps.map(c => {
        const k = kindOf(c.kind);
        const range = c.start === c.end ? c.start : `${c.start} 〜 ${c.end}`;
        const st = campStatus(c);
        const eff = campEffect(code, c);
        let effHtml = "";
        if (eff) {
          const cmp = eff.pct != null
            ? `<span class="${eff.pct >= 0 ? "up" : "down"}">前年比 ${signed(eff.pct)}%</span>（前年 ${man(eff.prev)}円）`
            : "前年データなし";
          const mom = eff.momPct != null
            ? `・<span class="${eff.momPct >= 0 ? "up" : "down"}">前月比 ${signed(eff.momPct)}%</span>` : "";
          effHtml = `<div class="ceff">期間中の${METRIC_LABELS[METRIC]}（確定${eff.months}ヶ月）<b>${man(eff.cur)}円</b>・${cmp}${mom}</div>`;
          // 集客（客数）の効果。売上表示のときだけ、同じ期間の客数を前年比・前月比で添える
          if (METRIC === "sales") {
            const cov = campCovers(code, c);
            if (cov) {
              const cy = cov.pct != null
                ? `<span class="${cov.pct >= 0 ? "up" : "down"}">前年比 ${signed(cov.pct)}%</span>` : "前年 ―";
              const cmom = cov.momPct != null
                ? `・<span class="${cov.momPct >= 0 ? "up" : "down"}">前月比 ${signed(cov.momPct)}%</span>` : "";
              effHtml += `<div class="ceff sub2">期間中の集客 <b>${nin(cov.cur)}</b>・${cy}${cmom}</div>`;
            }
          }
        } else if (st.k !== "soon" && METRIC !== "cost_rate") {
          effHtml = `<div class="ceff muted">確定した月の売上が出たら、前年同月比を表示します（月単位で集計）。</div>`;
        }
        // 目標対比（アプリ内で入力した目標／schedule.yaml の目標）
        const tgt = targetOf(c);
        let goalHtml;
        if (tgt != null) {
          const actual = eff ? eff.cur : null;
          const rate = (actual != null && tgt > 0) ? actual / tgt * 100 : null;
          const prog = rate != null
            ? ` ・ 実績(確定) ${man(actual)}円 ・ <span class="${rate >= 100 ? "up" : "down"}">達成 ${rate.toFixed(0)}%</span>`
            : ` ・ <span class="sub">実績は確定月が出てから</span>`;
          goalHtml = `<div class="cgoal">目標 <b>${man(tgt)}円</b>${prog} <button class="goalbtn" data-goal="${c.id}" title="目標を編集">✎</button></div>`;
        } else {
          goalHtml = `<div class="cgoal muted"><button class="goalbtn add" data-goal="${c.id}">＋ 目標を入力</button></div>`;
        }
        // 要因メモ（アプリ内で入力・共有）
        const memo = memoOf(c);
        const memoHtml = memo
          ? `<div class="cmemo">${escBr(memo)} <button class="goalbtn" data-memo="${c.id}" title="メモを編集">✎</button></div>`
          : `<div class="cmemo muted"><button class="goalbtn add" data-memo="${c.id}">＋ 要因メモ</button></div>`;
        return `<li data-camp="${c.id}">
          <span class="kchip" style="--kc:${k.color}">${k.label}</span>
          <div class="cbody">
            <div class="ctitle">${c.title}${c.scope_all ? '<span class="tagx">全店</span>' : ""}<span class="cstat ${st.k}">${st.label}</span></div>
            ${c.note ? `<div class="cnote">${c.note}</div>` : ""}
            ${effHtml}
            ${campHeadline(c)}
            ${goalHtml}
            ${memoHtml}
            <div class="cgo">詳細を確認 →</div>
          </div>
          <span class="crange">${range}</span>
        </li>`;
      }).join("")}</ul>`
    : `<div class="empty">この店の施策はまだ登録されていません。config/schedule.yaml に追記すると、ここと上の売上グラフに並びます。</div>`;

  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <section class="block">
      <div class="shd"><span class="rtag" style="--rc:${color}">${s.region}</span>
        <h2 class="sname">${s.name}</h2>${s.shared_facility ? '<span class="tagx">共営施設</span>' : ""}</div>
      ${kpis}
      ${storeTargetChip(code)}
    </section>
    ${renderProfitability(code)}
    <section class="block">
      <div class="bhead"><h2>この店の販促</h2>
        <span class="bnote">${myCamps.length}件</span></div>
      ${promoBlock}
    </section>
    ${myCreativesBlock}
    <section class="block">
      <div class="bhead"><h2>売上推移</h2><span class="bnote">${METRIC_LABELS[METRIC]}</span></div>
      ${own}
    </section>
    ${renderHourly(code)}
    ${renderLunchCard(code)}
    ${renderDepartments(code)}
    ${renderProducts(code)}
    ${neighBlock}
  `;
}

// ── 部門構成（FW ABC・分類=部門）────────────────────────────────────────────
// 店ごとにバラバラな部門名を コース/ランチ/アラカルト/飲み放題/食べ放題 に寄せて
// 構成比で見せる。生の部門は折りたたみに残す。abc-store-ingest で店を1つずつ焼くと入る。
function deptFor(code) {
  return (DATA.departments || {})[code] || null;
}
const DEPT_COLORS = {
  "コース": "var(--brand-bounenkai, #b5651d)",
  "ランチ": "var(--brand-lunch, #2e8b57)",
  "アラカルト": "var(--accent)",
  "飲み放題": "#4a7fb5",
  "食べ放題": "#c06a9e",
  "その他": "var(--ink-3)",
};
function renderDepartments(code) {
  const d = deptFor(code);
  if (!d || !d.buckets || !d.buckets.length) return "";
  const total = d.total_sales || 1;
  const rows = d.buckets.map(b => {
    const pct = Math.round((b.share || 0) * 100);
    const w = Math.max(2, pct);
    const cr = b.cost_rate != null ? `<span class="dcr">原価${b.cost_rate}%</span>` : "";
    const q = b.qty ? `<span class="dqty">${b.qty.toLocaleString("ja-JP")}点</span>` : "";
    let sub = "";
    if (b.name === "アラカルト" && d.alacarte) {
      const f = d.alacarte["フード"] || 0, dr = d.alacarte["ドリンク"] || 0;
      if (f || dr) sub = `<div class="dsub">フード ${yen(f)}／ドリンク ${yen(dr)}</div>`;
    }
    return `<li class="drow">
      <span class="dname" style="--dc:${DEPT_COLORS[b.name] || "var(--ink-3)"}">${esc(b.name)}</span>
      <span class="dbar"><span class="dfill" style="width:${w}%;--dc:${DEPT_COLORS[b.name] || "var(--ink-3)"}"></span></span>
      <span class="dpct">${pct}%</span>
      <span class="dsales">${yen(b.sales)}</span>
      ${q}${cr}${sub}</li>`;
  }).join("");
  const raw = (d.raw || []).map(r => {
    const cr = r.cost_rate != null ? ` 原価${r.cost_rate}%` : "";
    return `<li><span class="rbk" style="--dc:${DEPT_COLORS[r.bucket] || "var(--ink-3)"}">${esc(r.bucket)}</span>
      ${esc(r.name)}<span class="rval">${yen(r.sales)}・${(r.qty || 0).toLocaleString("ja-JP")}点${cr}</span></li>`;
  }).join("");
  const monthLbl = DATA.products_month ? `（${DATA.products_month}）` : "";
  const rawBlock = raw
    ? `<details class="draw"><summary>FWの生の部門 ${d.raw.length}件を見る</summary>
         <ul class="drawlist">${raw}</ul></details>`
    : "";
  return `
    <section class="block">
      <div class="bhead"><h2>部門構成</h2>
        <span class="bnote">コース/ランチ/アラカルト/飲み放題/食べ放題${monthLbl}　合計 ${yen(total)}</span></div>
      <div class="panel"><ul class="dlist">${rows}</ul>${rawBlock}</div>
    </section>`;
}

// ── 収益性・客単価（既存データから算出）───────────────────────────────────
// 店長会資料DL（空テンプレで取込不可）の代替。既にある売上/客数/原価率/部門から
// 客単価・粗利率・部門別原価率を1枚にまとめる。値が無い指標は自然に省く。
const salesAt = (code, m) => ((DATA.monthly[code] || {})[m] || {}).sales;
// 直近の確定月で、指定アクセサが数値を返す最新月を探す
function latestWith(code, fn) {
  for (let i = DATA.months.length - 1; i >= 0; i--) {
    const m = DATA.months[i];
    if (m >= CURRENT_MONTH) continue;
    const v = fn(code, m);
    if (typeof v === "number" && !Number.isNaN(v)) return { m, v };
  }
  return null;
}
function renderProfitability(code) {
  const crAt = (c, m) => (DATA.cost_rate[c] || {})[m];   // 分数(0-1)
  const ls = latestWith(code, salesAt);
  const lcr = latestWith(code, crAt);
  const d = deptFor(code);
  const deptCr = d && d.buckets ? d.buckets.filter(b => b.cost_rate != null) : [];
  if (!ls && !lcr && !deptCr.length) return "";      // 出せる材料が無ければ節ごと省く

  const cards = [];
  // 客単価（売上÷客数）＋前年同月比
  if (ls) {
    const cov = coversAt(code, ls.m);
    if (typeof cov === "number" && cov > 0) {
      const kt = ls.v / cov;
      const [y, mo] = ls.m.split("-");
      const pm = `${+y - 1}-${mo}`;
      const ps = salesAt(code, pm), pc = coversAt(code, pm);
      let delta = "前年比 ―";
      if (typeof ps === "number" && typeof pc === "number" && pc > 0) {
        const p = (kt / (ps / pc) - 1) * 100;
        delta = `<span class="${p >= 0 ? "up" : "down"}">前年比 ${signed(p)}%</span>`;
      }
      cards.push(`<div class="kpi"><div class="lbl">客単価（${ls.m}）</div>
        <div class="big">${yen(kt)}</div>
        <div class="delta">${delta}・${man(ls.v)}円÷${nin(cov)}</div></div>`);
    }
  }
  // 原価率 → 粗利率
  if (lcr) {
    cards.push(`<div class="kpi"><div class="lbl">原価率（${lcr.m}）</div>
      <div class="big ${lcr.v <= 0.35 ? "up" : "down"}">${pct(lcr.v)}</div>
      <div class="delta">粗利率 ${pct(1 - lcr.v)}</div></div>`);
  }
  // 粗利（推計）＝売上×粗利率。売上と原価率がそろう最新月で
  if (ls && lcr) {
    const grM = latestWith(code, (c, m) =>
      (typeof salesAt(c, m) === "number" && typeof crAt(c, m) === "number") ? salesAt(c, m) : undefined);
    if (grM) {
      const cr = crAt(code, grM.m);
      cards.push(`<div class="kpi"><div class="lbl">粗利（推計・${grM.m}）</div>
        <div class="big">${man(grM.v * (1 - cr))}<span class="unit">円</span></div>
        <div class="delta">売上 ${man(grM.v)}円 × 粗利率 ${pct(1 - cr)}</div></div>`);
    }
  }
  if (!cards.length && !deptCr.length) return "";
  // 部門別 原価率→粗利率（bucket.cost_rate は % 単位）
  const deptChips = deptCr.length
    ? `<div class="prof-depts">${deptCr
        .slice()
        .sort((a, b) => b.sales - a.sales)
        .map(b => `<span class="pchip" style="--dc:${DEPT_COLORS[b.name] || "var(--ink-3)"}">
          <i>${esc(b.name)}</i>原価${b.cost_rate}%<b>→粗利${(100 - b.cost_rate).toFixed(0)}%</b></span>`)
        .join("")}</div>`
    : "";
  return `
    <section class="block">
      <div class="bhead"><h2>収益性・客単価</h2>
        <span class="bnote">既存データ（売上・客数・ABC原価率）から算出</span></div>
      <div class="panel">
        <div class="kpis">${cards.join("")}</div>
        ${deptChips}
      </div>
    </section>`;
}

// 売れ筋商品（FW ABC分析）。売上上位を棒つきで並べ、FWのABCランクを添える。
// おすすめ料理の検討材料（何が出ているか・ランクA/B/C）。
function renderProducts(code) {
  const items = (DATA.products || {})[code];
  if (!items || !items.length) return "";
  const max = Math.max(1, ...items.map(p => p.sales));
  const rankColor = r => r === "A" ? "var(--good-ink)" : r === "B" ? "var(--accent)" : "var(--ink-3)";
  const rows = items.map((p, i) => {
    const pct = Math.max(3, Math.round(p.sales / max * 100));
    const rank = p.rank
      ? `<span class="prank" style="--pc:${rankColor(p.rank)}">${esc(p.rank)}</span>` : "";
    return `<li class="prow">
      <span class="pno">${i + 1}</span>
      <span class="pname">${esc(p.name)}</span>${rank}
      <span class="pbar"><span class="pfill" style="width:${pct}%"></span></span>
      <span class="psales">${yen(p.sales)}</span></li>`;
  }).join("");
  const monthLbl = DATA.products_month ? `（${DATA.products_month}）` : "";
  return `
    <section class="block">
      <div class="bhead"><h2>売れ筋商品（ABC）</h2>
        <span class="bnote">売上上位${items.length}品${monthLbl}　ランクはFWのABC</span></div>
      <div class="panel"><ul class="plist">${rows}</ul></div>
    </section>`;
}

// ── 新ランチ効果（FW ABC・部門ランチ）──────────────────────────────────────
// config/lunch_analysis.json（店舗別）。出品数＝本体(定食)のみ。
// トッピングは「付帯率（一人当たり出品数）」と「1食あたり売上」で見る。
function lunchFor(code) {
  return (DATA.lunch || []).find(x => x.store_code === code) || null;
}
function lunchMetrics(e) {
  const sum = (arr, f) => (arr || []).reduce((a, x) => a + f(x), 0);
  const dm = e.daily_meals || [], tp = e.toppings || [];
  const dailyQty = sum(dm, x => x.qty), dailySales = sum(dm, x => x.sales);
  const dailyCost = dailySales ? sum(dm, x => x.sales * x.cost_rate) / dailySales : 0;
  const to = e.period ? e.period.to : null;
  const days = to ? Math.max(1, daysBetween(e.start_date || e.period.from, to) + 1) : 1;
  const topQty = sum(tp, x => x.qty), topSales = sum(tp, x => x.sales);
  const lunchSales = (e.lunch && e.lunch.sales) || 0;
  return {
    days, dailyQty, dailySales, dailyCost,
    dailyPerDay: dailyQty / days, dailyPerDaySales: dailySales / days,
    avgCheck: dailyQty ? dailySales / dailyQty : 0,
    topQty, topSales,
    topAttach: dailyQty ? topQty / dailyQty : 0,
    topPerMeal: dailyQty ? topSales / dailyQty : 0,
    lunchSales, lunchCost: (e.lunch && e.lunch.cost_rate) || 0,
    lunchShare: e.store_sales ? lunchSales / e.store_sales : 0,
  };
}
const per1 = n => (Math.round(n * 10) / 10).toLocaleString("ja-JP");

// 店舗詳細に出す小カード（見出し＋主要KPI＋「詳細（一覧）→」）
function renderLunchCard(code) {
  const e = lunchFor(code);
  if (!e) return "";
  const m = lunchMetrics(e);
  return `
    <section class="block">
      <div class="bhead"><h2>新ランチ効果</h2>
        <span class="bnote">${esc(e.menu_title || "日替わりランチ")}・${esc(e.start_date || "")}〜</span></div>
      <div class="panel lsum">
        <div class="lkpi"><div class="lkv">${per1(m.dailyPerDay)}<small>食/日</small></div>
          <div class="lkl">日替わり出品数<span>計${m.dailyQty}食</span></div></div>
        <div class="lkpi"><div class="lkv">${yen(m.dailyPerDaySales)}<small>/日</small></div>
          <div class="lkl">日替わり売上<span>計${man(m.dailySales)}</span></div></div>
        <div class="lkpi"><div class="lkv">${pct(m.dailyCost)}</div>
          <div class="lkl">本体 原価率</div></div>
        <div class="lkpi"><div class="lkv">${pct(m.topAttach)}</div>
          <div class="lkl">トッピング付帯<span>+${yen(m.topPerMeal)}/食</span></div></div>
        <button class="linkbtn lmore" data-lunch="${esc(code)}">詳細（一覧）→</button>
      </div>
    </section>`;
}

// 詳細＝一覧画面。日替わり本体・トッピング・ランチ全品を見やすく並べる。
function renderLunch(code) {
  const e = lunchFor(code);
  if (!e) return `<div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <div class="empty">ランチ分析データがありません。</div>`;
  const m = lunchMetrics(e);
  const store = (DATA.stores || []).find(s => s.store_code === code);
  const nm = (store && store.store_name) || e.store_name || code;
  const pr = e.period || {};

  // 日替わり本体2品（強調）
  const dm = (e.daily_meals || []).slice().sort((a, b) => b.sales - a.sales);
  const maxM = Math.max(1, ...dm.map(p => p.qty));
  const dailyRows = dm.map(p => `
    <li class="lrow">
      <span class="lname">${esc(p.name)}<span class="lnew">NEW</span></span>
      <span class="lbar"><span class="lfill" style="width:${Math.max(4, Math.round(p.qty / maxM * 100))}%"></span></span>
      <span class="lqty">${p.qty}<small>食</small></span>
      <span class="lval">${yen(p.sales)}</span>
      <span class="lcr">原${pct(p.cost_rate)}</span>
    </li>`).join("");

  // トッピング（付帯率・1食あたり売上）
  const tp = (e.toppings || []).slice().sort((a, b) => b.qty - a.qty);
  const topRows = tp.map(p => `
    <li class="lrow toprow">
      <span class="lname">${esc(p.name)}</span>
      <span class="lqty">${p.qty}<small>個</small></span>
      <span class="lattach">${pct(m.dailyQty ? p.qty / m.dailyQty : 0)}<small>付帯</small></span>
      <span class="lval">${yen(p.sales)}</span>
    </li>`).join("");

  // ランチ全品（定食）の一覧＝人気順。新メニューにバッジ。
  const meals = (e.menu || []).filter(x => x.type === "meal").slice().sort((a, b) => b.qty - a.qty);
  const maxAll = Math.max(1, ...meals.map(p => p.qty));
  const rankColor = r => r === "A" ? "var(--good-ink)" : r === "B" ? "var(--accent)" : "var(--ink-3)";
  const mealRows = meals.map((p, i) => `
    <li class="lrow${p.new ? " isnew" : ""}">
      <span class="lno">${i + 1}</span>
      <span class="lname">${esc(p.name)}${p.new ? '<span class="lnew">NEW</span>' : ""}
        ${p.rank ? `<span class="lrank" style="--pc:${rankColor(p.rank)}">${esc(p.rank)}</span>` : ""}</span>
      <span class="lbar"><span class="lfill" style="width:${Math.max(4, Math.round(p.qty / maxAll * 100))}%"></span></span>
      <span class="lqty">${p.qty}<small>食</small></span>
      <span class="lval">${yen(p.sales)}</span>
      <span class="lcr">原${pct(p.cost_rate)}</span>
    </li>`).join("");
  const rice = (e.menu || []).filter(x => x.type === "rice");
  const riceNote = rice.length
    ? `<div class="lnote">ご飯：${rice.map(r => `${esc(r.name)} ${r.qty}回`).join("・")}（無料・売上0）</div>` : "";

  // 前年・直前の対比
  const cmp = e.compare || {};
  let cmpNote;
  if (cmp.unavailable) {
    const sm = (cmp.store_monthly || []).map(r =>
      `<div><b>${man(r.sales)}</b><span>${esc(r.ym)}${r.partial ? "（途中）" : ""}</span></div>`).join("");
    cmpNote = `
      <section class="block">
        <div class="bhead"><h2>前年比・新旧比較</h2><span class="bnote">FW ABCの制約</span></div>
        <div class="panel">
          <div class="lnote">${esc(cmp.reason || "過去月のランチ明細は取得できません。")}</div>
          ${sm ? `<div class="bnote" style="margin-top:10px">参考：店全体の月次売上（ランチ単位ではありません）</div>
            <div class="lstat" style="border:0;margin:6px 0 0">${sm}</div>` : ""}
        </div>
      </section>`;
  } else if (cmp.prev_year || cmp.prev_month) {
    cmpNote = "";  // 数値が入ったら対比表（将来、取得できた店で）
  } else {
    cmpNote = `<div class="lnote muted">前年・直前（旧ランチ）の対比は取得中です。</div>`;
  }

  return `
    <div class="crumbs">
      <button class="linkbtn" data-store="${esc(code)}">← ${esc(nm)}の詳細</button>
      <button class="linkbtn" data-view="schedule">全店スケジュール</button>
    </div>
    <header class="lhead">
      <h1>${esc(nm)}　新ランチ効果</h1>
      <div class="lsub">${esc(e.menu_title || "日替わりランチ")}　開始 ${esc(e.start_date || "?")}
        期間 ${esc(pr.from || "")}〜${esc(pr.to || "")}（${m.days}日）</div>
    </header>

    <section class="block">
      <div class="bhead"><h2>日替わりランチ（本体）</h2>
        <span class="bnote">出品数＝本体のみ・トッピングは別集計</span></div>
      <div class="panel">
        <div class="lstat">
          <div><b>${per1(m.dailyPerDay)}</b><span>食/日（計${m.dailyQty}食）</span></div>
          <div><b>${yen(m.dailyPerDaySales)}</b><span>/日（計${man(m.dailySales)}）</span></div>
          <div><b>${yen(m.avgCheck)}</b><span>1食A/V（客単価）</span></div>
          <div><b>${pct(m.dailyCost)}</b><span>原価率</span></div>
        </div>
        <ul class="llist">${dailyRows}</ul>
      </div>
    </section>

    <section class="block">
      <div class="bhead"><h2>トッピング（売上底上げ）</h2>
        <span class="bnote">出品数には含めない。付帯率＝一人（1食）当たり</span></div>
      <div class="panel">
        <div class="lstat">
          <div><b>${pct(m.topAttach)}</b><span>付帯率（${m.topQty}個/${m.dailyQty}食）</span></div>
          <div><b>+${yen(m.topPerMeal)}</b><span>1食あたり売上</span></div>
          <div><b>${man(m.topSales)}</b><span>トッピング売上計</span></div>
        </div>
        <ul class="llist">${topRows || '<li class="lnote">トッピングなし</li>'}</ul>
      </div>
    </section>

    <section class="block">
      <div class="bhead"><h2>ランチ全品 一覧（人気順）</h2>
        <span class="bnote">ランチ部門 ${man(m.lunchSales)}・原価${pct(m.lunchCost)}・店内構成比 ${pct(m.lunchShare)}</span></div>
      <div class="panel"><ul class="llist">${mealRows}</ul>${riceNote}</div>
    </section>
    ${cmpNote}`;
}

// 時間帯別 売上・集客（FW時間帯別売上）。棒＝売上、ピーク時間帯を強調。
// 時間帯別販促（ランチ強化・アイドルタイム対策など）の検討材料。
function renderHourly(code) {
  const per = (DATA.hourly || {})[code];
  if (!per) return "";
  const hours = Object.keys(per).map(Number).sort((a, b) => a - b);
  if (!hours.length) return "";
  const salesAt = h => (per[String(h)] || {}).sales || 0;
  const coversAt = h => (per[String(h)] || {}).covers || 0;
  const max = Math.max(1, ...hours.map(salesAt));
  const peak = hours.reduce((p, h) => (salesAt(h) > salesAt(p) ? h : p), hours[0]);
  const totSales = hours.reduce((a, h) => a + salesAt(h), 0);
  const bars = hours.map(h => {
    const s = salesAt(h), c = coversAt(h);
    const pct = Math.max(2, Math.round(s / max * 100));
    return `<div class="hbar${h === peak ? " peak" : ""}" title="${h}時台　売上 ${yen(s)}・客数 ${c}人">
      <div class="hcol"><div class="hfill" style="height:${pct}%"></div></div>
      <div class="hlbl">${h}</div></div>`;
  }).join("");
  const monthLbl = DATA.hourly_month ? `（${DATA.hourly_month}）` : "";
  return `
    <section class="block">
      <div class="bhead"><h2>時間帯別 売上・集客</h2>
        <span class="bnote">1時間ごとの売上${monthLbl}　ピーク ${peak}時台　棒にカーソルで客数</span></div>
      <div class="panel">
        <div class="hbars">${bars}</div>
        <figcaption>合計 ${man(totSales)}円／日中の山とアイドルタイムを見て、時間帯別の販促を検討できます。</figcaption>
      </div>
    </section>`;
}

// ── 集約ページ（実施中の施策サマリ ＋ エリア別売上表）─────────────────────
// 全店で今日実施中の施策を、前年比の良い順に並べる
function activeCampaignRows() {
  const rows = [];
  for (const c of (DATA.campaigns || [])) {
    if (campStatus(c).k !== "live") continue;
    for (const code of c.stores) {
      if (!hasData(code)) continue;
      rows.push({ code, c, eff: campEffect(code, c) });
    }
  }
  rows.sort((a, b) => {
    const pa = a.eff && a.eff.pct != null ? a.eff.pct : -Infinity;
    const pb = b.eff && b.eff.pct != null ? b.eff.pct : -Infinity;
    return pb - pa;
  });
  return rows;
}

function liveSummary() {
  const live = activeCampaignRows();
  if (!live.length) {
    return `<section class="block">
      <div class="bhead"><h2>実施中の施策</h2></div>
      <div class="empty">今日時点で実施中の施策はありません。</div>
    </section>`;
  }
  const withEff = live.filter(r => r.eff && r.eff.pct != null);
  const pos = withEff.filter(r => r.eff.pct >= 0).length;
  const withGoal = live.filter(r => targetOf(r.c) != null).length;
  const body = live.map(({ code, c, eff }) => {
    const k = kindOf(c.kind);
    const cell = eff && eff.pct != null
      ? `<span class="${eff.pct >= 0 ? "up" : "down"}">${signed(eff.pct)}%</span> <span class="sub">${eff.months}ヶ月</span>`
      : `<span class="sub">―</span>`;
    const tgt = targetOf(c);
    const rate = (tgt && eff && eff.cur) ? eff.cur / tgt * 100 : null;
    const goalCell = tgt == null
      ? `<span class="sub">未設定</span>`
      : `${man(tgt)}<span class="sub"> / </span>${rate != null ? `<span class="${rate >= 100 ? "up" : "down"}">${rate.toFixed(0)}%</span>` : `<span class="sub">―</span>`}`;
    return `<tr data-store="${code}">
      <td>${storeName(code)}</td>
      <td class="nowrap"><span class="kdot" style="background:${k.color}"></span>${k.label}</td>
      <td>${c.title}</td>
      <td class="sub nowrap">${c.start}〜${c.end}</td>
      <td class="num">${cell}</td>
      <td class="num">${goalCell}</td></tr>`;
  }).join("");
  return `<section class="block">
    <div class="bhead"><h2>実施中の施策</h2>
      <span class="bnote">${live.length}件　前年比プラス ${pos}/${withEff.length}　目標設定 ${withGoal}/${live.length}（確定月・月単位の概算）</span></div>
    <div class="panel"><div class="chartwrap">
      <table class="efftbl">
        <thead><tr><th>店舗</th><th>種類</th><th>施策</th><th>期間</th><th class="num">前年比（確定分）</th><th class="num">目標／達成</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div></div>
  </section>`;
}

// ── エリア・全店の一覧（控えめ・下位ページ）──────────────────────────────
// 予算達成ランキング（直近確定月・実績÷予算）。売上のときだけ。
function budgetRanking() {
  if (METRIC !== "sales") return "";
  const rows = DATA.stores.map(s => ({ s, br: budgetRate(s.code) }))
    .filter(x => x.br).sort((a, b) => b.br.rate - a.br.rate);
  if (!rows.length) return "";
  const max = Math.max(...rows.map(x => x.br.rate), 120);
  const at = p => (p / max * 100).toFixed(1) + "%";
  const bars = rows.map(({ s, br }) => {
    const cls = br.rate >= 100 ? "up" : "down";
    return `<li>
      <button class="brk-name linkbtn" data-store="${s.code}">${s.name}</button>
      <div class="brk-bar"><span class="brk-fill ${cls}" style="width:${at(br.rate)}"></span>
        <span class="brk-100" style="left:${at(100)}"></span></div>
      <span class="brk-val ${cls}">${br.rate.toFixed(0)}%</span>
    </li>`;
  }).join("");
  return `<section class="block">
    <div class="bhead"><h2>予算達成ランキング</h2>
      <span class="bnote">直近確定月・実績÷予算（FW月別予算）・${rows.length}店　点線=100%</span></div>
    <ul class="brk">${bars}</ul>
  </section>`;
}

// ブランド比較（直近確定月の売上合計・前年比）。売上のときだけ。
function brandCompare() {
  if (METRIC !== "sales") return "";
  const by = {};
  for (const s of DATA.stores) {
    const lc = latestConfirmed(s.code);
    if (!lc) continue;
    const key = s.brand_name || s.brand || "その他";
    const b = by[key] || (by[key] = { name: key, stores: 0, cur: 0, prev: 0, prevOk: true });
    b.stores += 1; b.cur += lc.v;
    const [y, mo] = lc.m.split("-");
    const pv = valueAt(s.code, `${+y - 1}-${mo}`);
    if (typeof pv === "number") b.prev += pv; else b.prevOk = false;
  }
  const rows = Object.values(by).sort((a, b) => b.cur - a.cur);
  if (rows.length < 2) return "";
  const body = rows.map(b => {
    const pct = (b.prevOk && b.prev) ? (b.cur / b.prev - 1) * 100 : null;
    const yoy = pct != null
      ? `<span class="${pct >= 0 ? "up" : "down"}">${signed(pct)}%</span>`
      : "―";
    return `<tr><td class="rgn">${b.name}</td><td class="num">${b.stores}店</td>
      <td class="num">${yen(b.cur)}</td><td class="num">${yoy}</td></tr>`;
  }).join("");
  return `<section class="block">
    <div class="bhead"><h2>ブランド比較</h2>
      <span class="bnote">直近確定月・売上合計と前年同月比・${rows.length}ブランド</span></div>
    <div class="panel"><div class="chartwrap">
      <table class="ovr"><thead><tr><th>ブランド</th><th class="num">店数</th>
        <th class="num">直近確定月 売上</th><th class="num">前年比</th></tr></thead>
        <tbody>${body}</tbody></table>
    </div></div>
  </section>`;
}

function renderOverview() {
  const months = DATA.months.slice(-4);
  const head = months.map(m =>
    `<th class="num">${axisLabel(DATA.months, DATA.months.indexOf(m))}</th>`).join("");
  const body = DATA.regions.map(r => {
    const codes = r.stores.filter(hasData);
    const rows = codes.map(c => {
      const cells = months.map(m => {
        const v = valueAt(c, m);
        const prov = isProvisional(m) ? " prov" : "";
        const shown = v == null ? "―" : METRIC === "cost_rate" ? pct(v) : yen(v);
        return `<td class="num${prov}">${shown}</td>`;
      }).join("");
      return `<tr><td class="rgn" style="--rc:${regionColor(r.name)}"><button class="linkbtn" data-store="${c}">${storeName(c)}</button></td>${cells}</tr>`;
    }).join("");
    return `<tr class="grp"><td colspan="${months.length + 1}">${r.name}（${codes.length}店）</td></tr>${rows}`;
  }).join("");
  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button>
      <span class="sep">／</span><button class="linkbtn" data-view="list">店舗カード</button></div>
    ${liveSummary()}
    ${budgetRanking()}
    ${brandCompare()}
    <section class="block">
      <div class="bhead"><h2>エリア・全店の売上</h2>
        <span class="bnote">直近4ヶ月・${METRIC_LABELS[METRIC]}（当月は暫定）</span></div>
      <div class="panel"><div class="chartwrap">
        <table class="ovr"><thead><tr><th>店舗</th>${head}</tr></thead><tbody>${body}</tbody></table>
      </div></div>
    </section>`;
}

// ── SVG ──────────────────────────────────────────────────────────────────
function sparkline(ser, color) {
  const vals = ser.filter(v => v != null);
  if (vals.length < 2) return `<div class="sparkempty"></div>`;
  const max = Math.max(...vals), min = Math.min(...vals, 0);
  const W = 240, H = 40, n = ser.length;
  const x = i => (i / (n - 1)) * (W - 4) + 2;
  const y = v => H - 4 - (v - min) / (max - min || 1) * (H - 8);
  let d = "", started = false;
  ser.forEach((v, i) => {
    if (v == null) { started = false; return; }
    d += (started ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1) + " ";
    started = true;
  });
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
}

function chartFrame(months, allVals, isRatio) {
  const W = 720, H = 300, PL = 56, PR = 16, PT = 18, PB = 34;
  const max = Math.max(...allVals), min = isRatio ? Math.min(...allVals) : 0;
  const n = months.length;
  const x = i => PL + (i / Math.max(n - 1, 1)) * (W - PL - PR);
  const y = v => PT + (1 - (v - min) / (max - min || 1)) * (H - PT - PB);
  let grid = "";
  for (let g = 0; g <= 4; g++) {
    const gy = PT + (g / 4) * (H - PT - PB);
    const gv = max - (g / 4) * (max - min);
    grid += `<line x1="${PL}" y1="${gy}" x2="${W - PR}" y2="${gy}" stroke="var(--line)"/>`;
    grid += `<text x="${PL - 8}" y="${gy + 3}" text-anchor="end" class="axt">${isRatio ? (gv * 100).toFixed(0) + "%" : man(gv)}</text>`;
  }
  let xlab = "";
  const step = Math.ceil(n / 8);
  months.forEach((m, i) => {
    if (i % step === 0 || i === n - 1)
      xlab += `<text x="${x(i)}" y="${H - 12}" text-anchor="middle" class="axt">${axisLabel(months, i)}</text>`;
  });
  return { W, H, x, y, grid, xlab, PT, PB };
}

function pathOf(ser, x, y) {
  let d = "", started = false;
  ser.forEach((v, i) => {
    if (v == null) { started = false; return; }
    d += (started ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1) + " ";
    started = true;
  });
  return d;
}

function singleLine(code, months, color, camps = []) {
  const isRatio = METRIC === "cost_rate";
  const ser = series(code, months);
  const vals = ser.filter(v => v != null);
  if (!vals.length) return `<div class="empty">データがありません</div>`;
  // 売上のときは予算（FW月別予算）を破線で重ねる
  const budSer = METRIC === "sales"
    ? months.map(m => { const b = budgetAt(code, m); return typeof b === "number" && b > 0 ? b : null; })
    : months.map(() => null);
  const frameVals = vals.concat(budSer.filter(v => v != null));
  const { W, H, x, y, grid, xlab, PT, PB } = chartFrame(months, frameVals, isRatio);
  const n = months.length;
  const budLine = budSer.some(v => v != null)
    ? `<path d="${pathOf(budSer, x, y)}" fill="none" stroke="var(--ink-3)" stroke-width="1.6"
        stroke-dasharray="5 4" stroke-linejoin="round" opacity="0.9"/>`
    : "";
  // 施策期間を帯として重ねる（該当月の列を薄く塗る）
  const bands = camps.map(c => {
    const sM = c.start.slice(0, 7), eM = c.end.slice(0, 7);
    const si = months.findIndex(m => m >= sM);
    let ei = -1;
    for (let i = n - 1; i >= 0; i--) { if (months[i] <= eM) { ei = i; break; } }
    if (si === -1 || ei === -1 || si > ei) return "";
    const x0 = si === 0 ? x(0) : (x(si) + x(si - 1)) / 2;
    const x1 = ei === n - 1 ? x(ei) : (x(ei) + x(ei + 1)) / 2;
    const k = kindOf(c.kind);
    return `<rect x="${x0.toFixed(1)}" y="${PT}" width="${(x1 - x0).toFixed(1)}" height="${H - PT - PB}"
        fill="${k.color}" opacity="0.12"/>
      <text x="${((x0 + x1) / 2).toFixed(1)}" y="${PT + 10}" text-anchor="middle" class="axt" fill="${k.color}">${c.title}</text>`;
  }).join("");
  // 確定と暫定（当月）を分けて描く
  const confSer = ser.map((v, i) => months[i] === CURRENT_MONTH ? null : v);
  const area = (() => {
    const d = pathOf(confSer, x, y);
    if (!d) return "";
    const first = confSer.findIndex(v => v != null);
    const last = confSer.length - 1 - [...confSer].reverse().findIndex(v => v != null);
    return `<path d="${d} L${x(last)},${H - 34} L${x(first)},${H - 34} Z" fill="${color}" opacity="0.10"/>`;
  })();
  const provDot = months.map((m, i) =>
    m === CURRENT_MONTH && ser[i] != null
      ? `<circle cx="${x(i)}" cy="${y(ser[i])}" r="4" fill="var(--surface)" stroke="var(--ink-3)" stroke-width="2" stroke-dasharray="2 2"/>` : ""
  ).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" style="min-width:520px" role="img"
      aria-label="${storeName(code)}の${METRIC_LABELS[METRIC]}推移">
    ${bands}${grid}${xlab}${area}${budLine}
    <path d="${pathOf(confSer, x, y)}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
    ${provDot}</svg>`;
}

function multiLine(codes, months, region, focusCode) {
  const isRatio = METRIC === "cost_rate";
  const all = [];
  const byCode = codes.map(c => { const s = series(c, months); s.forEach(v => v != null && all.push(v)); return s; });
  if (!all.length) return `<div class="empty">データがありません</div>`;
  const { W, H, x, y, grid, xlab } = chartFrame(months, all, isRatio);
  const base = regionColor(region);
  const many = codes.length > 5;
  const lines = byCode.map((ser, si) => {
    const c = codes[si];
    const isFocus = c === focusCode;
    const col = isFocus ? base : shade(base, si, codes.length);
    const w = isFocus ? 2.6 : (many ? 1.4 : 2);
    const op = isFocus ? 1 : (many ? 0.45 : 0.9);
    return `<path class="ml" data-si="${si}" d="${pathOf(ser, x, y)}" fill="none" stroke="${col}"
      stroke-width="${w}" stroke-opacity="${op}" stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join("");
  return `<svg class="mlsvg" viewBox="0 0 ${W} ${H}" width="100%" height="${H}" style="min-width:560px" role="img"
      aria-label="${region}エリア ${codes.length}店の${METRIC_LABELS[METRIC]}推移">
    ${grid}${xlab}${lines}</svg>`;
}

function legend(codes, region, focusCode) {
  const base = regionColor(region);
  return codes.map((c, i) => {
    const isFocus = c === focusCode;
    const col = isFocus ? base : shade(base, i, codes.length);
    return `<span class="lgi${isFocus ? " own" : ""}" data-si="${i}"><i style="background:${col}"></i>${storeName(c)}${isFocus ? "（自店）" : ""}</span>`;
  }).join("");
}

function wireEmphasis(root) {
  const svg = root.querySelector(".mlsvg");
  if (!svg) return;
  const paths = [...svg.querySelectorAll(".ml")];
  const items = [...root.querySelectorAll(".lgi")];
  const many = paths.length > 5;
  const focus = si => {
    paths.forEach(p => {
      const on = p.dataset.si === String(si);
      p.setAttribute("stroke-opacity", on ? "1" : "0.12");
      p.setAttribute("stroke-width", on ? "3" : (many ? "1.4" : "2"));
    });
    items.forEach(li => li.classList.toggle("mut", li.dataset.si !== String(si)));
  };
  const reset = () => { render0(); };
  const render0 = () => {
    paths.forEach((p, i) => {
      // 元の見た目に戻す（focus店だけ強め）
      const focusIdx = items.findIndex(li => li.classList.contains("own"));
      const isF = String(i) === (focusIdx >= 0 ? items[focusIdx].dataset.si : "-1");
      p.setAttribute("stroke-opacity", isF ? "1" : (many ? "0.45" : "0.9"));
      p.setAttribute("stroke-width", isF ? "2.6" : (many ? "1.4" : "2"));
    });
    items.forEach(li => li.classList.remove("mut"));
  };
  paths.forEach(p => { p.addEventListener("mouseenter", () => focus(p.dataset.si)); p.addEventListener("mouseleave", reset); });
  items.forEach(li => { li.addEventListener("mouseenter", () => focus(li.dataset.si)); li.addEventListener("mouseleave", reset); });
}

function shade(hex, i, total) {
  const { h, s, l } = hexToHsl(hex);
  if (total <= 1) return hex;
  const span = 34;
  const nl = Math.max(28, Math.min(72, l - span / 2 + (span * i) / (total - 1)));
  return `hsl(${h} ${s}% ${nl}%)`;
}
function hexToHsl(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255, g = parseInt(hex.slice(3, 5), 16) / 255, b = parseInt(hex.slice(5, 7), 16) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0; const l = (mx + mn) / 2;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  if (d !== 0) {
    if (mx === r) h = ((g - b) / d) % 6; else if (mx === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
    h *= 60; if (h < 0) h += 360;
  }
  return { h: Math.round(h), s: Math.round(s * 100), l: Math.round(l * 100) };
}

function fillNotice() {
  const gen = DATA.generated_at ? DATA.generated_at.replace("T", " ").replace("+00:00", " UTC") : "";
  document.getElementById("notice").innerHTML =
    `<p><b>データ</b>　FW実績・全${DATA.stores.length}店。当月は締め前の暫定値のため点線・淡色で示します。</p>
     <p><b>これから</b>　施策の登録・目標対比・ランチ／ディナー比（時間帯別）を追加します。施策を登録すると、店舗の売上グラフに施策期間の帯が重なり、結果が並びます。</p>
     <p class="fine">最終更新 ${gen}</p>`;
}

document.addEventListener("DOMContentLoaded", boot);
