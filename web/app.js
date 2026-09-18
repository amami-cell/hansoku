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
let STORE_YEAR = null;             // 店ページの年間スケジュールで見ている年（文字列 "YYYY"）
let STORE_ANNUAL_VIEW = "chart"; // 店ページ年間スケジュールの表示（chart=既定・月次一覧＋帯 / calendar=開いて詳しく）
let PANEL_SORT = "share";          // 品目構成比パネルの並び：share=売上構成比順（既定） / qty=出品数(点数)順
let PANEL_PCT = "dept";            // 品目構成比パネルの％基準：dept=部門別構成比（既定） / total=売上構成比（その月の全体比）
let ANNUAL_OPEN = {};              // カレンダー一覧の開閉状態（"code:month" と "code:month:区分" を鍵に）
let MONTH_PICK_OPEN = false;       // 月詳細の月ピッカーを開いているか
// 0円サブ（選択メニュー内訳）を親メイン商品の下に畳んで表示。開いている親の鍵
// （"code:month:親名"）の集合。既定は畳む（空集合）。パネル・売れ筋一覧で共通に使う。
const SUBS_OPEN = new Set();
let PROMO_SORT = "effect";         // この店の販促の並び（effect=効果順 / recent=新しい順）
let PROMO_FILTER = "all";          // この店の販促の状態フィルタ（all / live / done）
let STORE_LIST_SORT = "region";    // 店舗一覧の並び（region / budget / yoy / sales）
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

// 目標とメモは施策の「回」にぶら下がる。鍵は id@開始年（例 r1006-osusume@2026）。
// id だけだと、来年の秋おすすめが今年の目標・メモを上書きしてしまう。
// 鍵が入る前に書かれた行は素の id で入っているので、そちらも読む（移行の橋渡し）。
const campKey = c => c.key || `${c.id}@${(c.start || "").slice(0, 4)}`;
const pickByKey = (store, c) => {
  const k = campKey(c);
  return (k in store) ? store[k] : store[c.id];
};

// 有効な目標＝サーバ値（本番）→ 端末内
function targetOf(c) {
  if (API_OK) {
    const s = pickByKey(SERVER_TARGETS, c);
    return (s && typeof s.value === "number") ? s.value : null;
  }
  const g = pickByKey(GOALS, c);
  return typeof g === "number" ? g : null;
}

// 編集ボタンから来る id は鍵（id@開始年）。鍵がまだ無い（素のidで保存された）
// 施策では、いまの値を素のidから拾って初期値に出す。保存は必ず鍵で行う。
const bareId = key => String(key).split("@")[0];
async function editGoal(id) {
  const t = API_OK ? (SERVER_TARGETS[id] || SERVER_TARGETS[bareId(id)]) : null;
  const cur = API_OK ? (t && t.value) : (id in GOALS ? GOALS[id] : GOALS[bareId(id)]);
  const c = (DATA.campaigns || []).find(x => campKey(x) === id || x.id === bareId(id));
  const basis = c ? goalBasisLabel(c) : null;
  const per = c && goalIsMonthly(c) ? "1ヶ月あたりの" : "期間ぜんぶの";
  const v = window.prompt(
    basis
      ? `${per}目標を入力してください（円・空欄で削除）\n\n`
        + `この施策の実績は「${basis}」で見ています。同じものへの目標を入れてください。`
        + (c && goalIsMonthly(c)
            ? "\n終了日を決めていない施策なので、直近の確定月と比べます。" : "")
      : "この販促の目標売上（円）を入力してください（空欄で削除）",
    cur == null ? "" : String(cur));
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
    const s = pickByKey(SERVER_NOTES, c);
    if (s && s.note) return s.note;
    return c.memo || "";
  }
  const n = pickByKey(NOTES, c);
  if (n) return n;
  return c.memo || "";
}

async function editMemo(id) {
  const n = API_OK ? (SERVER_NOTES[id] || SERVER_NOTES[bareId(id)]) : null;
  const cur = API_OK ? (n && n.note) : (NOTES[id] || NOTES[bareId(id)]);
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

// 販促の手動ステータス（保留/中止/今季なし/完了）。目標・メモと同じく本番=Neon
// (/api/status)共有、無い所は端末内。空＝自動判定（実施中/予定/終了）に戻す。
const STATUS_OPTIONS = ["保留", "中止", "今季なし", "完了"];
let SERVER_STATUS = {};          // id → {value, by, at}
let LOCAL_STATUS = {};           // id → 値（端末内フォールバック）
function loadStatus() { try { return JSON.parse(localStorage.getItem("hansoku_status") || "{}"); } catch (e) { return {}; } }
function saveStatus() { try { localStorage.setItem("hansoku_status", JSON.stringify(LOCAL_STATUS)); } catch (e) { /* 保存不可でも表示は続く */ } }
async function fetchServerStatus() {
  try {
    const res = await fetch("/api/status", { headers: { accept: "application/json" }, cache: "no-store" });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || !ct.includes("application/json")) return;
    const data = await res.json();
    if (data && data.status) SERVER_STATUS = data.status;
  } catch (e) { /* API 無し → 端末内 */ }
}
// 手動ステータス（あれば）。無ければ null（＝自動判定を使う）
function manualStatusOf(c) {
  if (API_OK) { const s = pickByKey(SERVER_STATUS, c); return s && s.value ? s.value : null; }
  const v = pickByKey(LOCAL_STATUS, c);
  return v || null;
}
async function editStatus(id) {
  const c = (DATA.campaigns || []).find(x => campKey(x) === id || x.id === bareId(id));
  const cur = API_OK
    ? ((SERVER_STATUS[id] || SERVER_STATUS[bareId(id)] || {}).value || "")
    : (LOCAL_STATUS[id] || LOCAL_STATUS[bareId(id)] || "");
  const v = window.prompt(
    `この販促の手動ステータス（空＝自動に戻す）\n\n次のいずれかを入力: ${STATUS_OPTIONS.join(" / ")}\n`
    + "※ 実施中/予定/終了は日付から自動で出ます。保留・中止・今季なし・完了 だけ手で設定します。",
    cur);
  if (v === null) return;
  const s = String(v).trim();
  if (s && !STATUS_OPTIONS.includes(s)) { alert(`「${STATUS_OPTIONS.join("」「")}」のいずれかを入力してください（空で自動に戻す）。`); return; }
  if (API_OK) {
    try {
      const res = await fetch("/api/status", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ id, status: s }),
      });
      if (res.status === 401 || res.status === 403) { alert("ステータスの保存にはログイン（本番）が必要です。"); return; }
      if (!res.ok) { alert("ステータスの保存に失敗しました。"); return; }
      if (!s) delete SERVER_STATUS[id]; else SERVER_STATUS[id] = { value: s, by: "自分", at: new Date().toISOString() };
    } catch (e) { alert("ステータスの保存に失敗しました（通信エラー）。"); return; }
  } else {
    if (!s) delete LOCAL_STATUS[id]; else LOCAL_STATUS[id] = s;
    saveStatus();
  }
  render();
}
// ステータス表示＋編集（自動バッジの隣に置く）。手動があれば手動を主に、自動は括弧で添える。
function statusControl(c, autoLabel, autoKind) {
  const man = manualStatusOf(c);
  const badge = man
    ? `<span class="cstat man">${esc(man)}</span><span class="cstat ${autoKind} sub">${autoLabel}</span>`
    : `<span class="cstat ${autoKind}">${autoLabel}</span>`;
  const edit = WRITE_OK ? `<button class="stbtn" data-status="${campKey(c)}" title="ステータスを設定">状態 ✎</button>` : "";
  return badge + edit;
}

const CURRENT_MONTH = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
})();
const isProvisional = m => m === CURRENT_MONTH;
// データの信頼区分。締め済みの実績＝確定、当月＝暫定（集計途中）、
// 過去月なのに取り込まれていない＝未取込、未来月＝未来（未締め）。
function provOf(m, has) {
  if (m > CURRENT_MONTH) return { key: "future", label: "未来" };
  if (m === CURRENT_MONTH) return has ? { key: "prov", label: "暫定" } : { key: "wait", label: "取込待ち" };
  return has ? { key: "conf", label: "確定" } : { key: "none", label: "未取込" };
}
const provBadge = (m, has) => {
  const p = provOf(m, has);
  return `<span class="pv pv-${p.key}" title="${p.key === "conf" ? "締め済みの確定値" : p.key === "prov" ? "当月・集計途中の暫定値" : p.key === "none" ? "この月のFWデータはまだ取り込まれていません" : "未締めの先の月"}">${p.label}</span>`;
};
const TODAY = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
})();

// 施策の状態（今日基準）。予定 / 実施中 / 終了
function campStatus(c) {
  if (TODAY < c.start) return { k: "soon", label: "予定" };
  // 終了日を書いていない施策は、始まったらずっと実施中。GM改定やランチ変更は
  // 入れ替えたらそのまま続くもので、開始翌日に「終了」ではない。
  if (c.open_ended) return { k: "live", label: "実施中" };
  if (TODAY > c.end) return { k: "done", label: "終了" };
  return { k: "live", label: "実施中" };
}
// 目標数値は「2026年10月分」から運用する。過ぎた（10月前に終わる）施策には
// 目標を出さない。終了日未定（GM/ランチ変更など継続）は対象に含める。
const GOAL_START = "2026-10-01";
const goalEligible = c => !c.end || c.end >= GOAL_START;

// 効果を見る期間の終わり。終了日未定なら「今」まで（＝直近確定月まで見る）。
const campEndM = c => (c.open_ended ? CURRENT_MONTH : (c.end || c.start).slice(0, 7));
// 画面に出す期間の文字。終了日未定は「〜 継続中」。
const campRange = c =>
  c.open_ended ? `${c.start} 〜 継続中`
  : c.start === c.end ? c.start : `${c.start} 〜 ${c.end}`;
// 期間の進み具合（0-100）。予定=0／終了=100／実施中は start〜end の経過割合。
function campProgress(c) {
  const k = campStatus(c).k;
  if (k === "soon") return 0;
  if (c.open_ended) return null;   // 終わりが決まっていないので進捗率は出せない
  if (k === "done") return 100;
  const span = daysBetween(c.start, c.end) || 1;
  return Math.max(0, Math.min(100, Math.round(daysBetween(c.start, TODAY) / span * 100)));
}
// 目標達成率（確定分の実績合計÷目標）。目標や実績が無ければ null。
// 目標達成率。分母（目標）と分子（実績）は必ず同じものを指すこと。
//
// 以前は分子が「店全体の売上」だった。忘年会コースに300万の目標を入れると、
// 店全体の売上（数千万）÷300万 で達成率が数千%と出る。1回でもそんな数字を
// 見たら、この画面の数字は二度と信用されない。
// いまは主指標（その施策が効く部門・商品／GM改定は店全体）で割る。
// 終了日未定の施策（GM改定・ランチ変更など）は、実績が開始月から積み上がり続ける。
// 目標は1つなので、そのまま割ると達成率が伸び続ける（実測 4533%）。
// 終わりが無いものの目標は「1ヶ月あたり」と決め、直近確定月と比べる。
const goalIsMonthly = c => !!c.open_ended;

function campGoalRate(c) {
  const t = targetOf(c);
  if (t == null || !t) return null;
  if (goalIsMonthly(c)) {
    const m = latestCampMonth(c);
    if (!m) return null;
    const tg = campTargeted(c, null, { from: m, to: m });
    if (!tg || !tg.cur) return null;
    return { rate: tg.cur / t * 100, cur: tg.cur, target: t, label: tg.label, monthly: true, month: m };
  }
  const tg = campTargeted(c);
  if (!tg || !tg.cur) return null;
  return { rate: tg.cur / t * 100, cur: tg.cur, target: t, label: tg.label, monthly: false };
}

// その施策の期間内で、実績が出ている直近の月。
function latestCampMonth(c) {
  const sM = c.start.slice(0, 7), eM = campEndM(c);
  for (let i = DATA.months.length - 1; i >= 0; i--) {
    const m = DATA.months[i];
    if (m >= CURRENT_MONTH || m < sM || m > eM) continue;
    const has = c.stores.some(code => {
      const t = campTargeted(c, code, { from: m, to: m });
      return t && t.cur;
    });
    if (has) return m;
  }
  return null;
}
// 目標を入力してもらうときに「何に対する目標か」を必ず言う。
function goalBasisLabel(c) {
  const b = campBasis(c);
  if (!b) return null;
  if (b.kind === "store") return "店全体の売上";
  if (b.kind === "items") return `${b.items.join("・")} の売上`;
  return `${b.bucket}部門の売上`;
}
// 施策期間の効果（月次・確定分のみ）。施策が掛かる確定月の値を、前年同月と比べる。
// 月次データしか無いので月単位の概算。当月（暫定）と未来月は含めない。
// accessor(code, month) で「売上」でも「客数」でも同じ計算を使い回す。
function effectOver(code, c, accessor) {
  const sM = c.start.slice(0, 7), eM = campEndM(c);
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

// ── さがす（店舗・施策・商品・部門を1本で）──────────────────────────
// 24店 × 90施策 × 商品を、画面を渡り歩かずに1箇所から引けるようにする。
// 索引は DATA から1回だけ作って使い回す（描画のたびに作り直さない）。
let SEARCH_INDEX = null;

function buildSearchIndex() {
  const out = [];
  for (const s of DATA.stores || []) {
    out.push({
      kind: "store", label: s.name,
      // 通称を副題に出す。「大衆寿司酒場すさび湯」だけだと、梅田で探した人は
      // 自分の店だと分からない。
      sub: [(s.aliases || [])[0], s.region, s.brand_name].filter(Boolean).join("・"),
      key: [s.name, ...(s.aliases || []), ...(s.yomi || []),
            s.code, s.region, s.brand_name].filter(Boolean).join(" "),
      view: { kind: "store", code: s.code },
    });
  }
  for (const c of DATA.campaigns || []) {
    out.push({
      kind: "campaign", label: c.title,
      sub: `${kindOf(c.kind).label}・${campScopeLabel(c)}・${campRange(c)}`,
      key: [c.title, c.note, kindOf(c.kind).label, ...(c.items || []), c.bucket,
            ...c.stores.map(storeName)].filter(Boolean).join(" "),
      view: { kind: "campaign", id: c.id },
    });
  }
  // 商品は店ごとに重複するので、名前でまとめて「出ている店」を持たせる
  const prod = new Map();
  for (const [code, items] of Object.entries(DATA.products || {})) {
    for (const p of items) {
      const e = prod.get(p.name) || { sales: 0, codes: new Set() };
      e.sales += p.sales || 0; e.codes.add(code);
      prod.set(p.name, e);
    }
  }
  for (const [name, e] of prod) {
    const codes = [...e.codes];
    out.push({
      kind: "product", label: name,
      sub: `売れ筋・${codes.length === 1 ? storeName(codes[0]) : codes.length + "店で計上"}・${yen(e.sales)}`,
      key: name,
      view: { kind: "store", code: codes[0] },
    });
  }
  return out;
}

// 表記ゆれを吸収して比べる。人は正式表記どおりには打たない。
//  NFKC ……… 半角カナ→全角カナ（｢ｳﾒﾀﾞ｣→｢ウメダ｣。濁点も1文字に合成される）、
//             全角英数→半角。スマホのキーボードは半角カナを普通に出す。
//  かな→カナ … ｢ぷれみあむ｣で｢プレミアム｣に当たるように。
//  記号落とし … 中黒・スラッシュ・長音・ハイフン・空白は店名や商品名で
//             付いたり付かなかったりするので、両側から落として比べる。
const foldKey = (s) => String(s || "").normalize("NFKC").toLowerCase()
  .replace(/[ぁ-ゖ]/g, ch => String.fromCharCode(ch.charCodeAt(0) + 0x60))
  .replace(/[\s　・／/ー\-‐−–—]/g, "");

const KIND_ORDER = { store: 0, campaign: 1, product: 2 };

function searchAll(q, limit = 24) {
  const needle = foldKey(q);
  if (!SEARCH_INDEX) SEARCH_INDEX = buildSearchIndex();
  // 何も打っていないときは全店を並べる。スマホには店舗セレクトが無いので、
  // ここが「名前を思い出せないときに一覧から選ぶ」入口も兼ねる。
  if (needle.length < 1) {
    return SEARCH_INDEX.filter(e => e.kind === "store")
      .sort((a, b) => a.label.localeCompare(b.label, "ja"))
      .slice(0, 40);
  }
  const hits = [];
  for (const e of SEARCH_INDEX) {
    const hay = foldKey(e.key);
    const at = hay.indexOf(needle);
    if (at < 0) continue;
    // 先頭一致を上に、種類は 店舗→施策→商品 の順
    hits.push({ ...e, score: (at === 0 ? 0 : 1) * 10 + KIND_ORDER[e.kind] });
  }
  hits.sort((a, b) => a.score - b.score || a.label.localeCompare(b.label, "ja"));
  return hits.slice(0, limit);
}

const SEARCH_ICON = { store: "店", campaign: "販", product: "品" };

function searchResultsHtml(q) {
  const hits = searchAll(q);
  const hint = !q
    ? `<p class="sr-hint">店名・通称・読みがな・施策名・商品名で探せます。<br>
       例: 「梅田」「うめだ」「忘年会」「唐揚げ」 ── 打たなければ全店の一覧です。</p>`
    : "";
  if (q && !hits.length) return `<p class="sr-hint">「${esc(q)}」に当たるものはありませんでした。</p>`;
  return hint + `<ul class="sr-list">${hits.map((h, i) => `
    <li><button class="sr-item" data-srindex="${i}">
      <span class="sr-ico sr-${h.kind}">${SEARCH_ICON[h.kind]}</span>
      <span class="sr-body"><span class="sr-label">${esc(h.label)}</span>
        <span class="sr-sub">${esc(h.sub)}</span></span>
    </button></li>`).join("")}</ul>`;
}

// ── URL（#）と画面の対応 ───────────────────────────────────────────────
// 状態を URL に出さないと、戻るボタンでアプリごと抜け、リンクも送れず、
// 店長は自分の店をホーム画面に置けない。VIEW は URL の写しとして扱う。
//
// #/                        全店スケジュール（本部の入口）
// #/store/1006              店舗詳細（店長の入口）
// #/campaign/<id>           施策詳細
// #/campaigns?status=review 施策の効果（絞り込みつき）
// #/year/2026 など
const VIEW_PATHS = {
  schedule: "", calendar: "calendar", list: "stores", overview: "overview",
  campaigns: "campaigns", manage: "manage", cross: "cross", gallery: "gallery",
};
const PATH_VIEWS = Object.fromEntries(Object.entries(VIEW_PATHS).map(([k, v]) => [v, k]));

function viewToHash(v = VIEW) {
  let path = "";
  if (v.kind === "store") path = `store/${encodeURIComponent(v.code)}`;
  else if (v.kind === "storemonth") path = `store/${encodeURIComponent(v.code)}/${encodeURIComponent(v.month)}`;
  else if (v.kind === "storecat") path = `store/${encodeURIComponent(v.code)}/${encodeURIComponent(v.month)}/${encodeURIComponent(v.cat)}`;
  else if (v.kind === "lunch") path = `lunch/${encodeURIComponent(v.code)}`;
  else if (v.kind === "campaign") path = `campaign/${encodeURIComponent(v.id)}`;
  else if (v.kind === "year") path = `year/${YEAR}`;
  else path = VIEW_PATHS[v.kind] ?? "";
  const q = new URLSearchParams();
  if (METRIC !== "sales") q.set("metric", METRIC);
  if (v.kind === "campaigns") {
    if (CAMP_FILTER.status !== "all") q.set("status", CAMP_FILTER.status);
    if (CAMP_FILTER.kind !== "all") q.set("kind", CAMP_FILTER.kind);
  }
  const qs = q.toString();
  return `#/${path}${qs ? "?" + qs : ""}`;
}

// URL → 画面。読めない URL は既定（全店スケジュール）に落とす。
function hashToView(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  const q = new URLSearchParams(query || "");
  const metric = q.get("metric");
  if (metric && METRIC_LABELS[metric]) METRIC = metric;
  const seg = path.split("/").map(x => (x ? decodeURIComponent(x) : x));
  const [head, arg, arg2, arg3] = seg;
  if (head === "store" && arg && arg2 && arg3) return { kind: "storecat", code: arg, month: arg2, cat: arg3 };
  if (head === "store" && arg && arg2) return { kind: "storemonth", code: arg, month: arg2 };
  if (head === "store" && arg) return { kind: "store", code: arg };
  if (head === "lunch" && arg) return { kind: "lunch", code: arg };
  if (head === "campaign" && arg) return { kind: "campaign", id: arg };
  if (head === "year") { if (arg && /^\d{4}$/.test(arg)) YEAR = +arg; return { kind: "year" }; }
  if (head === "campaigns") {
    CAMP_FILTER = { status: q.get("status") || "all", kind: q.get("kind") || "all" };
    return { kind: "campaigns" };
  }
  const kind = PATH_VIEWS[head];
  return kind ? { kind } : { kind: "schedule" };
}

// いま見えているものを URL に反映する。履歴を汚さないよう、同じなら何もしない。
let SUPPRESS_HASH = false;
function syncHash(replace) {
  const next = viewToHash();
  if (location.hash === next) return;
  SUPPRESS_HASH = true;
  if (replace) history.replaceState(null, "", next);
  else history.pushState(null, "", next);
  SUPPRESS_HASH = false;
}

// ── うちの店（店長は毎回ここから始まる）──────────────────────────────
const HOME_KEY = "hansoku_home_store";
function homeStore() {
  try {
    const c = localStorage.getItem(HOME_KEY);
    return c && store(c).code ? c : null;
  } catch (e) { return null; }
}
function setHomeStore(code) {
  try {
    if (code) localStorage.setItem(HOME_KEY, code); else localStorage.removeItem(HOME_KEY);
  } catch (e) { /* 保存できなくても表示は続く */ }
}

// ── ナビ（PCの左／スマホの下タブ。同じ data-nav で動く）──────────────
// 「うちの店」は、決めていなければ全店スケジュールへ。決めていればその店へ。
function navTo(key) {
  if (key === "home") {
    const h = homeStore();
    go(h ? { kind: "store", code: h } : { kind: "schedule" });
    return;
  }
  if (key === "year") { YEAR = new Date().getFullYear(); }
  if (key === "calendar") { CAL_MONTH = CAL_MONTH || CURRENT_MONTH; }
  go({ kind: key });
}

// いまどこにいるかをナビに反映する。
function markNav() {
  const cur = VIEW.kind === "store" && homeStore() === VIEW.code ? "home" : VIEW.kind;
  document.querySelectorAll("[data-nav]").forEach(el =>
    el.classList.toggle("on", el.dataset.nav === cur));
}

function wireNav() {
  document.querySelectorAll("[data-nav]").forEach(el =>
    el.addEventListener("click", () => navTo(el.dataset.nav)));
  const home = document.getElementById("homebtn");
  if (home) home.addEventListener("click", () => navTo("home"));
}

// ── さがすシート ──────────────────────────────────────────────────────
let SEARCH_HITS = [];
let SEARCH_AT = 0;

function openSearch() {
  const sheet = document.getElementById("searchsheet");
  const input = document.getElementById("searchinput");
  sheet.hidden = false;
  input.value = "";
  drawSearch("");
  // スマホでキーボードが出るまで少し待つ端末があるので、次のフレームで当てる
  requestAnimationFrame(() => input.focus());
}
function closeSearch() {
  document.getElementById("searchsheet").hidden = true;
}
function drawSearch(q) {
  SEARCH_HITS = searchAll(q);
  SEARCH_AT = 0;
  const box = document.getElementById("searchresults");
  box.innerHTML = searchResultsHtml(q);
  box.querySelectorAll("[data-srindex]").forEach(el =>
    el.addEventListener("click", () => pickSearch(+el.dataset.srindex)));
  markSearchCursor();
}
function markSearchCursor() {
  const items = document.querySelectorAll("#searchresults .sr-item");
  items.forEach((el, i) => el.classList.toggle("on", i === SEARCH_AT));
  const cur = items[SEARCH_AT];
  if (cur) cur.scrollIntoView({ block: "nearest" });
}
function pickSearch(i) {
  const h = SEARCH_HITS[i];
  if (!h) return;
  closeSearch();
  go(h.view);
}

function wireSearch() {
  const input = document.getElementById("searchinput");
  document.getElementById("searchopen").addEventListener("click", openSearch);
  const tab = document.getElementById("tabsearch");
  if (tab) tab.addEventListener("click", openSearch);
  document.querySelectorAll("[data-closesearch]").forEach(el =>
    el.addEventListener("click", closeSearch));
  input.addEventListener("input", () => drawSearch(input.value.trim()));
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { e.preventDefault(); SEARCH_AT = Math.min(SEARCH_AT + 1, SEARCH_HITS.length - 1); markSearchCursor(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); SEARCH_AT = Math.max(SEARCH_AT - 1, 0); markSearchCursor(); }
    else if (e.key === "Enter") { e.preventDefault(); pickSearch(SEARCH_AT); }
    else if (e.key === "Escape") { closeSearch(); }
  });
  // PC: 「/」または Ctrl/⌘+K でどこからでも開く。
  // ⌘K は他のアプリで体に入っている人が多いので、入力中でも受ける。
  // 「/」は文字なので、入力中は邪魔しない。
  document.addEventListener("keydown", e => {
    const t = e.target;
    const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); openSearch(); return; }
    if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); openSearch(); }
    if (e.key === "Escape") closeSearch();
  });
}

// dashboard.json を読む。失敗の「種類」を見分けて、正しい直し方を出す。
//  - 通信で弾かれた（"Failed to fetch"）＝ログイン期限切れ/ネットワーク/Access
//    リダイレクト。→ 再読み込みを促す。夜間バッチとは無関係。
//  - 404 ＝まだ書き出されていない。→ 夜間バッチ後に再試行。
//  - その他HTTPエラー ＝サーバ側。→ 時間をおいて再読み込み。
// 一時的な瞬断のために、通信エラー時だけ短く数回リトライする。
async function loadDashboard() {
  // まず <script src="data/dashboard.js"> で先読みしたデータを使う。app.js が動いている＝
  // Accessセッションは有効なので、同じ <script> 経路のこれは必ず読めている。これにより
  // 「Failed to fetch」（fetch だけが Access の302リダイレクトで落ちる）を根絶する。
  if (window.__DASHBOARD__ && typeof window.__DASHBOARD__ === "object") {
    return window.__DASHBOARD__;
  }
  // 保険：先読みが無い環境（ローカルの直開き・古いデプロイ）は従来どおり fetch で補う。
  let last = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch("data/dashboard.json", { cache: "no-store" });
      if (res.ok) return await res.json();
      last = { kind: res.status === 404 ? "missing" : "server", detail: res.status };
      if (res.status === 404) break;   // 404 は粘っても無駄
    } catch (e) {
      last = { kind: "network", detail: (e && e.message) || "通信エラー" };
    }
    if (attempt < 2) await new Promise(r => setTimeout(r, 800 * (attempt + 1)));
  }
  paintLoadError(last);
  return null;
}

function paintLoadError(err) {
  const app = document.getElementById("app");
  const kind = err ? err.kind : "network";
  let msg;
  if (kind === "missing") {
    msg = `データがまだ書き出されていません（404）。<br>夜間バッチ（毎日11:00）の後に、もう一度お試しください。`;
  } else if (kind === "server") {
    msg = `サーバでエラーが発生しました（${err.detail}）。<br>少し時間をおいてから再読み込みしてください。`;
  } else {
    msg = `データの取得が通信でブロックされました（${err ? err.detail : "通信エラー"}）。<br>
      ログインの期限切れやネットワークの問題の可能性があります。まず再読み込みしてください。<br>
      <span class="muted">それでも直らない場合は本部（システム担当）へご連絡ください。データ自体は正常です。</span>`;
  }
  app.innerHTML = `<div class="empty">${msg}<div style="margin-top:14px">
    <button class="tbtn" data-reload>再読み込み</button></div></div>`;
  const b = app.querySelector("[data-reload]");
  if (b) b.addEventListener("click", () => location.reload());
}

// ── 起動 ─────────────────────────────────────────────────────────────────
async function boot() {
  document.getElementById("today").textContent = formatToday();
  wireTheme();
  DATA = await loadDashboard();
  if (!DATA) return;   // 失敗時は loadDashboard が画面に理由を出して null を返す
  CAL_MONTH = CURRENT_MONTH;
  YEAR = new Date().getFullYear();
  STORE_YEAR = String(new Date().getFullYear());
  GOALS = loadGoals();
  NOTES = loadNotes();
  LOCAL_STATUS = loadStatus();
  await fetchServerTargets();   // 本番は Neon の共有目標を読む。無ければ端末内保存で動く
  await fetchServerNotes();     // 要因メモも同様（本番=共有、無ければ端末内）
  await fetchServerStatus();    // 手動ステータス（本番=共有、無ければ端末内）
  await fetchServerCreatives(); // アップロード制作物（本番のみ・無ければ台帳ぶんだけ）
  await fetchServerPlans();     // アプリ内で起票した販促プラン（本番のみ・台帳と統合）
  await fetchServerMe();        // 誰でログイン中か・書き込めるか（WRITE_OK を確定）
  paintAccount();
  // URL が指定されていればそれに従う。無ければ「うちの店」。それも無ければ全店。
  if (location.hash && location.hash !== "#") {
    VIEW = hashToView(location.hash);
  } else {
    const home = homeStore();
    if (home) VIEW = { kind: "store", code: home };
  }
  buildMetricSelect();
  buildStoreJump();
  wireNav();
  wireSearch();
  render();
  syncHash(true);
  fillNotice();
  // 戻る/進むで画面が動くようにする。
  window.addEventListener("popstate", () => {
    if (SUPPRESS_HASH) return;
    VIEW = hashToView(location.hash);
    buildMetricSelect();
    render();
  });
  window.addEventListener("hashchange", () => {
    if (SUPPRESS_HASH) return;
    const v = hashToView(location.hash);
    if (JSON.stringify(v) === JSON.stringify(VIEW)) return;
    VIEW = v; buildMetricSelect(); render();
  });
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
// 比較の2ヶ月がリニューアルをまたぐか。またぐ＝別業態どうしの比較なので、
// 数字は出すが「同じ店の前年比」として読ませない。
function crossesRenewal(code, from, to) {
  const r = store(code).renewal_month;
  return !!r && from < r && to >= r;
}
const renewalNote = code => {
  const st = store(code);
  return st.renewal_month
    ? `${st.renewal_month} リニューアル${st.former_name ? `（前: ${st.former_name}）` : ""}`
    : "";
};

function yoy(code) {
  const last = latestConfirmed(code);
  if (!last) return null;
  const [y, mo] = last.m.split("-");
  const prev = `${+y - 1}-${mo}`;
  const b = valueAt(code, prev);
  if (typeof b !== "number" || !b) return null;
  return {
    month: last.m, cur: last.v, prev: b, pct: (last.v / b - 1) * 100,
    renewal: crossesRenewal(code, prev, last.m) ? renewalNote(code) : null,
  };
}

// ── 集客（客数）。売上と別枠の DATA.covers を読む。人数なので円と混ぜない ──
const nin = n => Math.round(n).toLocaleString("ja-JP") + "人";
// 点数（出品数）用の素の整数フォーマッタ。客数(人)とは別物なので「人」を付けない。
const ten = n => Math.round(n).toLocaleString("ja-JP");
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
  sel.addEventListener("change", () => { METRIC = sel.value; render(); syncHash(); });
}
function buildStoreJump() {
  const sel = document.getElementById("storesel");
  const opts = ['<option value="">店舗をえらぶ…</option>']
    .concat(DATA.stores.filter(s => hasData(s.code))
      .map(s => `<option value="${s.code}">${s.name}（${s.region}）</option>`));
  sel.innerHTML = opts.join("");
  sel.addEventListener("change", () => {
    if (sel.value) { VIEW = { kind: "store", code: sel.value }; render(); syncHash(); sel.value = ""; }
  });
}

// ── ルーティング描画 ─────────────────────────────────────────────────────
function render() {
  const app = document.getElementById("app");
  if (VIEW.kind === "store") app.innerHTML = renderStore(VIEW.code);
  else if (VIEW.kind === "storemonth") app.innerHTML = renderStoreMonth(VIEW.code, VIEW.month);
  else if (VIEW.kind === "storecat") app.innerHTML = renderStoreCat(VIEW.code, VIEW.month, VIEW.cat);
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

  // 開放モード（閲覧専用）は、書き込み系ボタン（追加/削除/目標/メモ）を丸ごと外す。
  // サーバも 403 で弾くが、押せるボタンを残さない。
  if (!WRITE_OK) {
    app.querySelectorAll("[data-upload],[data-crdel],[data-goal],[data-memo],[data-status]").forEach(el => el.remove());
  }

  app.querySelectorAll("[data-camp]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); go({ kind: "campaign", id: el.dataset.camp }); }));
  app.querySelectorAll("[data-store]").forEach(el =>
    el.addEventListener("click", () => go({ kind: "store", code: el.dataset.store })));
  app.querySelectorAll("[data-smonth]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      MONTH_PICK_OPEN = false;   // 月を移動したらピッカーは閉じる
      const [c, m] = el.dataset.smonth.split(":");
      go({ kind: "storemonth", code: c, month: m });
    }));
  // カレンダー一覧の月・区分の開閉、月詳細の月ピッカー（いずれもページ遷移せず開閉）
  app.querySelectorAll("[data-mtoggle]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const k = el.dataset.mtoggle;
      if (ANNUAL_OPEN[k]) delete ANNUAL_OPEN[k]; else ANNUAL_OPEN[k] = true;
      render();
    }));
  app.querySelectorAll("[data-cattoggle]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const parts = el.dataset.cattoggle.split(":");
      const k = `${parts[0]}:${parts[1]}:${decodeURIComponent(parts.slice(2).join(":"))}`;
      if (ANNUAL_OPEN[k]) delete ANNUAL_OPEN[k]; else ANNUAL_OPEN[k] = true;
      render();
    }));
  // 0円サブ（内訳）の親行の開閉（売れ筋一覧＝本文側。パネルは wireSubToggle が受ける）。
  // その場でDOMを切替える（render() を呼ぶと基礎データの折りたたみごと畳まれてしまうため）。
  app.querySelectorAll("[data-subtoggle]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const k = el.dataset.subtoggle;
      const open = !SUBS_OPEN.has(k);
      if (open) SUBS_OPEN.add(k); else SUBS_OPEN.delete(k);
      // 親行（.prow）の直後に続く内訳行（.prowsub）だけを開閉する。
      let n = 0, node = (el.closest(".prow") || el).nextElementSibling;
      while (node && node.classList.contains("prowsub")) { node.hidden = !open; n++; node = node.nextElementSibling; }
      el.classList.toggle("on", open);
      el.setAttribute("aria-expanded", String(open));
      el.textContent = `${open ? "▾" : "▸"} 内訳${n}件${open ? "" : "（詳細表示）"}`;
    }));
  app.querySelectorAll("[data-mpick]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); MONTH_PICK_OPEN = !MONTH_PICK_OPEN; render(); }));
  // 構成比（金額）セル → 商品内訳の小ウインドウを開く（複数可）。render しない＝既存の窓は残る。
  app.querySelectorAll("[data-compocell]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const parts = el.dataset.compocell.split(":");
      openCompo(parts[0], parts[1], decodeURIComponent(parts.slice(2).join(":")));
    }));
  // F/D比セル → その月の「部門別」小窓（販促の部門・商品は色付き）。
  app.querySelectorAll("[data-fdcell]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const [c, m] = el.dataset.fdcell.split(":");
      openMonth(c, m);
    }));
  app.querySelectorAll("[data-scat]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const [c, m, cat] = el.dataset.scat.split(":");
      go({ kind: "storecat", code: c, month: m, cat: decodeURIComponent(cat) });
    }));
  app.querySelectorAll("[data-savw]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); STORE_ANNUAL_VIEW = el.dataset.savw; render(); }));
  app.querySelectorAll("[data-syear]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); STORE_YEAR = el.dataset.syear; render(); }));
  app.querySelectorAll("[data-psort]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); PROMO_SORT = el.dataset.psort; render(); }));
  app.querySelectorAll("[data-pfilter]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); PROMO_FILTER = el.dataset.pfilter; render(); }));
  app.querySelectorAll("[data-lunch]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); go({ kind: "lunch", code: el.dataset.lunch }); }));
  app.querySelectorAll("[data-home]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const code = el.dataset.home;
      setHomeStore(homeStore() === code ? null : code);
      render();
    }));
  app.querySelectorAll("[data-tilefilter]").forEach(el =>
    el.addEventListener("click", () => { CAMP_FILTER = { ...CAMP_FILTER, status: el.dataset.tilefilter }; }, true));
  app.querySelectorAll("[data-listsort]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); STORE_LIST_SORT = el.dataset.listsort;
      if (el.hasAttribute("data-view")) return;   // data-view 付きは遷移側に任せる
      render(); }));
  app.querySelectorAll("[data-view]").forEach(el =>
    el.addEventListener("click", () => go({ kind: el.dataset.view }, el.dataset.scroll)));
  app.querySelectorAll("[data-cal]").forEach(el =>
    el.addEventListener("click", () => { CAL_MONTH = addMonth(CAL_MONTH, el.dataset.cal === "next" ? 1 : -1); render(); }));
  app.querySelectorAll("[data-year]").forEach(el =>
    el.addEventListener("click", () => { YEAR = +el.dataset.year; render(); syncHash(); }));
  app.querySelectorAll("[data-goal]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); editGoal(el.dataset.goal); }));
  app.querySelectorAll("[data-memo]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); editMemo(el.dataset.memo); }));
  app.querySelectorAll("[data-status]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); editStatus(el.dataset.status); }));
  app.querySelectorAll("[data-upload]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const [kind, val] = el.dataset.upload.split(":");
      promptUpload(kind === "campaign" ? val : "", kind === "store" ? val : "");
    }));
  app.querySelectorAll("[data-crdel]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); deleteCreative(el.dataset.crdel); }));
  app.querySelectorAll("[data-plannew]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); openPlanEditor({ store_code: el.dataset.plannew }); }));
  app.querySelectorAll("[data-plandup]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); duplicatePlan(el.dataset.plandup, VIEW && VIEW.code); }));
  app.querySelectorAll("[data-planedit]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const p = planById(el.dataset.planedit);
      if (p) openPlanEditor(p); else alert("プランが見つかりません。再読み込みしてください。");
    }));
  app.querySelectorAll("[data-crurl]").forEach(el =>
    el.addEventListener("click", e => { e.stopPropagation(); openCreativePreview(el.dataset.crurl, el.dataset.crmime, el.dataset.crtitle, el.dataset.cropen); }));
  app.querySelectorAll("[data-cfilter]").forEach(el =>
    el.addEventListener("click", () => {
      const [dim, val] = el.dataset.cfilter.split(":");
      CAMP_FILTER = { ...CAMP_FILTER, [dim]: val };
      render(); syncHash();
    }));
  app.querySelectorAll("[data-sharecopy]").forEach(el =>
    el.addEventListener("click", async e => {
      e.stopPropagation();
      const src = document.getElementById(el.dataset.sharecopy);
      const text = src ? src.textContent : "";
      const done = () => { const o = el.textContent; el.textContent = "✓ コピーしました"; setTimeout(() => { el.textContent = o; }, 1800); };
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); done(); return; }
        throw new Error("no clipboard");
      } catch (_) {
        // フォールバック：選択してユーザーにコピーしてもらう
        const ta = document.createElement("textarea");
        ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); done(); } catch (e2) { alert("コピーできませんでした。カードを長押しで選択してください。"); }
        ta.remove();
      }
    }));
  app.querySelectorAll("[data-prodsearch]").forEach(el =>
    el.addEventListener("input", () => {
      const q = el.value.trim().toLowerCase();
      const list = el.parentElement.querySelector(".pslist");
      if (!list) return;
      let shown = 0;
      list.querySelectorAll(".psrow").forEach(li => {
        const hit = !q || (li.dataset.pn || "").includes(q);
        li.hidden = !hit; if (hit) shown++;
      });
      const empty = list.querySelector(".psempty");
      if (empty) empty.hidden = shown !== 0;
    }));
  app.querySelectorAll("[data-jump]").forEach(el =>
    el.addEventListener("click", e => {
      e.stopPropagation();
      const t = document.getElementById(el.dataset.jump);
      if (!t) return;
      const det = t.closest("details");   // 基礎データは折りたたみ。飛ぶ時は開く。
      if (det && !det.open) det.open = true;
      t.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  wireEmphasis(app);
  wireTips(app);
  markNav();
}
function go(v, scroll) {
  VIEW = v; render(); syncHash();
  if (scroll) {
    const el = document.getElementById(scroll);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// タイムライン⇄カレンダーの切替
function viewToggle(active) {
  const t = (k, label) => `<button class="vtab${active === k ? " on" : ""}" data-view="${k}">${label}</button>`;
  return `<div class="viewtabs">${t("schedule", "タイムライン")}${t("calendar", "カレンダー")}</div>`;
}

// ── 進捗＆振り返りサマリ（TOP最上部・アプリの主眼を一目で）──────────────
// 「今どれだけ動いていて（進捗）／結果はどうで（判定）／やりっぱなしが無いか（振り返り）」
// を4タイルで示す。タップで施策の効果へ。数値はすべて既存の判定・効果から算出。
function reviewProgressStrip() {
  const camps = DATA.campaigns || [];
  if (!camps.length) return "";
  const live = camps.filter(c => campStatus(c).k === "live");
  const done = camps.filter(c => campStatus(c).k === "done");
  const soon = camps.filter(c => campStatus(c).k === "soon");
  // 判定内訳（実施中＋終了で計測できたもの）
  let good = 0, warn = 0;
  [...live, ...done].forEach(c => {
    const t = campVerdict(c).tone;
    if (t === "good") good++; else if (t === "warn") warn++;
  });
  // 目標達成（実施中で目標入り）
  const goals = live.map(campGoalRate).filter(Boolean);
  const achieved = goals.filter(g => g.rate >= 100).length;
  // 規模の違う施策の率を単純平均すると、小さい施策1件の大きな率が全体を支配する。
  // 実績の合計 ÷ 目標の合計 にする。
  const goalSum = goals.reduce((a, g) => a + g.target, 0);
  const curSum = goals.reduce((a, g) => a + g.cur, 0);
  const avgRate = goalSum ? Math.round(curSum / goalSum * 100) : null;
  // 振り返り（終了のうち記入済み）
  const needs = done.filter(needsReview).length;
  const reviewed = done.length - needs;
  const rvPct = done.length ? Math.round(reviewed / done.length * 100) : null;

  // 全店の予算達成（直近確定月の 実績÷予算）。予算を取り込んだ店ぶんで平均＋未達店数。
  const budRates = DATA.stores.map(s => budgetRate(s.code)).filter(Boolean);
  const underBud = budRates.filter(b => b.rate < 100).length;
  const avgBud = budRates.length ? Math.round(budRates.reduce((a, b) => a + b.rate, 0) / budRates.length) : null;

  // 原価アラート（前月比で原価率が悪化した店）
  const alerts = costAlerts();

  const tile = (view, big, lbl, sub, tone, scroll, filter) => `
    <button class="rptile${tone ? " " + tone : ""}" data-view="${view}"${scroll ? ` data-scroll="${scroll}"` : ""}${filter ? ` data-tilefilter="${filter}"` : ""}>
      <div class="rpv">${big}</div><div class="rpl">${lbl}</div>
      ${sub ? `<div class="rps">${sub}</div>` : ""}</button>`;
  // 予算達成タイルは店舗一覧（予算達成率順）へ飛ばす。data-listsort で並びを指定。
  const budTile = `<button class="rptile${underBud ? " warn" : ""}" data-view="list" data-listsort="budget">
      <div class="rpv">${avgBud != null ? `${avgBud}<small>%</small>` : "―"}</div>
      <div class="rpl">全店 予算達成(平均)</div>
      <div class="rps">${budRates.length ? (underBud ? `未達 ${underBud}店 → 確認` : "全店 達成") : "予算 未取込"}</div></button>`;

  return `
    <section class="rpstrip">
      ${tile("campaigns", `${live.length}<small>件</small>`, "実施中の施策", soon.length ? `予定 ${soon.length}・終了 ${done.length}` : `終了 ${done.length}`, "", "", "live")}
      ${budTile}
      ${tile("campaigns", `<span class="up">${good}</span> / <span class="down">${warn}</span>`, "判定 効果あり/要改善",
        `測れているのは ${good + warn} / ${live.length + done.length}件`)}
      ${tile("campaigns",
        goals.length ? `${achieved}<small>/${goals.length}</small>` : "―",
        "目標達成（実施中）",
        goals.length ? `全体の達成率 ${avgRate}%` : "目標未入力", goals.length && avgRate < 100 ? "warn" : "")}
      ${tile("campaigns",
        done.length ? `${rvPct}<small>%</small>` : "―",
        "振り返り記入率", needs ? `未記入 ${needs}件が残っています` : (done.length ? "やりっぱなし ゼロ" : "終了施策なし"),
        needs ? "warn" : "", "", "review")}
      ${tile("cross",
        `${alerts.length}<small>店</small>`,
        "原価アラート",
        alerts.length ? `前月比+${COST_ALERT_TH.toFixed(1)}pt以上の悪化 → 確認` : "悪化店なし",
        alerts.length ? "warn" : "", "cost-alert")}
    </section>`;
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
  const noGoal = camps.filter(c => campStatus(c).k === "live" && goalEligible(c) && targetOf(c) == null).slice(0, 8);
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
      <button class="goalbtn add" data-goal="${campKey(c)}">＋ 目標を入力</button></li>`).join("");

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
    ${reviewProgressStrip()}
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
  const monthLbl = DATA.products_group_month ? `（${DATA.products_group_month}）` : "";
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

// 店の収益性（直近確定月の 客単価＝売上/客数、粗利率＝1-原価率）。既存データから。
function storeProfit(code) {
  const crAt = (c, m) => (DATA.cost_rate[c] || {})[m];
  const ls = latestWith(code, salesAt);
  let kt = null;
  if (ls) {
    const cov = coversAt(code, ls.m);
    if (typeof cov === "number" && cov > 0) kt = ls.v / cov;
  }
  const lcr = latestWith(code, crAt);
  return { kt, gp: lcr ? 1 - lcr.v : null };
}

function renderList() {
  const months = DATA.months;
  // 各店の指標を先に集約（並べ替え・要注意サマリで使う）。エリア順のフラット列。
  const entries = DATA.regions.flatMap(r =>
    r.stores.filter(hasData).map(code => ({
      code, region: r.name, color: regionColor(r.name),
      total: periodTotal(code), y: yoy(code), br: budgetRate(code),
    })));
  // 要注意：予算未達・前年割れ（別業態リニューアルは除く）。管理の入口として先頭に出す。
  const under = entries.filter(e => e.br && e.br.rate < 100)
    .sort((a, b) => a.br.rate - b.br.rate);
  const yoyDown = entries.filter(e => e.y && !e.y.renewal && e.y.pct < 0)
    .sort((a, b) => a.y.pct - b.y.pct);
  const chip = (e, txt, tone) => `<button class="lwchip ${tone}" data-store="${e.code}">${storeName(e.code)} ${txt}</button>`;
  const warnBox = (under.length || yoyDown.length)
    ? `<div class="lwarn">
        <div class="lwrow"><span class="lwl">予算未達 ${under.length}店</span>${under.slice(0, 4).map(e => chip(e, `${e.br.rate.toFixed(0)}%`, "down")).join("")}${under.length > 4 ? `<span class="lwmore">ほか${under.length - 4}</span>` : ""}</div>
        <div class="lwrow"><span class="lwl">前年割れ ${yoyDown.length}店</span>${yoyDown.slice(0, 4).map(e => chip(e, `${signed(e.y.pct)}%`, "down")).join("")}${yoyDown.length > 4 ? `<span class="lwmore">ほか${yoyDown.length - 4}</span>` : ""}</div>
      </div>`
    : `<div class="lwarn ok">予算未達・前年割れの店はありません 👍</div>`;
  // 並べ替え。既定はエリア順（entries はエリア順）。悪い順を上に出すと確認しやすい。
  const big = 1e15;
  const sk = STORE_LIST_SORT;
  const ordered = sk === "budget"
    ? entries.slice().sort((a, b) => (a.br ? a.br.rate : big) - (b.br ? b.br.rate : big))
    : sk === "yoy"
      ? entries.slice().sort((a, b) => ((a.y && !a.y.renewal) ? a.y.pct : big) - ((b.y && !b.y.renewal) ? b.y.pct : big))
      : sk === "sales"
        ? entries.slice().sort((a, b) => b.total - a.total)
        : entries;
  const card = (e) => {
    const { code, region, color, total, y, br } = e;
    const spark = sparkline(series(code, months), color);
    const yline = y
      ? (y.renewal
          ? `<span class="yoy flat" title="${esc(y.renewal)}｜別業態どうしの比較になります">前年比 ${signed(y.pct)}% ⚠</span>`
          : `<span class="yoy ${y.pct >= 0 ? "up" : "down"}">前年比 ${signed(y.pct)}%</span>`)
      : `<span class="yoy flat">前年比 ―</span>`;
    const promo = storePromoSummary(code);
    const promoLine = promo
      ? `<div class="scamp">実施中 ${promo.count}件${promo.rate != null ? ` ・ 達成 <span class="${promo.rate >= 100 ? "up" : "down"}">${promo.rate.toFixed(0)}%</span>` : ""}</div>`
      : `<div class="scamp muted">実施中の販促なし</div>`;
    const budLine = br
      ? `<span class="budg ${br.rate >= 100 ? "up" : "down"}" title="${br.m} の 実績÷予算">予算 ${br.rate.toFixed(0)}%</span>`
      : "";
    const pf = storeProfit(code);
    const profLine = (pf.kt != null || pf.gp != null)
      ? `<div class="sprof">
          ${pf.kt != null ? `<span class="pf pf-kt" title="直近確定月の 売上÷客数">客単 ${yen(pf.kt)}</span>` : ""}
          ${pf.gp != null ? `<span class="pf pf-gp ${pf.gp >= 0.65 ? "up" : pf.gp < 0.60 ? "warn" : ""}" title="100−FW理論原価率（ロス・棚卸差異は含まない）">理論粗利 ${pct(pf.gp)}</span>` : ""}
        </div>`
      : "";
    return `
      <button class="scard" data-store="${code}" style="--rc:${color}">
        <div class="stop"><span class="rtag">${region}</span>${storeName(code)}</div>
        <div class="sbig">${METRIC === "cost_rate" ? "―" : man(total) + '<span class="unit">円</span>'}</div>
        ${spark}
        ${profLine}
        ${promoLine}
        <div class="sfoot">${yline}${budLine}<span class="more">詳しく →</span></div>
      </button>`;
  };
  const cards = ordered.map(card).join("");
  const sBtn = (v, l) => `<button class="ptab${v === sk ? " on" : ""}" data-listsort="${v}">${l}</button>`;
  const controls = `<div class="pctrl"><span class="pctrl-l">並べ替え</span>
    <div class="ptabs">${sBtn("region", "エリア順")}${sBtn("budget", "予算達成順")}${sBtn("yoy", "前年比順")}${sBtn("sales", "売上順")}</div></div>`;

  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <section class="block">
      <div class="bhead"><h2>店舗</h2>
        <span class="bnote">${METRIC === "cost_rate" ? "理論原価率は期間合計にできないため ― と出ます" : METRIC_LABELS[METRIC] + "・期間合計"}／前年同月比（当月の暫定は除く）</span></div>
      ${warnBox}
      ${controls}
      <div class="sgrid">${cards}</div>
    </section>
    <div class="ovrlink"><button class="linkbtn" data-view="overview">エリア・全店の一覧を見る →</button></div>
  `;
}

// ── 施策の効果ランキング（施策を全店横断で集計）──────────────────────────
// 1施策を、対象店それぞれの campEffect（確定月のみ）で合算する。
// acc を渡すとその指標で集計する。目標達成率・効果判定は「売上」で固定したいので
// salesAt を渡す（画面上部の指標セレクトに引きずられると、ドリンク売上や理論原価に
// 切り替えたときに「目標◯万円→達成◯%」の数字だけが黙って変わってしまう）。
function campaignSummary(c, acc) {
  let cur = 0, prev = 0, prevOk = true, stores = 0, monthsMax = 0, tot = 0;
  let mom = 0, momOk = true;
  // 集客（客数）も同じ期間で合算する。売上と別枠（DATA.covers）
  let cCur = 0, cPrev = 0, cPrevOk = true, cOk = false;
  for (const code of c.stores) {
    if (!hasData(code)) continue;
    tot += 1;
    const e = acc ? effectOver(code, c, acc) : campEffect(code, c);
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
    (CAMP_FILTER.status === "all" || r.s.k === CAMP_FILTER.status
      || (CAMP_FILTER.status === "review" && needsReview(r.c))) &&
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
    const range = campRange(c);
    // 対象店は「1店」ではなく店名で出す。90件のうち大半が1店の施策なので、
    // 件数だけだと一覧を見てもどの店の話か分からない（実際そうなっていた）。
    // 3店を超えたら数にする（名前を並べても読めないため）。
    const scope = campScopeLabel(c, sum.total);
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
      // 主指標＝この施策が効くはずの部門・商品。店全体はその下に参考として置く。
      const t = campTargeted(c);
      const head = t
        ? `<b>${esc(t.label)} ${man(t.cur)}円</b>　${
            t.pct != null
              ? `<span class="${t.pct >= 0 ? "up" : "down"}">前年比 ${signed(t.pct)}%</span>`
              : `<span class="muted">前年比 ―</span>`
          }<span class="sub">（確定${t.months}ヶ月・${t.stores}店）</span>`
        : campBasis(c)
          ? `<span class="muted">対象部門・商品がまだABCに出ていません</span>`
          : `<span class="muted">この販促を何で測るか未設定 — 対象の部門（例: コース）か商品名を決めると数字が出ます</span>`;
      effHtml = `${head}<div class="ceff sub2" title="${esc(overlapNote(c))}">店全体 ${man(sum.cur)}円　${yoy}${mom}<span class="sub">（確定${sum.months}ヶ月・${sum.stores}店／${esc(overlapNote(c))}）</span></div>`;
      // 集客（客数）。売上表示のときだけ、同期間の客数を前年比つきで添える
      if (METRIC === "sales" && sum.covers != null) {
        const cy = sum.coversPct != null
          ? `<span class="${sum.coversPct >= 0 ? "up" : "down"}">前年比 ${signed(sum.coversPct)}%</span>` : "前年 ―";
        effHtml += `<div class="ceff sub2">集客 <b>${nin(sum.covers)}</b>・${cy}</div>`;
      }
    }
    const goalHtml = tgt != null
      ? `<span class="cgtag">目標 ${man(tgt)}円</span>` : "";
    // 判定バッジ（PDCA）・期間進捗バー（実施中）・目標達成率（スコアボード化）
    const v = campVerdict(c);
    const vBadge = `<span class="rvbadge ${v.tone}">${v.label}</span>`;
    const prog = campProgress(c);
    const progHtml = s.k === "live"
      ? `<div class="cprog" title="期間の進捗 ${prog}%"><span style="width:${prog}%"></span></div>` : "";
    const gr = campGoalRate(c);
    const goalLine = gr
      ? `<div class="cgoalline ${gr.rate >= 100 ? "up" : "down"}">目標達成 ${gr.rate.toFixed(0)}%<span class="sub">（実績 ${man(gr.cur)}／目標 ${man(gr.target)}円）</span></div>`
      : "";
    const reviewTag = needsReview(c) ? `<span class="rvneed">⚠ 要振り返り</span>` : "";
    const memo = memoOf(c);
    const memoHtml = memo
      ? `<div class="cmemo">${escBr(memo)} <button class="goalbtn" data-memo="${campKey(c)}" title="メモを編集">✎</button></div>`
      : `<div class="cmemo muted"><button class="goalbtn add" data-memo="${campKey(c)}">＋ 要因メモ</button></div>`;
    return `<li data-camp="${c.id}">
      <span class="kchip" style="--kc:${k.color}">${k.label}</span>
      <div class="cbody">
        <div class="ctitle">${c.title}<span class="tagx">${scope}</span>
          <span class="cstat ${s.k}">${s.label}</span>${vBadge}${goalHtml}${reviewTag}</div>
        ${c.note ? `<div class="cnote">${c.note}</div>` : ""}
        ${progHtml}
        <div class="ceff">${effHtml}</div>
        ${goalLine}
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
  const needReviewN = all.filter(r => needsReview(r.c)).length;
  const statusChips = [["all", "すべて"], ["live", "実施中"], ["soon", "予定"], ["done", "終了"],
    ["review", `要振り返り${needReviewN ? " " + needReviewN : ""}`]]
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
// アプリ内アップロードの制作物（/api/creatives）。台帳(yaml)由来と統合して表示する。
let UPLOADED_CREATIVES = [];
let CREATIVES_API_OK = false;
// 書き込み可否。開放モード（ログイン無し公開）は閲覧専用なので、追加/削除/目標/メモの
// ボタンを出さない（サーバも 403 で弾くが、押せないほうが親切）。
let WRITE_OK = true;
async function fetchServerCreatives() {
  try {
    const res = await fetch("/api/creatives", { headers: { accept: "application/json" }, cache: "no-store" });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || !ct.includes("application/json")) return;   // プレビューはHTML→アップロード不可
    const data = await res.json();
    if (data && Array.isArray(data.creatives)) { UPLOADED_CREATIVES = data.creatives; CREATIVES_API_OK = true; }
    if (data && data.readonly) WRITE_OK = false;
  } catch (e) { /* API 無し → 台帳ぶんだけ表示 */ }
}
const allCreatives = () => (DATA.creatives || []).concat(UPLOADED_CREATIVES);
// 施策に紐づく制作物（台帳＋アップロード両方）
const creativesForCampaign = id => allCreatives().filter(cr => cr.campaign_id === id);

// ── 販促プラン（アプリ内で起票・複製する計画。/api/plans）───────────────────
// 台帳(schedule.yaml)由来の「確定した販促」に、アプリで起票した「計画」を足して
// 年間ビュー/PDCA に一緒に並べる。planned=true で「計画」と分かるように印を付ける。
let PLANS = [];
let PLANS_API_OK = false;
let BASE_CAMPAIGNS = null;   // 台帳由来のオリジナル（プラン反映のたびに再結合する土台）
const isPlan = c => !!(c && c.planned);
function planToCamp(p) {
  return {
    id: p.id, stores: [p.store_code], scope_all: false, title: p.title,
    kind: p.kind || "dev", bucket: p.bucket || undefined,
    start: p.start, end: p.end, planned: true, plan_goal: p.goal, plan_note: p.note || "",
  };
}
function applyPlans() {
  if (!DATA) return;
  if (BASE_CAMPAIGNS === null) BASE_CAMPAIGNS = (DATA.campaigns || []).slice();
  DATA.campaigns = BASE_CAMPAIGNS.concat(PLANS.map(planToCamp));
}
async function fetchServerPlans() {
  try {
    const res = await fetch("/api/plans", { headers: { accept: "application/json" }, cache: "no-store" });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || !ct.includes("application/json")) return;
    const data = await res.json();
    if (data && Array.isArray(data.plans)) { PLANS = data.plans; PLANS_API_OK = true; applyPlans(); }
  } catch (e) { /* API 無し → 台帳ぶんだけ */ }
}
const planById = id => PLANS.find(p => p.id === id) || null;

// ── ログイン状態（誰で入っているか・書き込めるか）───────────────────────────
let ME = { name: "", role: "open", via: "open", canWrite: false, owner: false };
async function fetchServerMe() {
  try {
    const res = await fetch("/api/me", { headers: { accept: "application/json" }, cache: "no-store" });
    const ct = res.headers.get("content-type") || "";
    if (!res.ok || !ct.includes("application/json")) return;
    const d = await res.json();
    if (d && typeof d === "object") { ME = d; WRITE_OK = !!d.canWrite; }
  } catch (e) { /* API 無し（プレビュー等）→ 既定のまま */ }
}
// 上部バーのアカウント表示。未ログイン=「ログイン」、ログイン中=「名前・ログアウト」。
function paintAccount() {
  const btn = document.getElementById("logoutbtn");
  if (!btn) return;
  const ic = btn.querySelector(".tbtn-ic"), t = btn.querySelector(".tbtn-t");
  if (ME.via === "open") {
    btn.href = "/login"; btn.setAttribute("aria-label", "ログイン");
    if (ic) ic.textContent = "→"; if (t) t.textContent = "ログイン";
  } else {
    btn.href = "/logout"; btn.setAttribute("aria-label", "ログアウト");
    if (ic) ic.textContent = "⏻";
    if (t) t.textContent = `${ME.name || ""}${ME.owner ? "（管理）" : ""}・ログアウト`;
  }
}

// 画面から施策/店にファイルを足す。PDF・画像・Excel対応。押すとその場でファイル選択。
function promptUpload(campaign, store) {
  if (!CREATIVES_API_OK) { alert("アップロードは本番（ログイン済み）でのみ使えます。"); return; }
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".pdf,.jpg,.jpeg,.png,.webp,.gif,.xlsx,.xls,.csv,image/*,application/pdf";
  input.addEventListener("change", () => { const f = input.files && input.files[0]; if (f) uploadCreative(f, campaign, store); });
  input.click();
}
async function uploadCreative(file, campaign, store) {
  const fd = new FormData();
  fd.append("file", file);
  if (campaign) fd.append("campaign", campaign);
  if (store) fd.append("store", store);
  fd.append("title", (file.name || "資料").replace(/\.[^.]+$/, ""));
  try {
    const res = await fetch("/api/creatives", { method: "POST", body: fd });
    if (res.status === 401) { alert("アップロードにはログインが必要です。"); return; }
    if (res.status === 413) { alert("ファイルが大きすぎます（25MBまで）。"); return; }
    if (res.status === 415) { alert("対応していない形式です（PDF・画像・Excel）。"); return; }
    if (!res.ok) { alert("アップロードに失敗しました。時間をおいて再度お試しください。"); return; }
    const data = await res.json();
    if (data && data.creative) UPLOADED_CREATIVES.push(data.creative);
    render();
  } catch (e) { alert("アップロードに失敗しました（通信エラー）。"); }
}
// 販促プランの起票・編集モーダル。seed で初期値（複製元 or 既存プラン）を渡す。
// 保存は /api/plans（ログイン必須）。開放モードや未ログインでは呼ばれない。
const PLAN_KIND_OPTS = ["osusume", "lunch", "bounenkai", "gm", "dev", "closure"];
function openPlanEditor(seed) {
  if (!PLANS_API_OK) { alert("販促の起票は本番（ログイン済み）でのみ使えます。"); return; }
  const s = seed || {};
  const code = s.store_code;
  const cats = (catRules(code) && catRules(code).categories || []).map(c => c.name);
  const ym = d => (d ? String(d).slice(0, 7) : "");
  let ov = document.getElementById("planedit");
  if (ov) ov.remove();
  ov = document.createElement("div");
  ov.id = "planedit"; ov.className = "crprev";
  const kindOpts = PLAN_KIND_OPTS.map(k => `<option value="${k}"${(s.kind || "osusume") === k ? " selected" : ""}>${esc(kindOf(k).label)}</option>`).join("");
  const bucketOpts = ['<option value="">（指定なし）</option>']
    .concat(cats.map(n => `<option value="${esc(n)}"${s.bucket === n ? " selected" : ""}>${esc(n)}</option>`)).join("");
  ov.innerHTML = `<div class="crprev-bd" data-planclose></div>
    <div class="crprev-box planbox" role="dialog" aria-modal="true">
      <div class="crprev-bar"><span class="crprev-title">${s.id ? "販促プランを編集" : "販促プランを起票"}</span>
        <button class="crprev-x" type="button" data-planclose aria-label="閉じる">×</button></div>
      <div class="planform">
        <label class="pf-l">販促名<input class="pf-in" id="pf-title" type="text" value="${esc(s.title || "")}" placeholder="例：秋のパフェフェア"></label>
        <div class="pf-row">
          <label class="pf-l">種類<select class="pf-in" id="pf-kind">${kindOpts}</select></label>
          <label class="pf-l">対象区分<select class="pf-in" id="pf-bucket">${bucketOpts}</select></label>
        </div>
        <div class="pf-row">
          <label class="pf-l">開始月<input class="pf-in" id="pf-start" type="month" value="${esc(ym(s.start))}"></label>
          <label class="pf-l">終了月<input class="pf-in" id="pf-end" type="month" value="${esc(ym(s.end))}"></label>
        </div>
        <label class="pf-l">目標（円・任意）<input class="pf-in" id="pf-goal" type="number" inputmode="numeric" value="${s.goal != null ? s.goal : ""}" placeholder="例：1500000"></label>
        <label class="pf-l">メモ（任意）<textarea class="pf-in" id="pf-note" rows="2" placeholder="狙い・段取りなど">${esc(s.note || "")}</textarea></label>
        <div class="pf-msg" id="pf-msg" hidden></div>
        <div class="pf-actions">
          ${s.id ? `<button class="pf-del" type="button" data-plandelete="${esc(s.id)}">削除</button>` : "<span></span>"}
          <div>
            <button class="pf-cancel" type="button" data-planclose>キャンセル</button>
            <button class="pf-save" type="button" id="pf-save">${s.id ? "保存" : "起票する"}</button>
          </div>
        </div>
      </div>
    </div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click", e => { if (e.target.hasAttribute("data-planclose")) ov.remove(); });
  ov.querySelector("#pf-save").addEventListener("click", () => savePlanFromForm(s, code));
  const del = ov.querySelector("[data-plandelete]");
  if (del) del.addEventListener("click", () => deletePlan(del.dataset.plandelete));
  const t = ov.querySelector("#pf-title"); if (t) t.focus();
}
function lastDayOfMonth(ym) { const [y, m] = ym.split("-").map(Number); return new Date(y, m, 0).getDate(); }
async function savePlanFromForm(seed, code) {
  const g = id => document.getElementById(id);
  const msg = g("pf-msg");
  const show = m => { if (msg) { msg.textContent = m; msg.hidden = false; } };
  const title = (g("pf-title").value || "").trim();
  const sm = g("pf-start").value, em = g("pf-end").value;
  if (!title) return show("販促名を入れてください。");
  if (!sm || !em) return show("開始月と終了月を入れてください。");
  if (em < sm) return show("終了月は開始月より後にしてください。");
  const payload = {
    id: seed.id || "",
    store_code: code,
    title,
    kind: g("pf-kind").value,
    bucket: g("pf-bucket").value,
    start: `${sm}-01`,
    end: `${em}-${String(lastDayOfMonth(em)).padStart(2, "0")}`,
    goal: g("pf-goal").value === "" ? null : Number(g("pf-goal").value),
    note: (g("pf-note").value || "").trim(),
    source_id: seed.source_id || "",
  };
  const saveBtn = g("pf-save"); if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "保存中…"; }
  try {
    const res = await fetch("/api/plans", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
    if (res.status === 401) return show("保存にはログインが必要です。");
    if (!res.ok) { const d = await res.json().catch(() => ({})); return show(d.detail || "保存に失敗しました。"); }
    const data = await res.json();
    if (data && data.plan) {
      const i = PLANS.findIndex(p => p.id === data.plan.id);
      if (i >= 0) PLANS[i] = data.plan; else PLANS.push(data.plan);
      applyPlans();
    }
    const ov = document.getElementById("planedit"); if (ov) ov.remove();
    render();
  } catch (e) {
    show("保存に失敗しました（通信エラー）。");
  } finally { if (saveBtn) { saveBtn.disabled = false; } }
}
async function deletePlan(id) {
  if (!id) return;
  if (!window.confirm("この販促プランを削除しますか？")) return;
  try {
    const res = await fetch("/api/plans", { method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ id }) });
    if (!res.ok && res.status !== 200) { alert("削除に失敗しました。"); return; }
    PLANS = PLANS.filter(p => p.id !== id);
    applyPlans();
    const ov = document.getElementById("planedit"); if (ov) ov.remove();
    render();
  } catch (e) { alert("削除に失敗しました（通信エラー）。"); }
}
// 既存の販促を複製して起票（前年の枠を今年に、など）。1年後の同月を初期値にする。
function duplicatePlan(campId, code) {
  const c = (DATA.campaigns || []).find(x => x.id === campId);
  if (!c) { openPlanEditor({ store_code: code }); return; }
  const plus1y = d => { if (!d) return ""; const [y, m, dd] = String(d).slice(0, 10).split("-"); return `${+y + 1}-${m}-${dd}`; };
  openPlanEditor({
    store_code: code, title: c.title, kind: c.kind || "osusume", bucket: c.bucket || "",
    start: plus1y(c.start), end: plus1y(c.end || c.start), goal: (isPlan(c) ? c.plan_goal : targetOf(c)) || null,
    note: "", source_id: c.id,
  });
}

// アップロードした画像・PDFを、別タブに飛ばずアプリ内の小窓で見る。
function openCreativePreview(url, mime, title, openUrl) {
  let ov = document.getElementById("crprev");
  if (!ov) {
    ov = document.createElement("div");
    ov.id = "crprev"; ov.className = "crprev"; ov.hidden = true;
    ov.innerHTML = `<div class="crprev-bd" data-crclose></div>
      <div class="crprev-box" role="dialog" aria-modal="true">
        <div class="crprev-bar"><span class="crprev-title"></span>
          <a class="crprev-open" target="_blank" rel="noopener">別タブ ↗</a>
          <button class="crprev-x" type="button" data-crclose aria-label="閉じる">×</button></div>
        <div class="crprev-body"></div>
      </div>`;
    document.body.appendChild(ov);
    ov.addEventListener("click", (e) => { if (e.target.hasAttribute("data-crclose")) closeCreativePreview(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCreativePreview(); });
  }
  const m = mime || "";
  const isImg = m.startsWith("image/");
  const isPdf = m.includes("pdf");
  const body = ov.querySelector(".crprev-body");
  body.innerHTML = isImg
    ? `<img src="${url}" alt="${esc(title || "")}">`
    : isPdf
      ? `<iframe src="${url}" title="${esc(title || "資料")}"></iframe>`
      : `<div class="crprev-dl">この形式は小窓で表示できません。<br><a href="${url}" target="_blank" rel="noopener">開く ↗</a></div>`;
  ov.querySelector(".crprev-title").textContent = title || "";
  // 「別タブ↗」は実体（PDFサムネのときは元PDF）を開く。
  ov.querySelector(".crprev-open").href = openUrl || url;
  ov.querySelector(".crprev-box").classList.remove("crprev-big");
  // 押したら拡大（もう一度で元に戻す）。画像・PDFとも、携帯/PC共通。
  body.onclick = () => ov.querySelector(".crprev-box").classList.toggle("crprev-big");
  ov.hidden = false;
}
function closeCreativePreview() {
  const ov = document.getElementById("crprev");
  if (ov) { ov.hidden = true; ov.querySelector(".crprev-body").innerHTML = ""; }
}
async function deleteCreative(id) {
  if (!confirm("この制作物を削除しますか？")) return;
  try {
    const res = await fetch("/api/creatives", {
      method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ id }),
    });
    if (!res.ok) { alert("削除に失敗しました。"); return; }
    UPLOADED_CREATIVES = UPLOADED_CREATIVES.filter(c => c.id !== id);
    render();
  } catch (e) { alert("削除に失敗しました（通信エラー）。"); }
}

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
// 部門解決: まず標準バケット（コース/ランチ/…）を完全一致で。無ければ FW生部門を
// 名前の部分一致で集約（パフェ/ケーキ/テイクアウトジェラート 等の個別部門に対応）。
function resolveDept(d, name) {
  if (!d || !name) return null;
  const b = (d.buckets || []).find(x => x.name === name);
  if (b) return b;
  const rs = (d.raw || []).filter(r => (r.name || "").includes(name));
  if (!rs.length) return null;
  const sales = rs.reduce((a, r) => a + (r.sales || 0), 0);
  const qty = rs.reduce((a, r) => a + (r.qty || 0), 0);
  const cost = rs.reduce((a, r) => a + (r.cost_rate != null ? r.sales * r.cost_rate / 100 : 0), 0);
  const tot = d.total_sales || 1;
  return { name, sales, qty, share: sales / tot, cost_rate: sales ? +(cost / sales * 100).toFixed(1) : null };
}
const bucketOf = (code, name) => resolveDept(deptFor(code), name);

// 部門構成・売れ筋は「その店の直近1ヶ月」。取込月は店ごとに違うので店の月を出す。
const abcMonthOf = code => (DATA.abc_month || {})[code] || null;
const abcMonthLbl = code => { const m = abcMonthOf(code); return m ? `（${m}）` : ""; };

// ── FW ABC 月次シリーズ（departments_monthly / products_monthly）アクセサ ───────
// 毎月ABCを取り込むと積み上がる。無ければ空＝旧・最新1ヶ月版にフォールバック。
function abcMonths(code) {
  const a = Object.keys((DATA.departments_monthly || {})[code] || {});
  const b = Object.keys((DATA.products_monthly || {})[code] || {});
  return [...new Set([...a, ...b])].sort().reverse();
}
const prevYearM = m => { const [y, mo] = m.split("-"); return `${+y - 1}-${mo}`; };
const deptBucketAtM = (code, m, name) =>
  resolveDept(((DATA.departments_monthly || {})[code] || {})[m], name);
const prodsAtM = (code, m) => ((DATA.products_monthly || {})[code] || {})[m] || [];

// ── 店の品目区分（config/store_categories.yaml 由来）──────────────────────────
// FWの部門が粗い店（ルクア=フード/ドリンクのみ）で、商品名から ケーキ/パフェ/
// ジェラート… に束ね直して売上構成を見る。ルールが無い店は null（従来どおり部門で見る）。
const catRules = code => (DATA.store_categories || {})[code] || null;
const hasCats = code => !!((DATA.categories_monthly || {})[code]);
// FWの区分見出し（例 "20:テイクアウトジェラート"）から先頭の "NN:" を落とした名前。
const groupLabel = g => (g || "").replace(/^\s*\d+\s*[:：]\s*/, "").trim();
function classifyCat(name, code, group) {
  const r = catRules(code);
  if (!r) return null;
  // 内訳（全商品に出ない0円の選択商品）は素の風味名では区分を当てられないので、
  // FW自身の区分見出し（groups マップ）を商品名より優先する。
  const gmap = r.groups || {};
  if (group) { const lb = groupLabel(group); if (gmap[lb]) return gmap[lb]; }
  const nm = name || "";
  for (const c of (r.categories || [])) {
    for (const kw of (c.keywords || [])) { if (kw && nm.includes(kw)) return c.name; }
  }
  return r.other || "その他";
}
// その店・その月の区分別構成。焼き込み（categories_monthly）を優先、無ければ商品から都度算出。
function catsAtM(code, m) {
  const pre = ((DATA.categories_monthly || {})[code] || {})[m];
  if (pre && pre.length) return pre;
  const items = prodsAtM(code, m);
  const r = catRules(code);
  if (!items.length || !r) return [];
  const total = items.reduce((a, p) => a + (p.sales || 0), 0) || 1;
  const agg = {};
  for (const p of items) {
    const c = classifyCat(p.name, code, p.group);
    (agg[c] = agg[c] || { sales: 0, count: 0 });
    agg[c].sales += p.sales || 0; agg[c].count += 1;
  }
  // 区分名の重複を除く（同名区分が2つあっても部門を二重に出さない）。
  const order = [...new Set((r.categories || []).map(c => c.name).concat(r.other || "その他"))];
  return order.filter(n => agg[n]).map(n => ({
    name: n, sales: Math.round(agg[n].sales), count: agg[n].count, share: agg[n].sales / total,
  }));
}
const catAtM = (code, m, cat) => catsAtM(code, m).find(c => c.name === cat) || null;
const prodsInCat = (code, m, cat) => prodsAtM(code, m).filter(p => classifyCat(p.name, code, p.group) === cat);

// ── 構成比セルを押すと出る「小ウインドウ」（複数可・ドラッグ移動・×で閉じる）──────
// 中身＝その区分×月の商品内訳（各商品の売上＝税抜 と、区分内の売上構成比%）。
let FWIN_SEQ = 0;
function fwinLayer() {
  let el = document.getElementById("fwins");
  if (!el) { el = document.createElement("div"); el.id = "fwins"; document.body.appendChild(el); }
  return el;
}
function fwinFront(win) { win.style.zIndex = String(1000 + (++FWIN_SEQ)); }
function fwinDrag(win, handle) {
  let sx = 0, sy = 0, ox = 0, oy = 0, on = false;
  const move = e => {
    if (!on) return;
    const p = e.touches ? e.touches[0] : e;
    win.style.left = (ox + p.clientX - sx) + "px";
    win.style.top = Math.max(0, oy + p.clientY - sy) + "px";
    e.preventDefault();
  };
  const up = () => {
    on = false;
    document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
    document.removeEventListener("touchmove", move); document.removeEventListener("touchend", up);
  };
  const down = e => {
    if (e.target.closest(".fw-x")) return;
    on = true; const p = e.touches ? e.touches[0] : e;
    sx = p.clientX; sy = p.clientY; ox = win.offsetLeft; oy = win.offsetTop; fwinFront(win);
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
    document.addEventListener("touchmove", move, { passive: false }); document.addEventListener("touchend", up);
    e.preventDefault();
  };
  handle.addEventListener("mousedown", down);
  handle.addEventListener("touchstart", down, { passive: false });
}
// ── 商品内訳／部門別の小パネル（PC=浮く小窓・複数/自由リサイズ／携帯=下シート＋タブ）──
// kind:"cat"=区分の商品内訳（点数・構成比）／"month"=その月の部門別一覧（区分ごと）。
// 販促に関わる区分・商品は色付け（promo クラス）。desc={kind,code,m,cat}
const isMobile = () => !!(window.matchMedia && window.matchMedia("(max-width: 640px)").matches);
const panelKey = d => d.kind === "month" ? `M:${d.code}:${d.m}` : `${d.code}:${d.m}:${d.cat}`;
// ── 0円サブ（選択メニュー内訳）の畳み表示 ────────────────────────────────────
// 親メイン商品の行に付ける開閉ボタン（既定＝畳む）と、開いたときの内訳（サブ）行。
// 焼き込み（export）で親に subs 配列が付き、サブはトップから外れている。古いデータで
// subs が無くても何も出さない（後方互換）。
const subKey = (code, m, name) => `${code}:${m}:${name}`;
function subToggleCtrl(code, m, p) {
  if (!p || !p.subs || !p.subs.length) return "";
  const k = subKey(code, m, p.name);
  const open = SUBS_OPEN.has(k);
  return ` <button class="fw-subtog${open ? " on" : ""}" data-subtoggle="${esc(k)}" ` +
    `aria-expanded="${open}">${open ? "▾" : "▸"} 内訳${p.subs.length}件${open ? "" : "（詳細表示）"}</button>`;
}
// 内訳（サブ）の <li> 群。名前は全文折返し（fw-pn）、点数、売上は sales>0 のときだけ。表示専用。
function subRowsHtml(code, m, p, col) {
  if (!p || !p.subs || !p.subs.length || !SUBS_OPEN.has(subKey(code, m, p.name))) return "";
  const cc = col ? ` style="--cc:${col}"` : "";
  return p.subs.map(s => {
    const q = (s.qty != null) ? ` <span class="fw-pq">${ten(s.qty)}点</span>` : "";
    const v = (s.sales > 0) ? man(s.sales) : "";
    return `<li class="fw-subitem"${cc}><span class="fw-pn">${esc(s.name)}</span>` +
      `<span class="fw-pv">${v}${q}</span></li>`;
  }).join("");
}
// 開閉ボタンの配線（その場で開閉し、開いているパネルを描き直す）。
function wireSubToggle(root) {
  root.querySelectorAll("[data-subtoggle]").forEach(el => el.addEventListener("click", e => {
    e.stopPropagation();
    const k = el.dataset.subtoggle;
    if (SUBS_OPEN.has(k)) SUBS_OPEN.delete(k); else SUBS_OPEN.add(k);
    rerenderPanels();
  }));
}
function panelContent(d) {
  const mo = +d.m.slice(5, 7);
  const hits = promoHitsForMonth(d.code, d.m);
  // 商品行の並び：既定＝売上構成比順（売上高降順）／点数順＝出品数降順。
  const prodCmp = (a, b) => PANEL_SORT === "qty"
    ? ((b.qty || 0) - (a.qty || 0)) || ((b.sales || 0) - (a.sales || 0))
    : ((b.sales || 0) - (a.sales || 0)) || ((b.qty || 0) - (a.qty || 0));
  if (d.kind === "month") {
    // その月の全部門を、各部門の商品明細まで展開して見せる。部門ごとに色分け、
    // 部門の合計は太字。商品は「出品数・売上・（部門内）売上構成比」。
    const cats = catsAtM(d.code, d.m).slice().sort((a, b) => (b.sales - a.sales) || ((b.count || 0) - (a.count || 0)));
    const tot = cats.reduce((a, c) => a + (c.sales || 0), 0);
    let html = "";
    for (const c of cats) {
      const col = catColor(c.name);
      const on = hits.cats.has(c.name);
      const catPct = tot ? Math.round((c.sales || 0) / tot * 100) : 0;
      const prods = prodsInCat(d.code, d.m, c.name).slice().sort(prodCmp);
      const catQty = prods.reduce((a, p) => a + (p.qty || 0), 0);
      html += `<li class="fw-sec${on ? " promo" : ""}" style="--cc:${col}"><span class="fw-pn"><span class="ymxcdot" style="background:${col}"></span><b>${esc(c.name)}</b>${on ? ' <span class="fw-pbadge">販促</span>' : ""}</span><span class="fw-pv"><b>${man(c.sales)}</b>${catQty ? ` <span class="fw-pq">${ten(catQty)}点</span>` : ""}<span class="fw-pp">${catPct}%</span></span></li>`;
      if (prods.length) {
        for (const p of prods) {
          // ％基準：部門別＝商品売上÷その区分売上（既定）／売上＝商品売上÷その月の全体売上。
          const ppct = PANEL_PCT === "total"
            ? (tot ? Math.round((p.sales || 0) / tot * 100) : 0)
            : (c.sales ? Math.round((p.sales || 0) / c.sales * 100) : 0);
          // 販促マークは「販促の対象商品」か「販促のある区分の“限定/おすすめ”商品」だけ。
          // GM(定番＝6か月連続)には付けない。
          const pOn = hits.items.has(p.name) || (on && isLimitedProduct(d.code, d.m, p.name));
          const q = (p.qty != null) ? ` <span class="fw-pq">${ten(p.qty)}点</span>` : "";
          html += `<li class="fw-subrow${pOn ? " promo" : ""}" style="--cc:${col}"><span class="fw-pn">${esc(p.name)}${p.rank ? ` <span class="fw-rk">${esc(p.rank)}</span>` : ""}${pOn ? ' <span class="fw-pbadge">販促</span>' : ""}${subToggleCtrl(d.code, d.m, p)}</span><span class="fw-pv">${man(p.sales)}${q}<span class="fw-pp">${ppct}%</span></span></li>`;
          html += subRowsHtml(d.code, d.m, p, col);
        }
      } else {
        html += `<li class="fw-subrow" style="--cc:${col}"><span class="muted">商品明細なし</span></li>`;
      }
    }
    const list = cats.length ? html : `<li class="muted">この月のデータがありません</li>`;
    return { key: panelKey(d), color: "var(--accent)", title: `${mo}月 部門別`, tab: `${mo}月 部門別`, sub: `${man(tot)}・${cats.length}区分`, list };
  }
  const prods = prodsInCat(d.code, d.m, d.cat).slice().sort(prodCmp);
  const tot = prods.reduce((a, p) => a + (p.sales || 0), 0);
  const totQty = prods.reduce((a, p) => a + (p.qty || 0), 0);
  // 売上構成比（全体比）のときは、その月の全区分売上を分母にする。
  const monthTot = PANEL_PCT === "total"
    ? catsAtM(d.code, d.m).reduce((a, c) => a + (c.sales || 0), 0) : 0;
  const catOn = hits.cats.has(d.cat);
  const list = prods.length ? prods.map(p => {
    const pct = PANEL_PCT === "total"
      ? (monthTot ? Math.round((p.sales || 0) / monthTot * 100) : 0)
      : (tot ? Math.round((p.sales || 0) / tot * 100) : 0);
    const qty = (p.qty != null) ? ` <span class="fw-pq">${ten(p.qty)}点</span>` : "";
    // 販促マーク：対象商品か、販促のある区分の“限定/おすすめ”商品だけ（GMは付けない）。
    const on = hits.items.has(p.name) || (catOn && isLimitedProduct(d.code, d.m, p.name));
    return `<li class="${on ? "promo" : ""}"><span class="fw-pn">${esc(p.name)}${p.rank ? ` <span class="fw-rk">${esc(p.rank)}</span>` : ""}${on ? ' <span class="fw-pbadge">販促</span>' : ""}${subToggleCtrl(d.code, d.m, p)}</span><span class="fw-pv">${man(p.sales)}${qty}<span class="fw-pp">${pct}%</span></span></li>`
      + subRowsHtml(d.code, d.m, p);
  }).join("") : `<li class="muted">この月の商品データ（FW ABC）はありません</li>`;
  const qsub = totQty ? `・計${ten(totQty)}点` : "";
  return { key: panelKey(d), color: catColor(d.cat), title: `${d.cat}｜${mo}月`, tab: `${d.cat} ${mo}月`, sub: `${man(tot)}・${prods.length}品${qsub}`, list };
}
// クリックの出し分け：PC=浮く小窓／携帯=下シート＋タブ。
function openPanel(desc) { if (isMobile()) openSheet(desc); else openWindow(desc); }
function openCompo(code, m, cat) { openPanel({ kind: "cat", code, m, cat }); }
function openMonth(code, m) { openPanel({ kind: "month", code, m }); }
// パネル内の区分行クリック → その区分の商品内訳を開く（部門別 → 商品ドリル）。
function wirePanelRows(root) {
  root.querySelectorAll("[data-compocell]").forEach(el => el.addEventListener("click", e => {
    e.stopPropagation();
    const p = el.dataset.compocell.split(":");
    openCompo(p[0], p[1], decodeURIComponent(p.slice(2).join(":")));
  }));
}
// パネル頭の並び替え・％基準トグル（PC小窓／携帯シート共通・その場で再描画）。
function panelCtrl() {
  const st = (v, l) => `<button class="ptab${PANEL_SORT === v ? " on" : ""}" data-panelsort="${v}">${l}</button>`;
  const pt = (v, l) => `<button class="ptab${PANEL_PCT === v ? " on" : ""}" data-panelpct="${v}">${l}</button>`;
  return `<div class="fw-ctl">` +
    `<span class="fw-ctl-l">並び</span><div class="ptabs">${st("share", "売上構成比")}${st("qty", "出品数")}</div>` +
    `<span class="fw-ctl-l">比率</span><div class="ptabs">${pt("dept", "部門別")}${pt("total", "売上")}</div>` +
    `</div>`;
}
function wirePanelCtrl(root) {
  root.querySelectorAll("[data-panelsort]").forEach(el => el.addEventListener("click", e => {
    e.stopPropagation(); PANEL_SORT = el.dataset.panelsort; rerenderPanels();
  }));
  root.querySelectorAll("[data-panelpct]").forEach(el => el.addEventListener("click", e => {
    e.stopPropagation(); PANEL_PCT = el.dataset.panelpct; rerenderPanels();
  }));
}
// トグル操作後、開いているパネル（PC小窓すべて＋携帯シート）をその場で描き直す。
function rerenderPanels() {
  const layer = document.getElementById("fwins");
  if (layer) [...layer.children].forEach(win => { if (win._desc) renderWindowBody(win); });
  if (SHEET_TABS.length) renderSheet();
}

// ── PC：浮く小ウインドウ（複数・ドラッグ移動・角で自由リサイズ・⤢で既定サイズ）──────
// 小窓の中身（頭・トグル・一覧）を描画して配線。トグル操作の再描画でも使い回す。
function renderWindowBody(win) {
  const c = panelContent(win._desc);
  win.innerHTML = `<div class="fw-head"><span class="fw-dot" style="background:${c.color}"></span>` +
    `<span class="fw-ti">${esc(c.title)}</span><span class="fw-sub">${c.sub}</span>` +
    `<button class="fw-sz" aria-label="大きさを切替">⤢</button>` +
    `<button class="fw-x" aria-label="閉じる">×</button></div>` +
    panelCtrl() +
    `<ul class="fw-list">${c.list}</ul>`;
  win.querySelector(".fw-x").addEventListener("click", () => win.remove());
  const SIZES = ["", "fw-lg", "fw-xl"];
  win.querySelector(".fw-sz").addEventListener("click", e => {
    e.stopPropagation();
    const cur = SIZES.findIndex(s => s && win.classList.contains(s));
    win.style.width = ""; win.style.height = "";
    win.classList.remove("fw-lg", "fw-xl");
    const next = SIZES[(cur + 1 + 1) % SIZES.length]; if (next) win.classList.add(next);
    fwinFront(win);
  });
  fwinDrag(win, win.querySelector(".fw-head"));
  wirePanelRows(win);
  wirePanelCtrl(win);
  wireSubToggle(win);
}
function openWindow(desc) {
  const layer = fwinLayer();
  const key = panelKey(desc);
  const exist = [...layer.children].find(w => w.dataset.fkey === key);
  if (exist) { fwinFront(exist); exist.classList.remove("fw-flash"); void exist.offsetWidth; exist.classList.add("fw-flash"); return; }
  const win = document.createElement("div");
  win.className = "fwin"; win.dataset.fkey = key; win._desc = desc;
  const off = layer.children.length * 20;
  win.style.left = (56 + off) + "px"; win.style.top = (84 + off) + "px";
  renderWindowBody(win);
  layer.appendChild(win); fwinFront(win);
  win.addEventListener("mousedown", () => fwinFront(win));
}

// ── 携帯：下から出るシート＋タブ（複数はタブで切替。重ならない）───────────────
let SHEET_TABS = [];   // desc[]
let SHEET_ACTIVE = "";
let SHEET_H = null;   // ユーザーが決めた高さ(px)。次の部門でも同じ高さに固定される。
// 上部ハンドルを上下ドラッグしてシートの高さを変える（決めた高さは記憶して固定）。
function fsheetResize(grab, sheet) {
  let sy = 0, sh = 0, on = false;
  const clamp = h => Math.max(200, Math.min(Math.round(window.innerHeight * 0.92), h));
  const move = e => { if (!on) return; const p = e.touches ? e.touches[0] : e; SHEET_H = clamp(sh - (p.clientY - sy)); sheet.style.height = SHEET_H + "px"; e.preventDefault(); };
  const up = () => { on = false; document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); document.removeEventListener("touchmove", move); document.removeEventListener("touchend", up); };
  const down = e => { on = true; const p = e.touches ? e.touches[0] : e; sy = p.clientY; sh = sheet.offsetHeight; document.addEventListener("mousemove", move); document.addEventListener("mouseup", up); document.addEventListener("touchmove", move, { passive: false }); document.addEventListener("touchend", up); e.preventDefault(); };
  grab.addEventListener("mousedown", down);
  grab.addEventListener("touchstart", down, { passive: false });
}
function renderSheet() {
  let sheet = document.getElementById("fsheet");
  if (!SHEET_TABS.length) { if (sheet) sheet.remove(); return; }
  if (!sheet) { sheet = document.createElement("div"); sheet.id = "fsheet"; document.body.appendChild(sheet); }
  if (!SHEET_TABS.find(d => panelKey(d) === SHEET_ACTIVE)) SHEET_ACTIVE = panelKey(SHEET_TABS[SHEET_TABS.length - 1]);
  const tabs = SHEET_TABS.map(d => {
    const c = panelContent(d);
    const col = d.kind === "month" ? "var(--accent)" : catColor(d.cat);
    return `<button class="fs-tab${c.key === SHEET_ACTIVE ? " on" : ""}" data-fstab="${esc(c.key)}">` +
      `<span class="fs-dot" style="background:${col}"></span>${esc(c.tab)}` +
      `<span class="fs-tx" data-fsclose="${esc(c.key)}">×</span></button>`;
  }).join("");
  const act = SHEET_TABS.find(d => panelKey(d) === SHEET_ACTIVE);
  const c = panelContent(act);
  sheet.innerHTML = `<div class="fs-grab" aria-label="高さ調整"></div>` +
    `<div class="fs-tabs">${tabs}</div>` +
    `<div class="fs-sub"><span class="fw-dot" style="background:${c.color}"></span><b>${esc(c.title)}</b>　${c.sub}` +
    `<button class="fs-close" data-fsdismiss="1">閉じる</button></div>` +
    panelCtrl() +
    `<ul class="fw-list fs-list">${c.list}</ul>`;
  // 高さは一定（既定56vh）。ユーザーが決めた高さがあればそれを固定（部門を変えても同じ）。
  sheet.style.height = (SHEET_H || Math.round(window.innerHeight * 0.56)) + "px";
  fsheetResize(sheet.querySelector(".fs-grab"), sheet);
  sheet.querySelectorAll("[data-fstab]").forEach(el => el.addEventListener("click", e => {
    if (e.target.closest("[data-fsclose]")) return;
    SHEET_ACTIVE = el.dataset.fstab; renderSheet();
  }));
  sheet.querySelectorAll("[data-fsclose]").forEach(el => el.addEventListener("click", e => {
    e.stopPropagation();
    SHEET_TABS = SHEET_TABS.filter(d => panelKey(d) !== el.dataset.fsclose); renderSheet();
  }));
  sheet.querySelectorAll("[data-fsdismiss]").forEach(el => el.addEventListener("click", () => { SHEET_TABS = []; renderSheet(); }));
  wirePanelRows(sheet);
  wirePanelCtrl(sheet);
  wireSubToggle(sheet);
}
function openSheet(desc) {
  const k = panelKey(desc);
  if (!SHEET_TABS.find(d => panelKey(d) === k)) SHEET_TABS.push(desc);
  SHEET_ACTIVE = k; renderSheet();
}
// 品目区分の固定色（構成比バーと区分行を同色でつなぐ）
const CAT_COLORS = {
  "パフェ": "#c06a9e", "ケーキ": "#b5651d", "ジェラート": "#4a7fb5",
  "ドリンク": "#2e8b57", "フード": "#8a5d00", "コラボ": "#9b59b6",
  "コース": "#b5651d", "ランチ": "#2e8b57", "アラカルト": "#4a7fb5",
  "飲み放題": "#4a7fb5", "食べ放題": "#c06a9e", "その他": "var(--ink-3)",
};
const catColor = name => CAT_COLORS[name] || "var(--ink-3)";
// 部門（コース/ランチ…）で引けなければ品目区分（パフェ/ケーキ…）で引く統一アクセサ。
// 施策の効果・構成比を、店に合った粒度で出すために両対応にする。qty は区分では
// 「出数」ではなく品目数なので、区分由来のときは点数を出さない（null）。
function bucketOrCatAtM(code, m, name) {
  const b = deptBucketAtM(code, m, name);
  if (b) return b;
  const c = catAtM(code, m, name);
  if (c) return { name, sales: c.sales, share: c.share, qty: null, count: c.count, viaCategory: true };
  return null;
}

// 施策の対象店の書き方。1〜3店なら店名、それ以上は件数。
// 「1店」とだけ出しても、どの店の施策か分からない。
function campScopeLabel(c, withData) {
  if (c.scope_all) return "全店";
  const codes = c.stores || [];
  if (codes.length === 0) return "対象店なし";
  if (codes.length <= 3) return codes.map(storeName).join("・");
  const n = typeof withData === "number" && withData < codes.length
    ? `${codes.length}店（実績あり ${withData}）` : `${codes.length}店`;
  return n;
}

// ── 施策の主指標（その施策が効くはずの範囲だけを見る）────────────────────
// 「効果」を対象店の総売上で見ると、同じ月に重なる施策が全部同じ数字を出す。
// 実際 1店あたり2〜3件が重なるので、秋おすすめも忘年会もGM改定も同じ前年比になる。
// 台帳の items（商品名）→ bucket（部門）→ 種類から推定した部門 の順に、
// 効くはずの範囲だけを前年同月と比べる。どれも無ければ数字を出さない
// （出せてしまうと「測り方が未設定」のまま放置されるので、あえて出さない）。
function campBasis(c) {
  const items = (c.items || []).filter(Boolean);
  const bucket = c.bucket || campKindBucket(c.kind);
  if (items.length) return { kind: "items", items, bucket };
  if (bucket) return { kind: "bucket", bucket };
  // GM改定はグランドメニューを丸ごと入れ替えるので、店全体の売上がそのまま
  // その施策の範囲。部門や商品に絞るほうがかえって狭い。
  if (c.kind === "gm") return { kind: "store" };
  return null;
}

// range を渡すと、その月だけを見る（終了日未定の施策で「1ヶ月あたり」を出すため）。
function campTargeted(c, onlyCode, range) {
  const basis = campBasis(c);
  if (!basis) return null;
  const stores0 = onlyCode ? [onlyCode] : c.stores;
  if (basis.kind === "store") {
    // 店全体が範囲。ただし重なっている施策の数は必ず添えて読ませる。
    const scoped = range ? { ...c, start: range.from + "-01", end: range.to + "-28", open_ended: false } : c;
    const sum = onlyCode
      ? campaignSummary({ ...scoped, stores: [onlyCode] }, salesAt)
      : campaignSummary(scoped, salesAt);
    if (!sum.stores) return null;
    return {
      basis, label: "店全体の売上", storeWide: true,
      cur: sum.cur, prev: sum.prev, pct: sum.pct,
      months: sum.months, stores: sum.stores,
    };
  }
  const sM = range ? range.from : c.start.slice(0, 7);
  const eM = range ? range.to : campEndM(c);
  let cur = 0, prev = 0, prevOk = true;
  const months = new Set(), stores = new Set();
  for (const code of stores0) {
    for (const m of abcMonths(code)) {
      // 当月（暫定）と未来月は入れない。店全体の効果と同じ扱い。
      if (m >= CURRENT_MONTH || m < sM || m > eM) continue;
      let v = null, pv = null;
      if (basis.kind === "items") {
        const hit = prodsAtM(code, m).filter(p => basis.items.some(kw => (p.name || "").includes(kw)));
        if (!hit.length) continue;
        v = hit.reduce((a, p) => a + (p.sales || 0), 0);
        const ph = prodsAtM(code, prevYearM(m))
          .filter(p => basis.items.some(kw => (p.name || "").includes(kw)));
        pv = ph.length ? ph.reduce((a, p) => a + (p.sales || 0), 0) : null;
      } else {
        const b = bucketOrCatAtM(code, m, basis.bucket);
        if (!b) continue;
        v = b.sales;
        const pb = bucketOrCatAtM(code, prevYearM(m), basis.bucket);
        pv = pb ? pb.sales : null;
      }
      cur += v; months.add(m); stores.add(code);
      if (pv != null) prev += pv; else prevOk = false;
    }
  }
  if (!stores.size) return null;
  return {
    basis,
    label: basis.kind === "items" ? basis.items.join("・") : basis.bucket,
    cur,
    prev: prevOk ? prev : null,
    pct: (prevOk && prev) ? (cur / prev - 1) * 100 : null,
    months: months.size,
    stores: stores.size,
  };
}

// 同じ店・同じ期間に重なっている他の施策の数。店全体の売上を効果として見せるときは
// 必ずこれを添える（その数字は重なっている施策すべてに共通のものなので）。
function campOverlap(c) {
  const sM = c.start.slice(0, 7), eM = campEndM(c);
  const mine = new Set(c.stores);
  let n = 0;
  for (const o of DATA.campaigns || []) {
    if (o.id === c.id) continue;
    if (o.start.slice(0, 7) > eM || (o.end || o.start).slice(0, 7) < sM) continue;
    if ((o.stores || []).some(x => mine.has(x))) n += 1;
  }
  return n;
}

// 店全体の数字に必ず付ける但し書き。
function overlapNote(c) {
  const n = campOverlap(c);
  return n ? `同月に重なる施策 ${n}件ぶんを含みます` : "この施策以外は重なっていません";
}

// 施策×データ紐づけ（ディスパッチャ）: 月次ABCがあれば「1ヵ月毎＋前年比」で、
// 無ければ最新1ヶ月版（campDeptCardSnapshot）で出す。
// bucket: 明示部門（コース/食べ放題/ランチ等）。無ければ種類から推定（lunch→ランチ・bounenkai→コース）。
// items: 商品名キーワード配列。該当商品の実績＋部門内構成比を出す。
const ABC_MONTHS_SHOWN = 12;
function campDeptCard(c) {
  const hasMonthly = c.stores.some(code => abcMonths(code).length);
  return hasMonthly ? campDeptCardMonthly(c) : campDeptCardSnapshot(c);
}

// 月次版: 部門(bucket)を 月×(売上/構成比/前年比)、商品内訳(items)を「表示」で月×商品。
function campDeptCardMonthly(c) {
  const bname = c.bucket || campKindBucket(c.kind);
  const items = c.items || [];
  const sections = [];

  // ① 関連部門の月次（売上・構成比・前年比）
  if (bname) {
    const blocks = c.stores.map(code => {
      const ms = abcMonths(code).filter(m => bucketOrCatAtM(code, m, bname)).slice(0, ABC_MONTHS_SHOWN);
      if (!ms.length) return "";
      const rows = ms.map(m => {
        const b = bucketOrCatAtM(code, m, bname);
        const share = Math.round((b.share || 0) * 100);
        const py = bucketOrCatAtM(code, prevYearM(m), bname);
        const yoy = (py && py.sales)
          ? `<span class="${b.sales >= py.sales ? "up" : "down"}">${signed((b.sales / py.sales - 1) * 100)}%</span>`
          : `<span class="muted">―</span>`;
        const q = b.qty ? `${b.qty.toLocaleString("ja-JP")}点` : "";
        return `<tr><td>${m}</td><td class="num"><b>${yen(b.sales)}</b></td><td class="num">${share}%</td><td class="num">${q}</td><td class="num">${yoy}</td></tr>`;
      }).join("");
      return `<div class="cdmonblk"><div class="cdih" data-store="${code}">${storeName(code)}</div>
        <div class="cdscroll"><table class="cdmon">
          <thead><tr><th>月</th><th class="num">売上</th><th class="num">構成比</th><th class="num">点数</th><th class="num">前年比</th></tr></thead>
          <tbody>${rows}</tbody></table></div></div>`;
    }).filter(Boolean).join("");
    if (blocks) sections.push(`<section class="block">
      <div class="bhead"><h2>関連部門の実績（${esc(bname)}・月次）</h2>
        <span class="bnote">FW ABC 部門・1ヵ月毎／前年同月比</span></div>
      <div class="panel">${blocks}
        <div class="cdnote">${esc(bname)}の売上・構成比を月ごとに。前年比は前年同月のABCがある月のみ。</div></div>
    </section>`);
  }

  // ② 商品内訳（items）＝「表示」で 月×商品（部門内構成比・前年比つき）
  if (items.length) {
    const blocks = c.stores.map(code => {
      const ms = abcMonths(code).slice(0, ABC_MONTHS_SHOWN);
      const monthRows = ms.map(m => {
        const b = bname ? bucketOrCatAtM(code, m, bname) : null;
        const denom = (b && b.sales) ? b.sales : (((DATA.departments_monthly || {})[code] || {})[m] || {}).total_sales || 0;
        const matched = prodsAtM(code, m).filter(p => items.some(kw => (p.name || "").includes(kw)));
        if (!matched.length) return "";
        const py = prodsAtM(code, prevYearM(m));
        const lis = matched.sort((a, b2) => b2.sales - a.sales).map(p => {
          const sh = denom ? `・${Math.round(p.sales / denom * 100)}%` : "";
          const pv = py.find(q => q.name === p.name);
          const yoy = (pv && pv.sales) ? `・<span class="${p.sales >= pv.sales ? "up" : "down"}">${signed((p.sales / pv.sales - 1) * 100)}%</span>` : "";
          const gp = prodGross(p);
          const gpc = gp != null ? `・粗${pct100(gp)}` : "";
          return `<li><span class="cdinm">${esc(p.name)}</span><span class="cdiv">${yen(p.sales)}${sh}${gpc}${yoy}</span></li>`;
        }).join("");
        return `<div class="cdmon-m"><div class="cdmm">${m}</div><ul class="cdilist">${lis}</ul></div>`;
      }).filter(Boolean).join("");
      if (!monthRows) return "";
      return `<div class="cditem"><div class="cdih" data-store="${code}">${storeName(code)}</div>${monthRows}</div>`;
    }).filter(Boolean).join("");
    const kw = items.map(esc).join("・");
    sections.push(`<section class="block">
      <div class="bhead"><h2>商品内訳（月次）</h2>
        <span class="bnote">FW ABC 商品・キーワード: ${kw}</span></div>
      <div class="panel">${blocks
        ? `<details class="cddet"><summary>商品ごとの実績を月次で表示</summary>
            <div class="cditems">${blocks}</div>
            <div class="cdnote">${bname ? esc(bname) + "内" : "部門内"}構成比＝商品売上÷部門売上。前年比は前年同月のABCがある月のみ。</div>
          </details>`
        : `<p class="muted">該当商品がまだABCに計上されていません（開始前／POS未登録／名称不一致）。名称が分かれば items を調整します。</p>`}
      </div>
    </section>`);
  }

  // ③ どちらも無指定で osusume/gm → 最新月の売れ筋（スナップショット版に委譲）
  if (!bname && !items.length) return campDeptCardSnapshot(c);
  return sections.join("");
}

// 最新1ヶ月版（月次ABCが無い時のフォールバック）。
function campDeptCardSnapshot(c) {
  const monthLbl = abcMonthLbl((c.stores || [])[0]);
  const bname = c.bucket || campKindBucket(c.kind);
  const items = c.items || [];
  const sections = [];

  // ① 関連部門の実績＋構成比
  if (bname) {
    const rows = c.stores.map(code => {
      const d = deptFor(code);
      if (!d) return "";
      const b = bucketOf(code, bname);
      if (!b || (!b.sales && !b.qty)) return `<li class="cdrow is-muted"><span class="cdnm">${storeName(code)}</span>
        <span class="sub">${esc(bname)}の計上なし</span></li>`;
      const share = Math.round((b.share || 0) * 100);
      const cr = b.cost_rate != null ? `<span class="cdcr">原価${b.cost_rate}%</span>` : "";
      const q = b.qty ? `・${b.qty.toLocaleString("ja-JP")}点` : "";
      return `<li class="cdrow" data-store="${code}">
        <span class="cdnm">${storeName(code)}</span>
        <span class="cdmet"><b>${yen(b.sales)}</b>　構成比 ${share}%${q}　${cr}</span></li>`;
    }).filter(Boolean).join("");
    if (rows) sections.push(`<section class="block">
      <div class="bhead"><h2>関連部門の実績（${esc(bname)}）</h2>
        <span class="bnote">FW ABC 部門${monthLbl}・この施策が効く区分</span></div>
      <div class="panel"><ul class="cdlist">${rows}</ul>
        <div class="cdnote">${esc(bname)}の売上・構成比が施策後に伸びているかを、確定月ごとに追ってください。</div></div>
    </section>`);
  }

  // ② 商品内訳（items 指定時）＝「表示」を押すと該当商品の実績＋部門内構成比を開く
  if (items.length) {
    const blocks = c.stores.map(code => {
      const prods = (DATA.products || {})[code] || [];
      const b = bname ? bucketOf(code, bname) : null;
      const denom = (b && b.sales) ? b.sales : ((deptFor(code) || {}).total_sales || 0);
      const matched = prods.filter(p => items.some(kw => (p.name || "").includes(kw)));
      if (!matched.length) return "";
      const lis = matched.sort((a, b2) => b2.sales - a.sales).map(p => {
        const sh = denom ? `・${bname ? esc(bname) + "内 " : ""}${Math.round(p.sales / denom * 100)}%` : "";
        const rk = p.rank ? `<span class="cdrk">${esc(p.rank)}</span>` : "";
        const gp = prodGross(p);
        const gpc = gp != null ? `・粗${pct100(gp)}` : "";
        return `<li><span class="cdinm">${esc(p.name)}</span>${rk}<span class="cdiv">${yen(p.sales)}${sh}${gpc}</span></li>`;
      }).join("");
      return `<div class="cditem"><div class="cdih" data-store="${code}">${storeName(code)}</div>
        <ul class="cdilist">${lis}</ul></div>`;
    }).filter(Boolean).join("");
    const kw = items.map(esc).join("・");
    if (blocks) {
      sections.push(`<section class="block">
      <div class="bhead"><h2>商品内訳</h2>
        <span class="bnote">FW ABC 商品${monthLbl}・キーワード: ${kw}</span></div>
      <div class="panel"><details class="cddet"><summary>商品ごとの実績を表示</summary>
        <div class="cditems">${blocks}</div>
        <div class="cdnote">${bname ? esc(bname) + "内の構成比" : "部門内の構成比"}は 商品売上÷部門売上。月次で並べるには月次ABC蓄積が必要です。</div>
      </details></div>
    </section>`);
    } else {
      sections.push(`<section class="block">
      <div class="bhead"><h2>商品内訳</h2><span class="bnote">キーワード: ${kw}</span></div>
      <div class="panel"><p class="muted">該当商品がまだABCに計上されていません（開始前／POS未登録／名称不一致）。名称が分かれば items を調整します。</p></div>
    </section>`);
    }
  }

  // ③ 部門も商品も未指定で osusume/gm → 売れ筋上位（フェア/改定の主役候補）
  if (!bname && !items.length && (c.kind === "osusume" || c.kind === "gm")) {
    const rows = c.stores.map(code => {
      const its = ((DATA.products || {})[code] || []).slice(0, 3);
      if (!its.length) return "";
      const chips = its.map((p, i) => `<span class="cdtop">${i + 1}. ${esc(p.name)} ${yen(p.sales)}</span>`).join("");
      return `<li class="cdrow" data-store="${code}"><span class="cdnm">${storeName(code)}</span>
        <span class="cdtops">${chips}</span></li>`;
    }).filter(Boolean).join("");
    if (rows) sections.push(`<section class="block">
      <div class="bhead"><h2>売れ筋（対象店）</h2>
        <span class="bnote">FW ABC 商品${monthLbl}・フェア/改定の主役候補</span></div>
      <div class="panel"><ul class="cdlist">${rows}</ul></div>
    </section>`);
  }

  return sections.join("");
}

// 施策の“販売時期”実績（abc-campaign 由来）。丸ごとの月ではなく登録期間レンジで取った実データ。
const campActual = c => (DATA.campaign_actuals || {})[c.id] || null;
function campPeriodActual(c) {
  const a = campActual(c);
  if (!a || !a.items || !a.items.length) return "";
  const tot = a.sales || 0;
  const rows = a.items.slice()
    .sort((x, y) => (y.sales - x.sales) || ((y.qty || 0) - (x.qty || 0)))
    .map(p => {
      const pct = tot ? Math.round((p.sales || 0) / tot * 100) : 0;
      const q = (p.qty != null) ? ` <span class="fw-pq">${ten(p.qty)}点</span>` : "";
      return `<li><span class="fw-pn">${esc(p.name)}</span><span class="fw-pv">${man(p.sales)}${q}<span class="fw-pp">${pct}%</span></span></li>`;
    }).join("");
  return `<section class="block">
    <div class="bhead"><h2>販売時期の実績</h2>
      <span class="bnote">${esc(c.start)}〜${esc(c.end)} の実データ（丸ごとの月ではなく販売期間ぶん）・税抜／構成比は施策内</span></div>
    <div class="panel">
      <div class="cactual-sum">売上 <b>${man(tot)}</b>・${a.items.length}品・計${ten(a.qty || 0)}点</div>
      <ul class="fw-list">${rows}</ul>
    </div></section>`;
}
// TOジェラートは0円だが「出品数(点数)」で見る。総スクープ = シングル×1＋ダブル×2＋トリプル×3。
// 各フレーバーの点数の構成比を出す（この回の販促2品は★で強調）。
// 二重計上を避けるため、容器行(TOジェラート単/双/三)・サイズ内訳(TOシングル〜)・店内(ジェラート）〜)・
// サンデーは分子(フレーバー)から除外。分母は容器数からの総スクープ。
function campGelatoCompo(c) {
  if (c.bucket !== "ジェラート") return "";
  const code = (c.stores || [])[0]; if (!code) return "";
  const months = monthRange(String(c.start).slice(0, 7), String(c.end).slice(0, 7));
  const flav = {}; let single = 0, dbl = 0, tri = 0;
  const excluded = n =>
    /^TOジェラート/.test(n) || /^TO(シングル|ダブル|トリプル)/.test(n) ||
    /^サンデー/.test(n) || /^ジェラート[）)]/.test(n) ||
    /^ジェラート(ダブル|シングル|トリプル|チケット)/.test(n);
  for (const m of months) {
    for (const p of prodsInCat(code, m, "ジェラート")) {
      const n = p.name || "", q = p.qty || 0;
      if (/^TOジェラートシングル/.test(n)) single += q;
      else if (/^TOジェラートダブル/.test(n)) dbl += q;
      else if (/^TOジェラートトリプル/.test(n)) tri += q;
      if (excluded(n)) continue;
      flav[n] = (flav[n] || 0) + q;
    }
  }
  const scoops = single * 1 + dbl * 2 + tri * 3;
  const list = Object.entries(flav).filter(([, q]) => q > 0).sort((a, b) => b[1] - a[1]);
  if (!scoops && !list.length) return "";
  const denom = scoops || list.reduce((a, [, q]) => a + q, 0) || 1;
  const promo = (c.items || []);
  const rows = list.map(([n, q]) => {
    const pct = Math.round(q / denom * 1000) / 10;
    const hot = promo.some(k => k && n.includes(k));
    return `<li><span class="fw-pn">${hot ? "★ " : ""}${esc(n)}</span>` +
      `<span class="fw-pv"><span class="fw-pq">${ten(q)}点</span><span class="fw-pp">${pct}%</span></span></li>`;
  }).join("");
  return `<section class="block">
    <div class="bhead"><h2>TOジェラート 出品数構成比</h2>
      <span class="bnote">${esc(c.start)}〜${esc(c.end)}／ジェラートは0円のため出品数(点数)で見る。総スクープ＝シングル×1＋ダブル×2＋トリプル×3。★＝この回の販促2品</span></div>
    <div class="panel">
      <div class="cactual-sum">総出品数(スクープ) <b>${ten(scoops)}</b>　容器内訳: 単${ten(single)}／双${ten(dbl)}／三${ten(tri)}</div>
      <ul class="fw-list">${rows}</ul>
    </div></section>`;
}
function renderCampaign(id) {
  const c = campById(id);
  if (!c) return `<div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <div class="empty">施策が見つかりません。</div>`;
  const k = kindOf(c.kind);
  const st = campStatus(c);
  const range = campRange(c);
  const sum = campaignSummary(c);
  const tgt = targetOf(c);
  const prog = campTimeProgress(c);
  const isRatio = METRIC === "cost_rate";

  // 目標（進捗欄で編集）。目標は2026年10月分から。過ぎた施策には出さない。
  const goalBtn = !goalEligible(c) ? ""
    : tgt != null
      ? `<button class="goalbtn" data-goal="${campKey(c)}" title="目標を編集">目標 ${man(tgt)}円 ✎</button>`
      : `<button class="goalbtn add" data-goal="${campKey(c)}">＋ 目標を入力</button>`;

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
    // 目標は円（売上）で立てるので、達成率は必ず売上で割る。
    const gr = campGoalRate(c);
    const goalKpi = tgt != null
      ? `<div class="kpi"><div class="lbl">目標達成${gr && gr.label ? `（${esc(gr.label)}${gr.monthly ? `・${gr.month}の1ヶ月` : ""}）` : ""}</div>
          <div class="big ${gr && gr.rate >= 100 ? "up" : "down"}">${gr ? gr.rate.toFixed(0) + "%" : "―"}</div>
          <div class="delta">目標 ${man(tgt)} → 実績 ${gr ? man(gr.cur) : "―"}</div></div>` : "";
    const covKpi = (METRIC === "sales" && sum.covers != null)
      ? `<div class="kpi"><div class="lbl">集客（確定分）</div><div class="big">${nin(sum.covers)}</div>
          <div class="delta">${sum.coversPct != null ? `<span class="${sum.coversPct >= 0 ? "up" : "down"}">前年比 ${signed(sum.coversPct)}%</span>` : "前年比 ―"}</div></div>` : "";
    // 主指標＝この施策が効くはずの部門・商品。先頭に置く。
    const t = campTargeted(c);
    const tgtKpi = t
      ? `<div class="kpi"><div class="lbl">${esc(t.label)}（確定${t.months}ヶ月・${t.stores}店）</div>
          <div class="big ${t.pct != null ? (t.pct >= 0 ? "up" : "down") : ""}">${
            t.pct != null ? signed(t.pct) + "%" : "―"
          }</div>
          <div class="delta">${man(t.cur)}${t.prev != null ? `（前年 ${man(t.prev)}）` : "・前年のABCなし"}</div></div>`
      : `<div class="kpi"><div class="lbl">この施策の効果</div>
          <div class="big">―</div>
          <div class="delta">${
            campBasis(c)
              ? "対象の部門・商品がまだABCに出ていません"
              : "何で測るかが未設定です。対象の部門（例: コース）か商品名を決めてください"
          }</div></div>`;
    overall = `<div class="kpis">
      ${tgtKpi}
      <div class="kpi"><div class="lbl">店全体の${METRIC_LABELS[METRIC]}（参考・確定${sum.months}ヶ月・${sum.stores}/${sum.total}店）</div>
        <div class="big">${man(sum.cur)}<span class="unit">円</span></div>
        <div class="delta">${yoy}　${mom}<br><span class="sub">${esc(overlapNote(c))}</span></div></div>
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
    ? `<div class="cmemo">${escBr(memo)} <button class="goalbtn" data-memo="${campKey(c)}" title="メモを編集">✎</button></div>`
    : `<div class="cmemo muted"><button class="goalbtn add" data-memo="${campKey(c)}">＋ 要因メモ</button></div>`;

  // POP・制作物（この施策に紐づくもの）＋アップロード導線
  const crs = creativesForCampaign(id);
  const crAdd = CREATIVES_API_OK
    ? `<button class="upbtn" data-upload="campaign:${c.id}">＋ POP・写真・資料を追加</button>` : "";
  const crBlock = (crs.length || CREATIVES_API_OK)
    ? `<section class="block"><div class="bhead"><h2>POP・制作物</h2><span class="bnote">${crs.length}件</span></div>
        ${crs.length ? `<div class="cgrid">${crs.map(creativeCard).join("")}</div>`
          : `<p class="muted" style="margin:2px 0 10px">まだありません。PDF・写真・Excelを追加できます。</p>`}
        ${crAdd}</section>` : "";

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
    ${campPeriodActual(c)}
    ${campGelatoCompo(c)}
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
    `<span class="ccmp-i">ランチ構成比 ${m.lunchShare == null ? "―" : pct(m.lunchShare)}</span></div>`;
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
      const sig = [`${per1(m.dailyPerDay)}食/日`, `原価 ${pct(m.dailyCost)}`];
      if (m.lunchShare != null) sig.push(`構成比 ${pct(m.lunchShare)}`);
      if (m.dailyCost > 0.32) return { tone: "warn", label: "原価高め", signals: sig };
      // 構成比が取れていない店は、このしきい値で判定しない。
      if (m.lunchShare != null && m.lunchShare >= 0.12) return { tone: "good", label: "定着", signals: sig };
      return { tone: "flat", label: "様子見", signals: sig };
    }
  }
  {
    // その施策が効くはずの範囲（部門・商品）で判定する。店全体の売上で判定すると、
    // 同月に重なっている施策が全部そろって「効果あり」か「要改善」になる。
    const t = campTargeted(c);
    if (!campBasis(c)) {
      return {
        tone: "flat", label: "測り方 未設定",
        signals: ["対象の部門か商品名を決めると、この販促だけの数字が出ます"],
      };
    }
    if (t && t.pct != null) {
      const sig = [`${t.label} 前年比 ${signed(t.pct)}%`, `確定${t.months}ヶ月・${t.stores}店`];
      if (t.storeWide) sig.push(overlapNote(c));
      return t.pct >= 0
        ? { tone: "good", label: "効果あり", signals: sig }
        : { tone: "warn", label: "要改善", signals: sig };
    }
    if (t) {
      return {
        tone: "flat", label: "前年比なし",
        signals: [`${t.label} ${man(t.cur)}円`, "前年同月のABCが無いため前年比は出せません"],
      };
    }
  }
  return { tone: "wait", label: campStatus(c).k === "soon" ? "開始前" : "計測中", signals: [] };
}

// 店ページ用の「この販促の効き」。その店ぶんの対象区分（bucket/items）の前年比で
// ◎/△ を出す。数字が無ければ理由（未設定／確定待ち／開始前）を返す。効果順の並べ替え・
// 効果サマリ・行の判定バッジで共通に使う。全店judgeの campVerdict とは別に、店1軒で見る。
function storeCampEffect(c, code) {
  const t = campTargeted(c, code);
  if (t && t.pct != null) {
    return {
      measured: true, pct: t.pct, cur: t.cur, label: t.label, months: t.months,
      tone: t.pct >= 0 ? "good" : "warn", mark: t.pct >= 0 ? "◎" : "△",
      text: t.pct >= 0 ? "効いた" : "要改善",
    };
  }
  const k = campStatus(c).k;
  if (!campBasis(c)) return { measured: false, tone: "flat", mark: "", state: "測り方 未設定" };
  return { measured: false, tone: "wait", mark: "", state: k === "soon" ? "開始前" : "確定待ち" };
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
    ? `<div class="cmemo">${escBr(memo)} <button class="goalbtn" data-memo="${campKey(c)}" title="メモを編集">✎</button></div>`
    : `<div class="cmemo muted"><button class="goalbtn add" data-memo="${campKey(c)}">＋ 要因メモ</button></div>`;
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
  // その施策が効くはずの範囲があればそれを出す。無ければ店全体だが、必ず
  // 「重なっている施策の数」を添えて、施策の成績と読ませない。
  const t = campTargeted(c);
  if (t) {
    const yoy = t.pct != null
      ? `<span class="${t.pct >= 0 ? "up" : "down"}">前年比 ${signed(t.pct)}%</span>` : "";
    return `<div class="ccmp"><span class="ccmp-i">${esc(t.label)} <b>${man(t.cur)}円</b></span>` +
      (yoy ? `<span class="ccmp-i">${yoy}</span>` : "") +
      `<span class="ccmp-i sub">確定${t.months}ヶ月・${t.stores}店</span></div>`;
  }
  if (!campBasis(c)) {
    return `<div class="ccmp"><span class="ccmp-i sub">この販促を何で測るか未設定 — 対象の部門（例: コース）か商品名を決めると数字が出ます</span></div>`;
  }
  const sum = campaignSummary(c);
  if (sum.stores) {
    const yoy = sum.pct != null ? `<span class="${sum.pct >= 0 ? "up" : "down"}">前年比 ${signed(sum.pct)}%</span>` : "";
    const mom = sum.momPct != null ? `・<span class="${sum.momPct >= 0 ? "up" : "down"}">前月比 ${signed(sum.momPct)}%</span>` : "";
    return `<div class="ccmp"><span class="ccmp-i">店全体 <b>${man(sum.cur)}円</b></span>` +
      (yoy ? `<span class="ccmp-i">${yoy}${mom}</span>` : "") +
      `<span class="ccmp-i sub">確定${sum.months}ヶ月・${sum.stores}店／${esc(overlapNote(c))}</span></div>`;
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
      const range = campRange(c);
      const scope = campScopeLabel(c);
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

// 収益性ランキング（横断）: 直近確定月の 客単価(=売上/客数) と 粗利率(=1-原価率)。
// 既存データ（monthly・covers・cost_rate）から算出。店長会資料DLの代替を全店に展開。
function crossProfit() {
  const crAt = (c, m) => (DATA.cost_rate[c] || {})[m];
  return (DATA.stores || []).map(s => {
    const code = s.code;
    const ls = latestWith(code, salesAt);
    let kt = null;
    if (ls) {
      const cov = coversAt(code, ls.m);
      if (typeof cov === "number" && cov > 0) kt = ls.v / cov;
    }
    const lcr = latestWith(code, crAt);
    return {
      code, name: s.name, brand_name: s.brand_name,
      kt, gp: lcr ? 1 - lcr.v : null,
      // 対象月は店ごとに違う（取込の進み方が揃っていない）。同じ表に並べる以上、
      // どの月の数字なのかを行ごとに出さないと、月をまたいだ順位づけになる。
      ktM: ls ? ls.m : null,
      gpM: lcr ? lcr.m : null,
    };
  }).filter(x => x.kt != null || x.gp != null);
}

// 原価改善の狙い目部門：原価額(=売上×原価率)の大きい順にTOP n。bucket.cost_rate は % 単位。
function deptCostTargets(code, n = 3) {
  const d = deptFor(code);
  if (!d || !d.buckets) return [];
  return d.buckets
    .filter(b => b.cost_rate != null && typeof b.sales === "number" && b.sales > 0)
    .map(b => ({ name: b.name, cost_rate: b.cost_rate, sales: b.sales, cost: b.sales * b.cost_rate / 100 }))
    .sort((a, b) => b.cost - a.cost)
    .slice(0, n);
}

// 原価率の前月比トレンド。直近確定月と、その一つ前（確定・cost_rateあり）を比較。
// deltaPt は pt差（+＝原価率上昇＝悪化）。材料が2ヶ月分なければ null。
// 原価率の「前月比」。隣り合った2ヶ月でなければ出さない。
//
// 以前は「原価率が入っている直近2ヶ月」を拾うだけで、隣接しているか見ていなかった。
// 原価率は月によって欠けることがあるので、10ヶ月離れた2点の差を「前月比 +x.xpt」と
// 表示し、そのまま原価アラートを鳴らしていた。
function costTrend(code) {
  const cr = DATA.cost_rate[code] || {};
  let cur = null;
  for (let i = DATA.months.length - 1; i >= 0; i--) {
    const m = DATA.months[i];
    if (m >= CURRENT_MONTH) continue;
    if (typeof cr[m] === "number") { cur = m; break; }
  }
  if (!cur) return null;
  const prev = addMonth(cur, -1);
  if (typeof cr[prev] !== "number") return null;   // 前月が無ければ前月比は出さない
  return { cur, prev, curV: cr[cur], prevV: cr[prev], deltaPt: (cr[cur] - cr[prev]) * 100 };
}

// 全店の宴会（=コース部門）まとめ。各店の直近ABC月のコース売上・構成比・前年比。
// 月次ABCがあれば前年同月比、無ければ最新スナップショット（前年比―）。
function crossBanquet() {
  return (DATA.stores || []).map(s => {
    const code = s.code;
    let cur = null, curM = null;
    for (const m of abcMonths(code)) {
      const b = deptBucketAtM(code, m, "コース");
      if (b && b.sales) { cur = b; curM = m; break; }
    }
    if (!cur) { const b = bucketOf(code, "コース"); if (b && b.sales) cur = b; }
    if (!cur) return null;
    let yoy = null;
    if (curM) { const py = deptBucketAtM(code, prevYearM(curM), "コース"); if (py && py.sales) yoy = (cur.sales / py.sales - 1) * 100; }
    return { code, name: s.name, brand_name: s.brand_name, sales: cur.sales, share: cur.share, cost_rate: cur.cost_rate, month: curM, yoy };
  }).filter(Boolean).sort((a, b) => b.sales - a.sales);
}

// 原価率が前月比 +th pt 以上に悪化した店（悪化幅の大きい順）。TOPタイルと横断アラートで共用。
const COST_ALERT_TH = 1.0;
function costAlerts(th = COST_ALERT_TH) {
  return (DATA.stores || [])
    .map(s => ({ s, t: costTrend(s.code) }))
    .filter(x => x.t && x.t.deltaPt >= th)
    .sort((a, b) => b.t.deltaPt - a.t.deltaPt);
}

function renderCross() {
  const brands = crossByBrand();
  const nStores = crossStores().length;
  if (!nStores) {
    return `<div class="crumb"><button class="linkbtn" data-view="schedule">← 予定表</button></div>
      <section class="block"><div class="bhead"><h2>販促ターゲット（横断）</h2></div>
      <div class="panel"><p class="muted">部門データがまだありません。abc-store-ingest で店舗別ABCを取り込むと表示されます。</p></div></section>`;
  }
  // 店ごとに取込月が違うので、単月を名乗らない（各行に対象月を出す）。
  const monthLbl = "";

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
    ${(() => {
      const prof = crossProfit().filter(x => x.kt != null).sort((a, b) => b.kt - a.kt);
      if (!prof.length) return "";
      const maxKt = Math.max(1, ...prof.map(x => x.kt));
      // 粗利率ワースト＝原価改善の狙い目。粗利率のある店の下位3、かつ粗利率60%未満（原価率40%超）。
      const withGp = prof.filter(x => x.gp != null);
      const worst = new Set(
        withGp.slice().sort((a, b) => a.gp - b.gp).filter(x => x.gp < 0.60).slice(0, 3).map(x => x.code),
      );
      const rows = prof.map((x, i) => {
        const isWorst = worst.has(x.code);
        const gpCls = x.gp != null && x.gp >= 0.65 ? "up" : (x.gp != null && x.gp < 0.60 ? "warn" : "");
        // ワースト店は原価改善の狙い目部門TOP3を名指し（原価額の大きい順）
        let aim = "";
        if (isWorst) {
          const t = deptCostTargets(x.code, 3);
          aim = t.length
            ? `<span class="xpaim"><em>狙い目</em>${t.map(b =>
                `<span class="xpaimc" style="--dc:${DEPT_COLORS[b.name] || "var(--ink-3)"}">${esc(b.name)}<b>原価${b.cost_rate}%</b><small>${man(b.cost)}円</small></span>`).join("")}</span>`
            : `<span class="xpaim muted">部門データ未取得（abc-store-ingest で回収）</span>`;
        }
        return `
        <li class="xprow${isWorst ? " xpworst" : ""}" data-store="${x.code}">
          <span class="xpno">${i + 1}</span>
          <span class="xpname">${esc(x.name)}<small>${esc(x.brand_name || "")}</small>${isWorst ? '<span class="xptag">原価改善の狙い目</span>' : ""}</span>
          <span class="xpbar"><span class="xpfill" style="width:${Math.max(4, Math.round(x.kt / maxKt * 100))}%"></span></span>
          <span class="xpkt">${yen(x.kt)}<small>客単価${x.ktM ? "・" + x.ktM : ""}</small></span>
          <span class="xpgp ${gpCls}">${x.gp != null ? pct(x.gp) : "—"}<small>理論粗利率${x.gpM ? "・" + x.gpM : ""}</small></span>
          ${aim}
        </li>`;
      }).join("");
      const worstNote = worst.size
        ? `<div class="bnote" style="margin-top:8px"><b class="warn">原価改善の狙い目</b>＝粗利率60%未満（原価率40%超）の下位${worst.size}店。各行の<b>狙い目</b>は原価額（売上×原価率）が大きい部門TOP3＝ここを直すと効きます（値付け/レシピ/仕入れ）。</div>`
        : "";
      return `
    <section class="block">
      <div class="bhead"><h2>収益性ランキング（横断）</h2>
        <span class="bnote">${(() => {
          const ms = [...new Set(prof.map(x => x.ktM).filter(Boolean))].sort();
          const span = ms.length > 1 ? `対象月は店により ${ms[0]}〜${ms[ms.length - 1]}` : `対象月 ${ms[0] || "―"}`;
          return `各店の直近確定月の 客単価 × 理論粗利率　${prof.length}店　${span}`;
        })()}</span></div>
      <div class="panel"><ul class="xplist">${rows}</ul>
        <div class="bnote" style="margin-top:8px">客単価＝売上÷客数、理論粗利率＝100−FW理論原価率（ロス・棚卸差異は含まない）。行タップで店舗詳細へ。</div>
        ${worstNote}
      </div>
    </section>`;
    })()}
    ${(() => {
      // 原価率の前月比トレンド悪化アラート（+1.0pt以上を悪化として名指し）
      const rows = costAlerts();
      if (!rows.length) return "";
      const items = rows.map(({ s, t }) => {
        const t3 = deptCostTargets(s.code, 2);
        const aim = t3.length
          ? `<span class="ctaim">重い部門: ${t3.map(b => `${esc(b.name)}(原価${b.cost_rate}%)`).join("・")}</span>`
          : "";
        return `<li class="ctrow" data-store="${s.code}">
          <span class="ctname">${esc(s.name)}<small>${esc(s.brand_name || "")}</small></span>
          <span class="ctmove"><b class="warn">+${t.deltaPt.toFixed(1)}pt</b>
            <small>${pct(t.prevV)}（${t.prev}）→ ${pct(t.curV)}（${t.cur}）</small></span>
          ${aim}
        </li>`;
      }).join("");
      return `
    <section class="block" id="cost-alert">
      <div class="bhead"><h2>原価率トレンド悪化アラート</h2>
        <span class="bnote">前月比で原価率が +${COST_ALERT_TH.toFixed(1)}pt 以上に悪化した店　${rows.length}店</span></div>
      <div class="panel"><ul class="ctlist">${items}</ul>
        <div class="bnote" style="margin-top:8px">直近確定月とその前月のABC原価率を比較。上昇＝利益を圧迫。行タップで店舗詳細（部門別の原価率）へ。</div>
      </div>
    </section>`;
    })()}
    ${(() => {
      // 全店 忘新年会/宴会（＝コース部門）まとめ
      const bq = crossBanquet();
      if (!bq.length) return "";
      const tot = bq.reduce((a, x) => a + x.sales, 0);
      const withYoy = bq.filter(x => x.yoy != null);
      const rows = bq.map((x, i) => {
        const share = Math.round((x.share || 0) * 100);
        const yoy = x.yoy != null
          ? `<span class="${x.yoy >= 0 ? "up" : "down"}">${signed(x.yoy)}%</span>`
          : `<span class="muted">―</span>`;
        const cr = x.cost_rate != null ? `原価${x.cost_rate}%` : "";
        return `<tr data-store="${x.code}">
          <td>${i + 1}</td>
          <td class="bqname">${esc(x.name)}<small>${esc(x.brand_name || "")}</small></td>
          <td class="num"><b>${yen(x.sales)}</b></td>
          <td class="num">${share}%</td>
          <td class="num">${cr}</td>
          <td class="num">${yoy}</td>
          <td class="bqm">${x.month || ""}</td></tr>`;
      }).join("");
      return `
    <section class="block" id="cross-banquet">
      <div class="bhead"><h2>忘新年会・宴会まとめ（全店）</h2>
        <span class="bnote">宴会=コース部門の 売上・構成比・前年比　${bq.length}店</span></div>
      <div class="panel">
        <div class="bqsum">全店 宴会（コース）売上 合計 <b>${yen(tot)}</b>
          ${withYoy.length ? `・前年比の取れた店 ${withYoy.length}店` : "・前年比は前年同月ABCの蓄積後に表示"}</div>
        <div class="cdscroll"><table class="cdmon bqtbl">
          <thead><tr><th>#</th><th>店舗</th><th class="num">宴会売上</th><th class="num">構成比</th><th class="num">原価率</th><th class="num">前年比</th><th>月</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <div class="bnote" style="margin-top:8px">行タップで店舗詳細へ。忘新年会コースの商品内訳は、各店の忘年会施策に items（コース名）を足すと「宴会内 構成比」付きで並びます。</div>
      </div>
    </section>`;
    })()}
    <section class="block">
      <div class="bhead"><h2>部門構成マトリクス</h2><span class="bnote">${legend}</span></div>
      <div class="panel xmatrix">${brandBlocks}</div>
    </section>`;
}

// ── 制作物ギャラリー（config/creatives.yaml 由来）──────────────────────────
// この店に掛かる制作物（全店ものも含む）。掲出日の新しい順は export 側で済み。
const creativesFor = code => allCreatives().filter(cr => {
  if (cr.scope_all) return true;
  if ((cr.stores || []).includes(code) || cr.store_code === code) return true;
  const c = cr.campaign_id ? campById(cr.campaign_id) : null;   // 施策紐づけの店経由
  return c ? (c.scope_all || (c.stores || []).includes(code)) : false;
});

function creativeCard(cr) {
  const k = kindOf(cr.kind);
  const mime = cr.mime || "";
  const isImg = mime.startsWith("image/");
  const isPdf = mime.includes("pdf");
  const canPreview = isImg || isPdf;   // 画像・PDFは小窓プレビュー、Excel等はボタンで開く/DL
  const label = isPdf ? "PDF"
    : isImg ? "画像"
    : /spreadsheet|excel|csv/.test(mime) ? "表"
    : ((cr.url || "").split(".").pop() || "資料").toUpperCase().slice(0, 4);
  const meta = [
    cr.date || "",
    cr.campaign_title ? "施策: " + cr.campaign_title : "",
    cr.uploaded ? ("追加" + (cr.by ? "・" + cr.by : "")) : (cr.scope_all ? "全店" : ((cr.stores || []).length ? cr.stores.length + "店" : "")),
  ].filter(Boolean).join("　·　");
  // PDFは1ページ目のサムネ画像（cr.thumb）があれば、画像として小窓表示する。
  // iframeのPDFはスマホで真っ白になるため。実体PDFは「別タブ↗」で開けるようにする。
  const hasThumb = isPdf && cr.thumb;
  const showImg = isImg || hasThumb;
  const pvUrl = hasThumb ? cr.thumb : cr.url;
  const pvMime = hasThumb ? "image/png" : mime;
  // 同一ドメイン（ログイン内）/creatives/… から配信。画像／PDFサムネはサムネ表示、他は種別バッジ。
  const view = `data-crurl="${pvUrl}" data-crmime="${esc(pvMime)}" data-crtitle="${esc(cr.title)}" data-cropen="${cr.url}"`;
  const thumb = showImg
    ? `<button type="button" class="cthumb cimg" ${view} style="--kc:${k.color}" title="小窓で開く"><img src="${pvUrl}" alt="${esc(cr.title)}" loading="lazy"></button>`
    : canPreview
      ? `<button type="button" class="cthumb" ${view} style="--kc:${k.color}" title="小窓で開く"><span class="cext">${label}</span></button>`
      : `<a class="cthumb" href="${cr.url}" target="_blank" rel="noopener" style="--kc:${k.color}" title="開く"><span class="cext">${label}</span></a>`;
  const del = (cr.uploaded && cr.id) ? `<button class="crdel" data-crdel="${cr.id}" title="削除" aria-label="削除">×</button>` : "";
  return `<div class="ccard">
    ${thumb}
    <div class="ccbody">
      <span class="kchip" style="--kc:${k.color}">${k.label}</span>
      <div class="cctitle">${esc(cr.title)}</div>
      <div class="ccmeta">${esc(meta)}</div>
      ${canPreview
        ? `<button type="button" class="pdfbtn" ${view}>${label}を開く</button>`
        : `<a class="pdfbtn" href="${cr.url}" target="_blank" rel="noopener" download>${label}を開く ↗</a>`}
    </div>${del}
  </div>`;
}

function renderGallery() {
  const all = allCreatives();
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

// 旧 storeSummary（先頭サマリ）は storeHero に統合。重複していた「今月の売上・
// 前年比・実施中の販促・振り返り残り」はヒーローが、狙いどころは storeTargetChip が持つ。

// ── 年間スケジュール → 月 → 品目区分 → 商品 のドリル ─────────────────────────
// 店ページの主眼。月を押すと構成比・単価・集客とその月の販促、区分を押すと商品詳細。
const jpMonth = m => { const [y, mo] = m.split("-"); return `${y}年${+mo}月`; };
const salesAtC = (code, m) => ((DATA.monthly[code] || {})[m] || {}).sales;

// その店・その月に走っている施策（販促期間が月に掛かるもの）。開始日順。
function campsInMonth(code, m) {
  return (DATA.campaigns || [])
    .filter(c => (c.stores || []).includes(code)
      && c.start.slice(0, 7) <= m && campEndM(c) >= m)
    .sort((a, b) => a.start < b.start ? -1 : 1);
}
// 販促が対象にしている区分（bucket）と商品（items）を、その月ぶんだけ集める。
// 小パネルで「販促に関わる部門・商品」を色付けするのに使う。
const campCat = c => c.bucket || campKindBucket(c.kind) || "その他";
function promoHitsForMonth(code, m) {
  const cats = new Set(), items = new Set();
  for (const c of campsInMonth(code, m)) {
    cats.add(campCat(c));
    for (const it of (c.items || [])) if (it) items.add(it);
  }
  return { cats, items };
}
// m から k か月前の "YYYY-MM"。
function monthMinus(m, k) {
  let y = +m.slice(0, 4), mo = +m.slice(5, 7) - k;
  while (mo <= 0) { mo += 12; y -= 1; }
  return `${y}-${String(mo).padStart(2, "0")}`;
}
// 定番(GM)か限定(おすすめ)か。直近6か月で毎月連続して出ていれば GM（＝限定でない）。
// どこか抜けがあれば限定＝おすすめ＝販促対象。履歴が浅い月は限定側に倒す（安全にマーク）。
function isLimitedProduct(code, m, name) {
  const pm = (DATA.products_monthly || {})[code] || {};
  const win = [0, 1, 2, 3, 4, 5].map(k => monthMinus(m, k));
  const dataMonths = win.filter(mm => pm[mm] && pm[mm].length);
  if (dataMonths.length < 4) return true;   // 6か月の履歴が揃っていない→限定扱い
  return dataMonths.some(mm => !(pm[mm] || []).some(p => p.name === name));
}
// 同じ店・同じ区分（bucket）の“前回の回”。前回比（直近比較）に使う。
function campPrevOccurrence(c) {
  const mine = new Set(c.stores || []);
  const start = c.start;
  const cands = (DATA.campaigns || []).filter(o =>
    o.id !== c.id
    && (o.stores || []).some(x => mine.has(x))
    && ((c.bucket && o.bucket === c.bucket) || (!c.bucket && o.kind === c.kind))
    && (o.end || o.start) < start
  ).sort((a, b) => (a.end || a.start) < (b.end || b.start) ? 1 : -1);
  return cands[0] || null;
}

// 年間スケジュール（全月をカードで。押すと月の詳細へ）
// 販促エンジンビュー：ルクアの主役（パフェ／ケーキ 等、schedule で bucket 指定のある区分）を
// 直近13ヶ月の通年の流れ（月次バー・前年比色）＋回ごと（昨対・前回比）で見せる。
function storeEngines(code) {
  const r = catRules(code);
  if (!r) return "";
  const myCamps = (DATA.campaigns || []).filter(c => (c.stores || []).includes(code) && c.bucket);
  const catNames = (r.categories || []).map(x => x.name);
  // 販促枠がある区分＝主役。売上順に並べる。
  const engineCats = [...new Set(myCamps.map(c => c.bucket))]
    .filter(name => catNames.includes(name));
  if (!engineCats.length) return "";
  const allM = [...new Set([
    ...Object.keys((DATA.categories_monthly || {})[code] || {}),
    ...Object.keys((DATA.products_monthly || {})[code] || {}),
  ])].sort();
  const months = allM.slice(-13);
  if (!months.length) return "";

  const cards = engineCats.map(cat => {
    const series = months.map(m => {
      const c = catAtM(code, m, cat);
      return { m, sales: c ? c.sales : null };
    });
    const maxS = Math.max(1, ...series.map(s => s.sales || 0));
    const bars = series.map(s => {
      if (s.sales == null) return `<div class="ebcell"><span class="eblab">${+s.m.slice(5, 7)}</span></div>`;
      const py = catAtM(code, prevYearM(s.m), cat);
      const yoy = (py && py.sales) ? (s.sales / py.sales - 1) * 100 : null;
      const cls = yoy == null ? "" : (yoy >= 0 ? " up" : " down");
      const h = Math.max(6, Math.round(s.sales / maxS * 100));
      return `<button class="ebcell" data-scat="${code}:${s.m}:${encodeURIComponent(cat)}"
        data-tip="${+s.m.slice(5, 7)}月 ${esc(cat)}｜売上 ${man(s.sales)}円${yoy != null ? `｜前年比 ${signed(yoy)}%` : ""}">
        <span class="ebbar${cls}" style="height:${h}%"></span><span class="eblab">${+s.m.slice(5, 7)}</span></button>`;
    }).join("");
    // 回ごと（この店・この区分の販促）を新しい順に、昨対・前回比つき
    const occ = myCamps.filter(c => c.bucket === cat).sort((a, b) => a.start < b.start ? 1 : -1).slice(0, 8);
    const occRows = occ.map(c => {
      const k = kindOf(c.kind), st = campStatus(c);
      const tg = campTargeted(c, code);
      // エンジンは「この枠が年々効いているか」を見る場所。昨対だけを出し、
      // 回どうしの比較（前回比）や目標・POPは下の「販促リスト」に任せる（役割分担）。
      let cmp = "";
      if (tg && tg.cur) {
        cmp = `<b>${man(tg.cur)}円</b>${tg.pct != null ? ` <span class="${tg.pct >= 0 ? "up" : "down"}">昨対${signed(tg.pct)}%</span>` : ""}`;
      }
      return `<li data-camp="${c.id}"><span class="kchip" style="--kc:${k.color}">${k.label}</span>
        <span class="eon">${esc(c.title)}<span class="cstat ${st.k} sub">${st.label}</span></span>
        <span class="eov">${cmp || '<span class="muted">確定月待ち</span>'}</span>
        <span class="eor">${campRange(c)}</span></li>`;
    }).join("");
    return `<div class="engcard">
      <div class="enghd"><span class="kdot" style="background:${catColor(cat)}"></span><b>${esc(cat)}</b><span class="muted">直近13ヶ月・前年比で色</span></div>
      <div class="engbars">${bars}</div>
      <ul class="engocc">${occRows}</ul>
    </div>`;
  }).join("");

  return `<section class="block" id="engines">
    <div class="bhead"><h2>販促エンジン（主役の通年トレンド）</h2>
      <span class="bnote">主役区分が年々伸びているか。バー＝月次売上（緑=前年超/赤=前年割れ・押すと商品）／下は回ごとの昨対。回どうしの比較や目標・POPは下の「この店の販促」で。</span></div>
    <div class="enggrid">${cards}</div>
  </section>`;
}

// 店長ダッシュボード（店ページ先頭のヒーロー）。開いて3秒で「予算に対してどうか／
// 主役の販促は効いているか／やり残し（POP・目標・振り返り）」が分かる。
function storeHero(code) {
  const my = (DATA.campaigns || []).filter(c => (c.stores || []).includes(code));
  const live = my.filter(c => campStatus(c).k === "live");
  const soon = my.filter(c => campStatus(c).k === "soon");
  const done = my.filter(c => campStatus(c).k === "done");

  // ① 予算達成率（直近確定月）。予算未取込なら売上＋前年比にフォールバック。
  const latest = latestConfirmed(code);
  const y = yoy(code);
  const pf = storeProfit(code);
  const ktSub = pf && pf.kt != null ? `・客単価 ${yen(pf.kt)}` : "";
  let budCard;
  if (latest) {
    const bud = budgetAt(code, latest.m);
    const sales = salesAtC(code, latest.m);
    if (typeof bud === "number" && bud) {
      const rate = Math.round(sales / bud * 100);
      budCard = `<div class="hcard hbig ${rate >= 100 ? "good" : "warn"}">
        <div class="hlbl">予算達成率（${latest.m}）</div>
        <div class="hval">${rate}<span class="hu">%</span></div>
        <div class="hsub">予算 ${man(bud)} → 実績 ${man(sales)}円${y ? `・前年 ${signed(y.pct)}%` : ""}${ktSub}</div></div>`;
    } else {
      budCard = `<div class="hcard hbig">
        <div class="hlbl">直近売上（${latest.m}）</div>
        <div class="hval">${man(sales)}<span class="hu">円</span></div>
        <div class="hsub">${y ? `前年 ${signed(y.pct)}%` : "前年 ―"}${ktSub}　<span class="muted">予算未登録</span></div></div>`;
    }
  } else {
    budCard = `<div class="hcard hbig"><div class="hlbl">売上</div><div class="hval">―</div></div>`;
  }
  // データ鮮度の一言（月次なので当月は締め後）。旧サマリから引き継ぐ。
  const freshNote = `<div class="hnote">${CURRENT_MONTH.slice(5)}月ぶんは締め後（翌月上旬）に入ります。${
    latest ? `いま確定しているのは ${latest.m} まで。` : ""}数値はFWから自動集計しています。</div>`;

  // ② 主役の販促（実施中）。ルクアは パフェ→ケーキ が主役なので優先して並べる。
  const priority = c => (c.bucket === "パフェ" ? 0 : c.bucket === "ケーキ" ? 1 : 2);
  const heroCamps = live.slice().sort((a, b) => priority(a) - priority(b)).slice(0, 3);
  const heroCards = heroCamps.length ? heroCamps.map(c => {
    const tg = campTargeted(c, code);
    const v = campVerdict(c);
    const mk = VERDICT_MARK[v.tone] || "";
    const num = (tg && tg.cur)
      ? `<b>${man(tg.cur)}円</b>${tg.pct != null ? ` <span class="${tg.pct >= 0 ? "up" : "down"}">昨対 ${signed(tg.pct)}%</span>` : ""}`
      : `<span class="muted">数値は確定月が出てから</span>`;
    return `<button class="hpromo" data-camp="${c.id}"><span class="kdot" style="background:${kindOf(c.kind).color}"></span>
      <span class="hpn">${esc(c.title)}${mk ? ` <span class="gvm ${v.tone}">${mk}</span>` : ""}</span>
      <span class="hpv">${num}</span></button>`;
  }).join("") : `<div class="muted hmt">実施中の販促はありません</div>`;
  // 直近の販促結果（確定済み・測定できたものの最新）。当月の主役がまだ数字を
  // 持たない時でも「効いたか」を空にしないため。◎/△ は正直に出す。
  const measuredDone = done
    .map(c => ({ c, e: storeCampEffect(c, code) }))
    .filter(x => x.e.measured)
    .sort((a, b) => ((a.c.end || a.c.start) < (b.c.end || b.c.start) ? 1 : -1));
  const win = measuredDone[0];
  const winLine = win
    ? `<button class="hpromo hwin" data-camp="${win.c.id}">
        <span class="hpn"><span class="muted">直近の結果:</span> ${esc(win.c.title)} <span class="gvm ${win.e.tone}">${win.e.mark}</span></span>
        <span class="hpv"><span class="${win.e.pct >= 0 ? "up" : "down"}">昨対 ${signed(win.e.pct)}%</span></span></button>`
    : "";

  // ③ 要対応（POP未登録／目標未設定／振り返り未記入）
  const noPop = [...live, ...soon].filter(c => creativesForCampaign(c.id).length === 0);
  const noGoal = live.filter(c => goalEligible(c) && targetOf(c) == null);
  const noReview = done.filter(needsReview);
  const todo = [];
  if (noPop.length) todo.push({ n: noPop.length, label: "POP未登録", c: noPop[0] });
  if (noGoal.length) todo.push({ n: noGoal.length, label: "目標未設定", c: noGoal[0] });
  if (noReview.length) todo.push({ n: noReview.length, label: "振り返り未記入", c: noReview[0] });
  const todoHtml = todo.length
    ? `<ul class="htodo">${todo.map(t => `<li data-camp="${t.c.id}"><span class="htn">${t.label}</span><span class="htc">${t.n}件</span><span class="htgo">→</span></li>`).join("")}</ul>`
    : `<div class="muted hmt">やり残しなし 👍</div>`;

  return `<section class="block hero" id="hero">
    <div class="hgrid">
      ${budCard}
      <div class="hcard"><div class="hlbl">主役の販促（実施中）</div>${heroCards}${winLine}</div>
      <div class="hcard"><div class="hlbl">要対応</div>${todoHtml}</div>
    </div>
    ${freshNote}
  </section>`;
}

// 今月の共有カード（会議・LINE用）。確定した最新月の要点を1枚に。スクショで会議、
// 「コピー」でLINEに貼れるプレーンテキストも用意。数値は自動集計から。
function storeShareCard(code) {
  const s = store(code);
  const latest = latestConfirmed(code);
  if (!latest) return "";
  const m = latest.m;
  const y = yoy(code);
  const sales = salesAtC(code, m);
  const bud = budgetAt(code, m);
  const rate = (typeof bud === "number" && bud && sales) ? Math.round(sales / bud * 100) : null;
  const cov = coversAt(code, m);
  const spp = (sales && cov) ? Math.round(sales / cov) : null;
  const my = (DATA.campaigns || []).filter(c => (c.stores || []).includes(code));
  const live = my.filter(c => campStatus(c).k === "live");
  const soon = my.filter(c => campStatus(c).k === "soon");
  const done = my.filter(c => campStatus(c).k === "done");
  // 主役の結果＝計測できた最新（実施中→終了問わず）
  const win = my.map(c => ({ c, e: storeCampEffect(c, code) })).filter(x => x.e.measured)
    .sort((a, b) => ((a.c.end || a.c.start) < (b.c.end || b.c.start) ? 1 : -1))[0];
  // 要対応
  const noPop = [...live, ...soon].filter(c => creativesForCampaign(c.id).length === 0).length;
  const noGoal = live.filter(c => goalEligible(c) && targetOf(c) == null).length;
  const noReview = done.filter(needsReview).length;
  const todoParts = [];
  if (noPop) todoParts.push(`POP未登録${noPop}`);
  if (noGoal) todoParts.push(`目標未設定${noGoal}`);
  if (noReview) todoParts.push(`振り返り未記入${noReview}`);
  const todoTxt = todoParts.length ? todoParts.join("・") : "なし";

  // LINE貼り付け用プレーンテキスト（改行つき）
  const winTxt = win ? `${win.c.title} ${win.e.mark} 昨対${signed(win.e.pct)}%` : "―";
  const text = [
    `【${s.name}】${m} 実績`,
    rate != null ? `予算達成 ${rate}%（予算${man(bud)}→実績${man(sales)}円）` : `売上 ${man(sales)}円`,
    `売上 ${man(sales)}円${y ? `・前年 ${signed(y.pct)}%` : ""}`,
    cov != null ? `客数 ${nin(cov)}${spp != null ? `・客単価 ${yen(spp)}` : ""}` : "",
    `主役の結果: ${winTxt}`,
    `要対応: ${todoTxt}`,
    `※数値はFWから自動集計`,
  ].filter(Boolean).join("\n");

  return `<section class="block" id="share">
    <div class="bhead"><h2>今月の共有カード</h2><span class="bnote">会議はスクショ、LINEは「コピー」で貼り付け</span></div>
    <div class="sharecard">
      <div class="sc-hd"><b>${esc(s.name)}</b><span class="sc-m">${m} 実績</span></div>
      <div class="sc-main">
        <div class="sc-kpi"><span class="sc-l">予算達成</span><b class="${rate != null ? (rate >= 100 ? "up" : "down") : ""}">${rate != null ? rate + "%" : "―"}</b></div>
        <div class="sc-kpi"><span class="sc-l">売上</span><b>${man(sales)}</b>${y ? `<span class="${y.pct >= 0 ? "up" : "down"}">${signed(y.pct)}%</span>` : ""}</div>
        <div class="sc-kpi"><span class="sc-l">客単価</span><b>${spp != null ? yen(spp) : "―"}</b></div>
      </div>
      <div class="sc-row"><span class="sc-l">主役の結果</span>${win ? `${esc(win.c.title)} <span class="gvm ${win.e.tone}">${win.e.mark}</span> <span class="${win.e.pct >= 0 ? "up" : "down"}">昨対${signed(win.e.pct)}%</span>` : "―"}</div>
      <div class="sc-row"><span class="sc-l">要対応</span>${todoParts.length ? esc(todoTxt) : "なし 👍"}</div>
    </div>
    <pre class="sc-copytext" id="sharetext-${esc(code)}" hidden>${esc(text)}</pre>
    <button class="shbtn" data-sharecopy="sharetext-${esc(code)}">📋 テキストをコピー（LINE用）</button>
  </section>`;
}

// 年間スケジュール本体。チャート（帯・既定）とカレンダー表を切替、年を選べる。
// 販促（帯・チップ）を押すと販促詳細へ、月を押すと月ドリルへ。
function storeAnnual(code) {
  const mkeys = Object.keys((DATA.monthly || {})[code] || {});
  const camps = (DATA.campaigns || []).filter(c => (c.stores || []).includes(code));
  if (!mkeys.length && !camps.length) return "";
  // 選べる年 ＝ 実績のある年 ∪ 販促のある年
  const yset = new Set(mkeys.map(m => m.slice(0, 4)));
  camps.forEach(c => {
    const a = +c.start.slice(0, 4), b = +((c.end || c.start).slice(0, 4));
    for (let y = a; y <= b; y++) yset.add(String(y));
  });
  const years = [...yset].sort();
  const cur = String(STORE_YEAR && years.includes(String(STORE_YEAR)) ? STORE_YEAR : years[years.length - 1]);
  const view = STORE_ANNUAL_VIEW === "calendar" ? "calendar" : "chart";
  const yearTabs = years.map(y => `<button class="ytab${y === cur ? " on" : ""}" data-syear="${y}">${y}年</button>`).join("");
  const toggle = `<div class="viewtabs">
    <button class="vtab${view === "chart" ? " on" : ""}" data-savw="chart">月次一覧</button>
    <button class="vtab${view === "calendar" ? " on" : ""}" data-savw="calendar">カレンダー表（開いて詳しく）</button></div>`;
  const body = view === "calendar" ? storeAnnualCalendar(code, cur) : storeAnnualChart(code, cur);
  return `<section class="block" id="annual">
    <div class="bhead"><h2>年間スケジュール</h2>
      <span class="bnote">${view === "chart" ? "月ごとの売上・前年比・予算・構成比・販促を上から下へ一覧。下は販促の帯（期間）。行/帯を押すと詳細へ" : "月を押すと予算・売上・集客・客単価・区分構成比が開く／区分を押すと商品一覧"}</span></div>
    <div class="annualbar">${toggle}<div class="ytabs">${yearTabs}</div></div>
    ${body}
  </section>`;
}

// チャート（帯・ガント）。1年ぶん、販促を期間の帯で並べる。帯クリックで詳細。
// 上に売上ミニ棒（前年比で色）を月軸に重ね、帯は種類色＋効果判定（◎/△）マーカー付き。
const VERDICT_MARK = { good: "◎", warn: "△", flat: "" };
function storeAnnualChart(code, year) {
  const ys = `${year}-01-01`, ye = `${year}-12-31`;
  const camps = (DATA.campaigns || [])
    .filter(c => (c.stores || []).includes(code) && c.start.slice(0, 10) <= ye && (c.end || c.start).slice(0, 10) >= ys)
    .sort((a, b) => a.start < b.start ? -1 : 1);
  const head = Array.from({ length: 12 }, (_, i) =>
    `<button class="gmh" data-smonth="${code}:${year}-${String(i + 1).padStart(2, "0")}">${i + 1}</button>`).join("");

  // 販促を「対象区分」（パフェ/ケーキ/コラボ…）でまとめ、区分ごとに1レーン＝1行にする。
  // 帯の色も区分色でそろえる。同じ区分で“日付”が重なる販促があるときだけ、その区分に
  // 2レーン目（例: パフェ2）を作る（月がたまたま同じでも、日付が重ならなければ同じ行）。
  const catOf = c => c.bucket || campKindBucket(c.kind) || "その他";
  // 区分色。CAT_COLORS に無い区分（テイクアウトジェラート・おすすめ等）が灰色に
  // ならないよう、名前寄せ→はっきり違う予備パレットの順で必ず色を割り当てる。
  const CHART_FALLBACK = ["#e07a3e", "#2e8b57", "#7b5cd6", "#3aa0a0", "#c94f7c", "#c9a227", "#5b8def"];
  const laneColor = {};
  const assignColor = (cat, i) => {
    let c = catColor(cat);
    if (!c || c === "var(--ink-3)") {
      if (cat.includes("ジェラート")) c = catColor("ジェラート");
      else if (cat.includes("パフェ")) c = catColor("パフェ");
      else if (cat.includes("ケーキ")) c = catColor("ケーキ");
      else if (cat.includes("コラボ")) c = catColor("コラボ");
      else c = CHART_FALLBACK[i % CHART_FALLBACK.length];
    }
    return c;
  };
  const makeBar = (c, col) => {
    const st = campStatus(c);
    const s = c.start.slice(0, 10), e = (c.end || c.start).slice(0, 10);
    // 帯の位置は「分数月」（月内の日付も反映）。同じ月に接する2件が重なって見えない。
    const monthFrac = (ds, end = false) => {
      let dd = ds < ys ? ys : (ds > ye ? ye : ds);
      const mo = +dd.slice(5, 7), day = +dd.slice(8, 10);
      const dim = new Date(+dd.slice(0, 4), mo, 0).getDate();
      return Math.min(1, Math.max(0, ((mo - 1) + (end ? day : day - 1) / dim) / 12));
    };
    const left = monthFrac(s) * 100;
    const w = Math.max(1.2, (monthFrac(e, true) - monthFrac(s)) * 100);
    const ef = storeCampEffect(c, code);
    const mark = ef.mark || (VERDICT_MARK[campVerdict(c).tone] || "");
    const tone = ef.mark ? ef.tone : campVerdict(c).tone;
    // ホバー用（マウスを合わせると売上・昨対・前回比が出る）。
    let tip = `${c.title}｜${campRange(c)}`;
    if (ef.measured) {
      tip += `｜${ef.label} ${man(ef.cur)}円・昨対${signed(ef.pct)}%`;
      const prevOcc = campPrevOccurrence(c);
      if (prevOcc) { const pb = campTargeted(prevOcc, code); if (pb && pb.cur) tip += `・前回${signed((ef.cur / pb.cur - 1) * 100)}%`; }
    } else if (ef.state) { tip += `｜${ef.state}`; }
    return `<button class="gbar ${st.k}" data-camp="${c.id}" style="left:${left}%;width:${w}%;--kc:${col}"
      data-tip="${esc(tip)}">${mark ? `<span class="gvm ${tone}">${mark}</span>` : ""}<span class="gbt">${esc(c.title)}</span></button>`;
  };
  // 区分ごとにまとめる。並びは販促件数が多い区分を上に（同数は開始が早い順）。
  const byCat = new Map();
  for (const c of camps) { const k = catOf(c); (byCat.get(k) || byCat.set(k, []).get(k)).push(c); }
  const catOrder = [...byCat.keys()].sort((a, b) =>
    (byCat.get(b).length - byCat.get(a).length) || (byCat.get(a)[0].start < byCat.get(b)[0].start ? -1 : 1));
  catOrder.forEach((cat, i) => { laneColor[cat] = assignColor(cat, i); });
  const laneHtml = [];
  for (const cat of catOrder) {
    const col = laneColor[cat];
    const list = byCat.get(cat).slice().sort((a, b) => a.start < b.start ? -1 : 1);
    const sub = []; // この区分の中のサブレーン（“日付”が重なったときだけ増える）
    for (const c of list) {
      const s = c.start.slice(0, 10), e = (c.end || c.start).slice(0, 10);
      let lane = sub.find(L => L.lastEnd < s);   // 前の帯の終了日より後に始まれば同じ行
      if (!lane) { lane = { lastEnd: "", bars: [] }; sub.push(lane); }
      lane.bars.push(makeBar(c, col)); if (e > lane.lastEnd) lane.lastEnd = e;
    }
    sub.forEach((L, idx) => {
      const label = idx === 0 ? cat : `${cat}${idx + 1}`;
      laneHtml.push(`<div class="grow"><div class="glabel gcat"><span class="kdot" style="background:${col}"></span>${esc(label)}</div><div class="gtrack">${L.bars.join("")}</div></div>`);
    });
  }
  const rows = laneHtml.join("");

  // 凡例（その年に出ている種類）＋見方（誰が見ても操作が分かるように）
  const legend = catOrder.length
    ? `<div class="glegend">${catOrder.map(cc => `<span class="glg"><i style="background:${laneColor[cc]}"></i>${esc(cc)}</span>`).join("")}
        <span class="glg"><i class="gvm good">◎</i>効果あり</span><span class="glg"><i class="gvm warn">△</i>要改善</span></div>
       <div class="ghelp">区分ごとに帯をまとめています（色＝品目区分）。同じ区分で期間が重なる販促は「パフェ2」のように行を分けます。<b>帯や販促名</b>を押すと詳細、<b>上の月番号</b>を押すとその月の詳細（構成比・POP）へ。◎/△は対象区分の前年比で自動判定。</div>`
    : "";

  // 年サマリ（確定分の売上合計・前年比・予算達成の平均・販促◎/△）。チャートの頭に置いて、
  // 下までスクロールしなくても要約が分かるように。
  const yConf = Array.from({ length: 12 }, (_, i) => `${year}-${String(i + 1).padStart(2, "0")}`)
    .filter(m => m < CURRENT_MONTH && (DATA.monthly[code] || {})[m]);
  let ySales = 0, yPrev = 0; const budRates = [];
  for (const m of yConf) {
    const sv = salesAtC(code, m); if (typeof sv !== "number") continue;
    ySales += sv;
    const pv = salesAtC(code, prevYearM(m)); if (typeof pv === "number") yPrev += pv;
    const b = budgetAt(code, m); if (sv && b) budRates.push(sv / b * 100);
  }
  const yYoY = yPrev ? (ySales / yPrev - 1) * 100 : null;
  const budAvg = budRates.length ? Math.round(budRates.reduce((a, b) => a + b, 0) / budRates.length) : null;
  const cGood = camps.filter(c => { const e = storeCampEffect(c, code); return e.measured && e.pct >= 0; }).length;
  const cWarn = camps.filter(c => { const e = storeCampEffect(c, code); return e.measured && e.pct < 0; }).length;
  const yearSummary = `<div class="ysum">
    <span class="ysum-i"><span class="ysl">${year}年 売上(確定)</span><b>${ySales ? man(ySales) + "円" : "―"}</b>${yYoY != null ? `<span class="${yYoY >= 0 ? "up" : "down"}">前年${signed(yYoY)}%</span>` : ""}</span>
    ${budAvg != null ? `<span class="ysum-i"><span class="ysl">予算達成(平均)</span><b class="${budAvg >= 100 ? "up" : "down"}">${budAvg}%</b></span>` : ""}
    <span class="ysum-i"><span class="ysl">販促の効き</span><b class="up">◎ ${cGood}</b> <b class="down">△ ${cWarn}</b></span>
  </div>`;

  return `${yearSummary}${storeYearMatrix(code, year)}
    <div class="mmhd" style="margin-top:16px">販促 年間チャート<span class="mmhint">帯＝実施期間（横軸＝月）。◎効いた/△要改善。帯や販促名を押すと詳細、月番号を押すと月の詳細へ</span></div>
    <div class="panel gantt">
    <div class="grow ghead"><div class="glabel gh">区分</div><div class="gmonths">${head}</div></div>
    ${camps.length ? rows : `<div class="empty">${year}年に走った販促はありません。</div>`}
  </div>${legend}`;
}

// 月次の推移を「1行＝1ヶ月」の一覧にする。年間まとめではなく、月ごとの結果（売上・前年比・
// 予算達成・客数/客単価・品目構成比・販促の効き）を上から下へ読める。行を押すと月の詳細へ。
// 年間×月のマトリクス表。縦＝指標（売上・前年比・予算・客数・客単価・構成比・販促）、
// 横＝12ヶ月。1画面で年間を管理でき、月は列で読める。右端に「年計」列を付ける。
// 月見出しを押すとその月の詳細へ。横は狭いときスクロール（左の指標名は固定）。
function storeYearMatrix(code, year) {
  const months = Array.from({ length: 12 }, (_, i) => `${year}-${String(i + 1).padStart(2, "0")}`);
  const S = months.map(m => (((DATA.monthly[code] || {})[m]) ? salesAtC(code, m) : null));
  if (!S.some(s => typeof s === "number")) return "";
  const myCamps = (DATA.campaigns || []).filter(c => (c.stores || []).includes(code));
  const cellCls = i => (typeof S[i] === "number" ? "" : " na") + (months[i] >= CURRENT_MONTH ? " prov" : "");

  // 各月の派生値
  const rows = months.map((m, i) => {
    const s = S[i], has = typeof s === "number";
    const yv = salesAtC(code, prevYearM(m));
    const yoy = (has && typeof yv === "number" && yv) ? (s / yv - 1) * 100 : null;
    const bud = budgetAt(code, m), budRate = (has && s && bud) ? Math.round(s / bud * 100) : null;
    const cov = coversAt(code, m), spp = (has && s && cov) ? Math.round(s / cov) : null;
    const cats = has ? catsAtM(code, m).slice().sort((a, b) => b.share - a.share) : [];
    const camps = myCamps.filter(c => c.start.slice(0, 7) <= m && (c.end || c.start).slice(0, 7) >= m);
    return { m, i, s, has, yoy, bud, budRate, cov, spp, cats, camps };
  });

  // 年計（確定＝当月より前かつ実績あり）
  const conf = rows.filter(r => r.has && r.m < CURRENT_MONTH);
  const sumSales = conf.reduce((a, r) => a + (r.s || 0), 0);
  let yPrev = 0; for (const r of conf) { const pv = salesAtC(code, prevYearM(r.m)); if (typeof pv === "number") yPrev += pv; }
  const yYoY = yPrev ? (sumSales / yPrev - 1) * 100 : null;
  const budR = conf.filter(r => r.budRate != null); const budAvg = budR.length ? Math.round(budR.reduce((a, r) => a + r.budRate, 0) / budR.length) : null;
  const sumCov = conf.reduce((a, r) => a + (typeof r.cov === "number" ? r.cov : 0), 0) || null;
  const yKt = (sumSales && sumCov) ? Math.round(sumSales / sumCov) : null;

  const th = `<th class="ymxc"></th>` + months.map((m, i) => {
    const mo = +m.slice(5, 7);
    return `<th class="ymxmh${cellCls(i)}"><button data-smonth="${code}:${m}">${mo}月</button></th>`;
  }).join("") + `<th class="ymxsum">年計</th>`;

  const numCell = (i, txt, cls) => `<td class="${cellCls(i)}${cls ? " " + cls : ""}">${txt}</td>`;
  const rowSales = `<tr><th>売上</th>${rows.map(r => numCell(r.i, r.has ? `<b>${man(r.s)}</b>` : "―")).join("")}<td class="ymxsum"><b>${sumSales ? man(sumSales) : "―"}</b></td></tr>`;
  const rowYoY = `<tr><th>前年比</th>${rows.map(r => numCell(r.i, r.yoy != null ? signed(r.yoy) + "%" : "―", r.yoy != null ? (r.yoy >= 0 ? "up" : "down") : "")).join("")}<td class="ymxsum ${yYoY != null ? (yYoY >= 0 ? "up" : "down") : ""}">${yYoY != null ? signed(yYoY) + "%" : "―"}</td></tr>`;
  const rowBud = `<tr><th>予算</th>${rows.map(r => numCell(r.i, r.budRate != null ? r.budRate + "%" : "―", r.budRate != null ? (r.budRate >= 100 ? "up" : "down") : "")).join("")}<td class="ymxsum ${budAvg != null ? (budAvg >= 100 ? "up" : "down") : ""}">${budAvg != null ? budAvg + "%" : "―"}</td></tr>`;
  const rowCov = `<tr><th>客数</th>${rows.map(r => numCell(r.i, r.cov != null ? nin(r.cov) : "―")).join("")}<td class="ymxsum">${sumCov ? nin(sumCov) : "―"}</td></tr>`;
  const rowKt = `<tr><th>客単価</th>${rows.map(r => numCell(r.i, r.spp != null ? yen(r.spp) : "―")).join("")}<td class="ymxsum">${yKt != null ? yen(yKt) : "―"}</td></tr>`;

  // 品目構成比は「月ごと」に読めるよう、区分×月の行にする。各月＝その月の売上に占める割合、
  // 年計＝確定分の年間シェア。区分は年間シェアの大きい順に並べる（色はチャートと共通）。
  const yearAgg = new Map();
  for (const r of conf) for (const c of r.cats) yearAgg.set(c.name, (yearAgg.get(c.name) || 0) + c.sales);
  const yTot = [...yearAgg.values()].reduce((a, b) => a + b, 0);
  const catNames = [...yearAgg.entries()].sort((a, b) => b[1] - a[1]).map(([n]) => n);
  // その月に販促がある区分（色付けに使う）。
  const monthHits = rows.map(r => promoHitsForMonth(code, r.m));
  const compoSec = catNames.length
    ? `<tr class="ymxsec"><th>品目構成比（金額）</th>${months.map((m, i) => `<td class="${cellCls(i)}"></td>`).join("")}<td class="ymxsum"></td></tr>`
    : "";
  const compoRows = catNames.map(n => {
    const cells = rows.map(r => {
      const tot = r.cats.reduce((a, x) => a + (x.sales || 0), 0);
      const c = r.cats.find(x => x.name === n);
      if (!(r.has && c && c.sales)) return `<td class="${cellCls(r.i)} ymxcompo-c">―</td>`;
      const pct = tot ? Math.round(c.sales / tot * 100) : 0;
      const promo = monthHits[r.i].cats.has(n);   // その月に販促がある区分 → 色付け
      // カーソルを合わせると 構成比(%)・出品数・金額。押すと商品内訳の小窓が開く。
      const tip = `${n}｜構成比 ${pct}%｜${c.count || 0}品｜${man(c.sales)}円${promo ? "｜販促あり" : ""}（押すと商品内訳）`;
      return `<td class="${cellCls(r.i)} ymxcompo-c ymxcompo-click${promo ? " promo" : ""}" data-compocell="${code}:${r.m}:${encodeURIComponent(n)}" data-tip="${esc(tip)}">${man(c.sales)}</td>`;
    }).join("");
    const yAmt = yearAgg.get(n) || 0;
    const yPct = yTot ? Math.round(yAmt / yTot * 100) : null;
    const yTip = yPct != null ? `${n}｜年間シェア ${yPct}%｜${man(yAmt)}円` : "";
    return `<tr class="ymxcompo-row"><th><span class="ymxcdot" style="background:${catColor(n)}"></span>${esc(n)}</th>${cells}<td class="ymxsum"${yTip ? ` data-tip="${esc(yTip)}"` : ""}>${yAmt ? man(yAmt) : "―"}</td></tr>`;
  }).join("");

  // フード/ドリンク比（店長会シート・税抜）。押すとその月の「部門別」小窓が開く。
  const fdCell = r => {
    const mm = (DATA.monthly[code] || {})[r.m] || {};
    const f = mm.food_sales, d = mm.drink_sales;
    if (!(typeof f === "number" && typeof d === "number" && (f + d) > 0)) return `<td class="${cellCls(r.i)}">―</td>`;
    const fp = Math.round(f / (f + d) * 100);
    const tip = `${+r.m.slice(5, 7)}月 フード${fp}% / ドリンク${100 - fp}%（押すと部門別）`;
    return `<td class="${cellCls(r.i)} fdcell" data-fdcell="${code}:${r.m}" data-tip="${esc(tip)}">${fp}/${100 - fp}</td>`;
  };
  let sumF = 0, sumD = 0;
  for (const r of rows) { const mm = (DATA.monthly[code] || {})[r.m] || {}; if (typeof mm.food_sales === "number") sumF += mm.food_sales; if (typeof mm.drink_sales === "number") sumD += mm.drink_sales; }
  const fdYsum = (sumF + sumD) > 0 ? `${Math.round(sumF / (sumF + sumD) * 100)}/${100 - Math.round(sumF / (sumF + sumD) * 100)}` : "―";
  const fdRow = `<tr class="ymxfd"><th>F/D比</th>${rows.map(fdCell).join("")}<td class="ymxsum">${fdYsum}</td></tr>`;

  // 予算未入力の月（売上はあるが予算が無い確定月）を明示。FW入力を促す。
  const budMissing = rows.filter(r => r.has && r.m < CURRENT_MONTH && !(typeof r.bud === "number" && r.bud));
  const budHint = budMissing.length
    ? `<div class="budnote">予算未入力の月あり（${budMissing.map(r => +r.m.slice(5, 7) + "月").join("・")}）— FWに月別予算を入れると「予算」行が埋まります。</div>`
    : "";

  return `<div class="ymx">
    <div class="mmhd">${year}年 一覧（縦＝指標／横＝月）<span class="mmhint">金額はすべて税抜。月(列見出し)を押すとその月の詳細へ。品目構成比の金額を押すと商品内訳の小窓が開く（複数可）。緑=前年超/赤=前年割れ・薄い列＝暫定/未取込・右端＝年計</span></div>
    <div class="ymxwrap"><table class="ymxt"><thead><tr>${th}</tr></thead>
      <tbody>${rowSales}${rowYoY}${rowBud}${rowCov}${rowKt}${fdRow}${compoSec}${compoRows}</tbody></table></div>
    ${budHint}</div>`;
}

// カレンダー表＝月を縦に一覧。各月に 予算/売上/集客/客単価 と品目区分の構成比。
// 月を押すと開閉。区分（例ジェラート）を押すとその場で商品一覧を開閉（ページ遷移なし）。
// 「この月の詳細→」で構成比・販促・POPのフルページへ。
function storeAnnualCalendar(code, year) {
  const mset = new Set(Object.keys((DATA.monthly || {})[code] || {}));
  const stat = (lbl, val, extra) =>
    `<span class="mstat"><span class="msl">${lbl}</span><span class="msv">${val}</span>${extra || ""}</span>`;
  const rows = [];
  for (let mo = 1; mo <= 12; mo++) {
    const m = `${year}-${String(mo).padStart(2, "0")}`;
    const has = mset.has(m);
    const prov = m >= CURRENT_MONTH;
    const sales = has ? salesAtC(code, m) : null;
    const bud = budgetAt(code, m);
    const cov = coversAt(code, m);
    const spp = (sales && cov) ? Math.round(sales / cov) : null;
    const yv = salesAtC(code, prevYearM(m));
    const yoy = (has && typeof yv === "number" && yv) ? (sales / yv - 1) * 100 : null;
    const budRate = (sales && bud) ? Math.round(sales / bud * 100) : null;
    const open = !!ANNUAL_OPEN[`${code}:${m}`];
    const camps = campsInMonth(code, m);

    const head = `<button class="mhd${open ? " on" : ""}"${has ? ` data-mtoggle="${code}:${m}"` : ""}>
      <span class="mhm">${has ? (open ? "▾" : "▸") + " " : ""}${mo}月${provBadge(m, has)}${camps.length ? `<span class="mhc">販促${camps.length}</span>` : ""}</span>
      <span class="mstats">
        ${stat("予算達成率", budRate != null ? `<b class="${budRate >= 100 ? "up" : "down"}">${budRate}%</b>` : "―", bud != null ? `<span class="mssub">予算${man(bud)}円</span>` : "")}
        ${stat("売上", sales != null ? man(sales) + "円" : "―", yoy != null ? `<span class="msx ${yoy >= 0 ? "up" : "down"}">前年${signed(yoy)}%</span>` : "")}
        ${stat("客数", cov != null ? nin(cov) : "―")}
        ${stat("客単価", spp != null ? yen(spp) : "―")}
      </span></button>`;

    let body = "";
    if (open) {
      const cats = has ? catsAtM(code, m) : [];
      // 部門別売上比を一目で：100%積み上げバー（色は区分ごと固定）
      const compBar = cats.length
        ? `<div class="mcompbar">${cats.map(c =>
            `<span class="mseg" style="width:${(c.share * 100).toFixed(2)}%;background:${catColor(c.name)}" data-tip="${esc(c.name)}｜構成比 ${Math.round(c.share * 100)}%｜${c.count || 0}品｜${yen(c.sales)}"></span>`).join("")}</div>` : "";
      const catList = cats.length ? `<ul class="mcats">${cats.map(c => {
        const pctv = Math.round(c.share * 100);
        const co = !!ANNUAL_OPEN[`${code}:${m}:${c.name}`];
        const py = catAtM(code, prevYearM(m), c.name);
        const cyoy = (py && py.sales) ? (c.sales / py.sales - 1) * 100 : null;
        let prodRows = "";
        if (co) {
          const prods = prodsInCat(code, m, c.name).slice().sort((a, b) => b.sales - a.sales);
          prodRows = `<ul class="mprods">${prods.length ? prods.map(p =>
            `<li><span class="mpn">${esc(p.name)}</span><span class="mps">${yen(p.sales)}${p.rank ? `・${p.rank}` : ""}</span></li>`).join("")
            : '<li class="muted">この月の商品はありません</li>'}</ul>`;
        }
        return `<li>
          <button class="mcatrow${co ? " on" : ""}" data-cattoggle="${code}:${m}:${encodeURIComponent(c.name)}">
            <span class="mcn">${co ? "▾" : "▸"} ${esc(c.name)}</span>
            <span class="mcbar"><span class="mcfill" style="width:${Math.max(2, pctv)}%;background:${catColor(c.name)}"></span></span>
            <span class="mcp">${pctv}%</span><span class="mcs">${yen(c.sales)}</span>
            ${cyoy != null ? `<span class="msx ${cyoy >= 0 ? "up" : "down"}">${signed(cyoy)}%</span>` : ""}
          </button>${prodRows}</li>`;
      }).join("")}</ul>` : (has ? `<div class="muted mcatsempty">この月の商品データ（FW ABC）はまだありません。</div>` : "");
      const chips = camps.length
        ? `<div class="mchips">${camps.map(c => `<button class="pchip" data-camp="${c.id}" style="--kc:${kindOf(c.kind).color}" title="${esc(campRange(c))}">${esc(c.title)}</button>`).join("")}</div>` : "";
      body = `<div class="mbody">
        ${compBar}
        ${catList}
        ${chips}
        ${has ? `<button class="linkbtn mdet" data-smonth="${code}:${m}">この月の詳細（構成比・販促・POP）→</button>` : ""}
      </div>`;
    }
    rows.push(`<div class="mrow${has ? "" : " off"}">${head}${body}</div>`);
  }
  const legend = `<div class="mllegend">
    <span class="pv pv-conf">確定</span>締め済み
    <span class="pv pv-prov">暫定</span>当月・集計途中
    <span class="pv pv-none">未取込</span>FW未反映
    <span class="mllsp">数値はFWから自動集計</span></div>`;
  return `<div class="panel mlist">${legend}${rows.join("")}</div>`;
}

// 月詳細の月ナビ（← 前月／選択中の月▾／次月 →）。中央を押すと月ピッカーを開閉。
function storeMonthNav(code, m) {
  const all = Object.keys((DATA.monthly || {})[code] || {}).sort();
  const i = all.indexOf(m);
  const prev = i > 0 ? all[i - 1] : null;
  const next = (i >= 0 && i < all.length - 1) ? all[i + 1] : null;
  const pick = MONTH_PICK_OPEN
    ? `<div class="mpick">${all.slice().reverse().map(x =>
        `<button class="mpk${x === m ? " on" : ""}" data-smonth="${code}:${x}">${jpMonth(x)}</button>`).join("")}</div>`
    : "";
  return `<div class="mnav">
    <button class="mnavb"${prev ? ` data-smonth="${code}:${prev}"` : " disabled"}>←</button>
    <button class="mnavc" data-mpick="1">${jpMonth(m)} ▾</button>
    <button class="mnavb"${next ? ` data-smonth="${code}:${next}"` : " disabled"}>→</button>
    ${pick}</div>`;
}

// Steppy風の予算比ピル。rate=100 で予算どおり。↑良い(緑)／↓悪い(赤)／◆ほぼ予算(黄)。
// deltaTxt は「+531,895円」のような差額（任意）。higherWorse は原価率など高いほど悪い指標用。
function budgetPill(rate, deltaTxt, higherWorse) {
  if (rate == null) return "";
  const d = rate - 100;
  let tone = Math.abs(d) < 0.5 ? "flat" : (d > 0 ? "up" : "down");
  if (higherWorse && tone !== "flat") tone = tone === "up" ? "down" : "up";
  const mark = tone === "up" ? "▲" : tone === "down" ? "▼" : "◆";
  return `<span class="kpill ${tone}">${mark} 予算比 ${rate.toFixed(1)}%${deltaTxt ? `<span class="kpd">（${deltaTxt}）</span>` : ""}</span>`;
}
// 前年比／前月比などのピル（0以上=緑・未満=赤・≒0=黄）。
function pctPill(label, pct) {
  if (pct == null) return "";
  const tone = Math.abs(pct) < 0.05 ? "flat" : (pct >= 0 ? "up" : "down");
  const mark = tone === "up" ? "▲" : tone === "down" ? "▼" : "◆";
  return `<span class="kpill ${tone}">${mark} ${label} ${signed(pct)}%</span>`;
}

// 月の詳細（構成比・客単価・集客＋その月の販促）
function renderStoreMonth(code, m) {
  const s = store(code);
  const color = regionColor(s.region);
  const sales = salesAtC(code, m);
  const cov = coversAt(code, m);
  const yv = salesAtC(code, prevYearM(m));
  const pv = salesAtC(code, addMonth(m, -1));
  const yoy = (typeof yv === "number" && yv) ? (sales / yv - 1) * 100 : null;
  const mom = (typeof pv === "number" && pv) ? (sales / pv - 1) * 100 : null;
  const spp = (sales && cov) ? Math.round(sales / cov) : null;
  const covY = coversAt(code, prevYearM(m));
  const covYoy = (typeof covY === "number" && covY && cov) ? (cov / covY - 1) * 100 : null;
  const prov = m >= CURRENT_MONTH;

  // フード/ドリンクの原価率（FW部門）
  const dm = ((DATA.departments_monthly || {})[code] || {})[m];
  const crRow = dm && dm.raw ? dm.raw.filter(r => r.cost_rate != null)
    .map(r => `${esc(r.name)} 原価${r.cost_rate}%`).join("　") : "";

  // 予算達成率（FW月別予算）。売上の主要指標として、売上のとなりに出す。
  const bud = budgetAt(code, m);
  const budRate = (sales && typeof bud === "number" && bud) ? Math.round(sales / bud * 100) : null;
  // 予算未入力の明示＋入力ナビ（売上はあるのに予算が無い確定月だけ）。
  const budNote = (sales != null && !prov && !(typeof bud === "number" && bud))
    ? `<div class="budnote">予算未入力 — FWに <b>${jpMonth(m)}</b> の月別予算を入れると、ここに予算達成率が自動で出ます。</div>`
    : "";

  // Steppy風カード：見出し・大きな数字・薄い予算/前年サブ・色付きの予算比ピル。
  const card = (lbl, big, sub, pill, extra) => `<div class="kpi">
    <div class="lbl">${lbl}</div>
    <div class="big">${big}</div>
    ${sub ? `<div class="ksub">${sub}</div>` : ""}
    ${pill || ""}
    ${extra ? `<div class="kextra">${extra}</div>` : ""}</div>`;
  // 売上カード：予算があれば「予算◯円」＋予算比ピル、無ければ前年比ピル。前年/前月は補足行。
  const budDelta = (budRate != null) ? `${sales - bud >= 0 ? "+" : "−"}${man(Math.abs(sales - bud))}円` : "";
  const salesPill = (budRate != null) ? budgetPill(budRate, budDelta) : pctPill("前年", yoy);
  const salesExtra = [yoy != null ? `前年 <span class="${yoy >= 0 ? "up" : "down"}">${signed(yoy)}%</span>` : "",
    mom != null ? `前月 <span class="${mom >= 0 ? "up" : "down"}">${signed(mom)}%</span>` : ""].filter(Boolean).join("　");
  const kpis = `<div class="kpis">
    ${card("売上" + (prov ? "（暫定）" : ""), sales != null ? man(sales) + "円" : "―",
      (typeof bud === "number" && bud) ? `予算 ${man(bud)}円` : "", salesPill, salesExtra)}
    ${card("集客（客数）", cov != null ? nin(cov) : "―", covY != null ? `前年 ${nin(covY)}` : "", pctPill("前年", covYoy))}
    ${card("客単価", spp != null ? yen(spp) : "―", cov != null ? `客数 ${nin(cov)}で算出` : "", "")}
  </div>${crRow ? `<div class="mcr">${crRow}</div>` : ""}`;

  // 品目区分の構成比（区分を押すと、その場で下に商品が開く。モバイルでもページ移動なし）
  const cats = catsAtM(code, m);
  const catTotal = cats.reduce((a, c) => a + c.sales, 0) || 1;
  const catBar = cats.slice().sort((a, b) => b.sales - a.sales).map(c =>
    `<span class="mseg" style="width:${(c.sales / catTotal * 100).toFixed(2)}%;background:${catColor(c.name)}" data-tip="${esc(c.name)}｜構成比 ${Math.round(c.sales / catTotal * 100)}%｜${c.count || 0}品｜${yen(c.sales)}"></span>`).join("");
  const catBlock = cats.length ? `<section class="block">
    <div class="bhead"><h2>品目区分の構成比</h2><span class="bnote">区分を押すと下に商品が開く　合計 ${yen(catTotal)}</span></div>
    <div class="panel"><div class="mcompbar lg">${catBar}</div><ul class="dlist">${cats.map(c => {
      const pctv = Math.round(c.share * 100);
      const w = Math.max(2, pctv);
      const py = catAtM(code, prevYearM(m), c.name);
      const cyoy = (py && py.sales) ? (c.sales / py.sales - 1) * 100 : null;
      const co = !!ANNUAL_OPEN[`${code}:${m}:${c.name}`];
      let prodRows = "";
      if (co) {
        const prevByName = {};
        prodsInCat(code, prevYearM(m), c.name).forEach(p => { prevByName[p.name] = p.sales; });
        const prods = prodsInCat(code, m, c.name).slice().sort((a, b) => b.sales - a.sales);
        prodRows = `<ul class="mprods">${prods.length ? prods.map(p => {
          const yv = prevByName[p.name];
          const pyoy = yv ? (p.sales / yv - 1) * 100 : null;
          return `<li><span class="mpn">${esc(p.name)}</span><span class="mps"><b>${yen(p.sales)}</b>${p.rank ? `・${p.rank}` : ""}${pyoy != null ? ` <span class="${pyoy >= 0 ? "up" : "down"}">${signed(pyoy)}%</span>` : ""}</span></li>`;
        }).join("") : '<li class="muted">この月の商品データはありません</li>'}</ul>`;
      }
      return `<li>
        <button class="catrow${co ? " on" : ""}" data-cattoggle="${code}:${m}:${encodeURIComponent(c.name)}">
        <span class="dname">${co ? "▾" : "▸"} ${esc(c.name)}</span>
        <span class="dbar"><span class="dfill" style="width:${w}%;background:${catColor(c.name)}"></span></span>
        <span class="dpct">${pctv}%</span>
        <span class="dsales">${yen(c.sales)}</span>
        <span class="dqty">${c.count}品</span>
        ${cyoy != null ? `<span class="myoy ${cyoy >= 0 ? "up" : "down"}">${signed(cyoy)}%</span>` : ""}
        </button>${prodRows}</li>`;
    }).join("")}</ul></div></section>`
    : `<section class="block"><div class="bhead"><h2>品目区分の構成比</h2></div>
       <div class="empty">この月の商品データ（FW ABC）はまだありません。</div></section>`;

  // その月の販促（複数重なればすべて。期間・売上・構成比・昨対比・POP）
  const camps = campsInMonth(code, m);
  const promoBlock = camps.length ? `<ul class="clist">${camps.map(c => {
    const k = kindOf(c.kind); const st = campStatus(c);
    const tg = campTargeted(c, code, { from: m, to: m });
    const share = (tg && tg.cur && sales) ? Math.round(tg.cur / sales * 100) : null;
    let eff;
    if (tg && tg.cur) {
      eff = `<div class="ceff">${esc(tg.label)} <b>${man(tg.cur)}円</b>${share != null ? `・構成比 ${share}%` : ""}${tg.pct != null ? `・<span class="${tg.pct >= 0 ? "up" : "down"}">昨対 ${signed(tg.pct)}%</span>` : ""}</div>`;
      // 通期の前回比（同じ区分の前回の回と、施策まるごとで比べる）
      const prevOcc = campPrevOccurrence(c);
      if (prevOcc) {
        const a = campTargeted(c), b = campTargeted(prevOcc);
        if (a && a.cur && b && b.cur) {
          const d = (a.cur / b.cur - 1) * 100;
          eff += `<div class="ceff sub2">前回比 <span class="${d >= 0 ? "up" : "down"}">${signed(d)}%</span><span class="sub">（前回 ${esc(prevOcc.title)}｜通期 ${man(b.cur)}→${man(a.cur)}円）</span></div>`;
        }
      }
    } else if (!campBasis(c)) {
      eff = `<div class="ceff muted">測り方が未設定（対象の区分か商品名を決めると数字が出ます）</div>`;
    } else {
      eff = `<div class="ceff muted">この月のこの販促の数値はまだ出ていません</div>`;
    }
    const crs = creativesForCampaign(c.id);
    const pops = crs.length ? `<div class="cgrid mini">${crs.map(creativeCard).join("")}</div>` : "";
    // 管理者（ログイン済み）はこの月の販促に、その場でPOPを足せる。公開（閲覧専用）では出さない。
    const add = (CREATIVES_API_OK && WRITE_OK) ? `<button class="upbtn sm" data-upload="campaign:${c.id}">＋ POP・資料を追加</button>` : "";
    return `<li data-camp="${c.id}"><span class="kchip" style="--kc:${k.color}">${k.label}</span>
      <div class="cbody"><div class="ctitle">${esc(c.title)}${statusControl(c, st.label, st.k)}</div>
        <div class="cnote">${campRange(c)}</div>${eff}${pops}${add}
        <div class="cgo">販促の詳細 →</div></div></li>`;
  }).join("")}</ul>` : `<div class="empty">この月に走っていた販促はありません。</div>`;

  // 前年差の内訳（なぜ増えた/減ったか）。区分ごとに この月 vs 前年同月 の売上差を出し、
  // 差の大きい順に「伸びた（緑）／落ちた（赤）」で並べる。前年同月と区分データが揃う時だけ。
  const breakdownBlock = (() => {
    const ym = prevYearM(m);
    const curCats = cats;                 // この月（catsAtM）
    const prevCats = catsAtM(code, ym);
    if (!curCats.length && !prevCats.length) return "";
    if (yoy == null) return "";           // 前年の実績が無ければ内訳も出さない
    const names = [...new Set([...curCats.map(c => c.name), ...prevCats.map(c => c.name)])];
    const items = names.map(name => {
      const cur = (catAtM(code, m, name) || {}).sales || 0;
      const prev = (catAtM(code, ym, name) || {}).sales || 0;
      return { name, cur, prev, delta: cur - prev, pct: prev ? (cur / prev - 1) * 100 : null };
    }).filter(d => d.cur || d.prev).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    if (!items.length) return "";
    const maxAbs = Math.max(1, ...items.map(d => Math.abs(d.delta)));
    const totalDelta = items.reduce((a, d) => a + d.delta, 0);
    const sgnMan = v => `${v >= 0 ? "+" : "−"}${man(Math.abs(v))}円`;
    const rows = items.slice(0, 8).map(d => {
      const up = d.delta >= 0;
      const w = Math.max(2, Math.round(Math.abs(d.delta) / maxAbs * 100));
      return `<li class="ybrow">
        <span class="ybn">${esc(d.name)}</span>
        <span class="ybtrack"><span class="ybfill ${up ? "up" : "down"}" style="width:${w}%"></span></span>
        <span class="ybd ${up ? "up" : "down"}">${sgnMan(d.delta)}</span>
        <span class="ybp">${d.pct != null ? signed(Math.round(d.pct)) + "%" : (d.prev ? "" : "新")}</span>
      </li>`;
    }).join("");
    return `<section class="block">
      <div class="bhead"><h2>前年差の内訳</h2><span class="bnote">前年同月と比べて伸びた（緑）／落ちた（赤）区分。差の大きい順。合計 <b class="${totalDelta >= 0 ? "up" : "down"}">${sgnMan(totalDelta)}</b></span></div>
      <div class="panel"><ul class="ybd-list">${rows}</ul></div>
    </section>`;
  })();

  return `
    <div class="crumbs"><button class="linkbtn" data-store="${code}">← ${esc(s.name)}</button>
      <button class="linkbtn" data-view="schedule">全店</button></div>
    <section class="block">
      <div class="shd"><span class="rtag" style="--rc:${color}">${s.region}</span>
        <h2 class="sname">${esc(s.name)}　${jpMonth(m)}</h2></div>
      ${storeMonthNav(code, m)}
    </section>
    <section class="block">${kpis}${budNote}</section>
    ${breakdownBlock}
    ${catBlock}
    <section class="block">
      <div class="bhead"><h2>この月の販促</h2><span class="bnote">${camps.length}件${camps.length > 1 ? "（重なり）" : ""}</span></div>
      ${promoBlock}
    </section>`;
}

// 品目区分の商品詳細（その月・前年比つき）＋関連販促
function renderStoreCat(code, m, cat) {
  const s = store(code);
  const color = regionColor(s.region);
  const c = catAtM(code, m, cat);
  const prods = prodsInCat(code, m, cat).slice().sort((a, b) => b.sales - a.sales);
  const py = catAtM(code, prevYearM(m), cat);
  const pm = catAtM(code, addMonth(m, -1), cat);
  const cyoy = (c && py && py.sales) ? (c.sales / py.sales - 1) * 100 : null;
  const cmom = (c && pm && pm.sales) ? (c.sales / pm.sales - 1) * 100 : null;
  const sales = salesAtC(code, m);
  const share = (c && c.sales && sales) ? Math.round(c.sales / sales * 100) : null;

  const prevByName = {};
  prodsInCat(code, prevYearM(m), cat).forEach(p => { prevByName[p.name] = p.sales; });
  const rows = prods.map(p => {
    const yv = prevByName[p.name];
    const yoy = yv ? (p.sales / yv - 1) * 100 : null;
    return `<tr><td>${esc(p.name)}</td><td class="num"><b>${yen(p.sales)}</b></td>
      <td class="num">${p.rank || ""}</td>
      <td class="num">${yoy != null ? `<span class="${yoy >= 0 ? "up" : "down"}">${signed(yoy)}%</span>` : "―"}</td></tr>`;
  }).join("");

  const kpi = (lbl, big, sub, tone) => `<div class="kpi"><div class="lbl">${lbl}</div>
    <div class="big ${tone || ""}">${big}</div>${sub ? `<div class="delta">${sub}</div>` : ""}</div>`;
  const kpis = `<div class="kpis">
    ${kpi("売上", c ? man(c.sales) + "円" : "―", share != null ? `店の構成比 ${share}%` : "")}
    ${kpi("品目数", c ? c.count + "品" : "―", "")}
    ${kpi("昨対比", cyoy != null ? signed(cyoy) + "%" : "―", py ? `前年 ${man(py.sales)}円` : "前年 ―", cyoy != null ? (cyoy >= 0 ? "up" : "down") : "")}
    ${kpi("前月比", cmom != null ? signed(cmom) + "%" : "―", pm ? `前月 ${man(pm.sales)}円` : "前月 ―", cmom != null ? (cmom >= 0 ? "up" : "down") : "")}
  </div>`;

  // この区分に紐づく販促（bucket 一致）で、この月に走っているもの
  const camps = campsInMonth(code, m).filter(x => x.bucket === cat);
  const promo = camps.length ? `<ul class="clist">${camps.map(x => {
    const k = kindOf(x.kind); const st = campStatus(x);
    const crs = creativesForCampaign(x.id);
    const pops = crs.length ? `<div class="cgrid mini">${crs.map(creativeCard).join("")}</div>` : "";
    return `<li data-camp="${x.id}"><span class="kchip" style="--kc:${k.color}">${k.label}</span>
      <div class="cbody"><div class="ctitle">${esc(x.title)}<span class="cstat ${st.k}">${st.label}</span></div>
        <div class="cnote">${campRange(x)}</div>${pops}<div class="cgo">販促の詳細 →</div></div></li>`;
  }).join("")}</ul>` : "";

  return `
    <div class="crumbs"><button class="linkbtn" data-smonth="${code}:${m}">← ${jpMonth(m)}</button>
      <button class="linkbtn" data-store="${code}">${esc(s.name)}</button></div>
    <section class="block">
      <div class="shd"><span class="rtag" style="--rc:${color}">${esc(cat)}</span>
        <h2 class="sname">${esc(s.name)}　${jpMonth(m)}　${esc(cat)}</h2></div>
    </section>
    <section class="block">${kpis}</section>
    <section class="block">
      <div class="bhead"><h2>商品詳細</h2><span class="bnote">${prods.length}品・売上順／前年同月比つき</span></div>
      ${prods.length ? `<div class="panel"><div class="cdscroll"><table class="cdmon">
        <thead><tr><th>商品</th><th class="num">売上</th><th class="num">ランク</th><th class="num">前年比</th></tr></thead>
        <tbody>${rows}</tbody></table></div></div>` : `<div class="empty">この区分の商品はこの月にありません。</div>`}
    </section>
    ${promo ? `<section class="block"><div class="bhead"><h2>関連する販促</h2></div>${promo}</section>` : ""}`;
}

// 商品を横断で探す（この店）。商品名で絞り込み、どの月・どの区分で動いたかを一覧。
// 入力での絞り込みは再描画せずDOM側で行い、入力欄のフォーカスを保つ。
function storeProductSearch(code) {
  const pm = (DATA.products_monthly || {})[code] || {};
  const months = Object.keys(pm).sort();
  if (!months.length) return "";
  const rows = [];
  for (const m of months) for (const p of prodsAtM(code, m)) {
    rows.push({ name: p.name, m, sales: p.sales || 0, rank: p.rank || "", cat: classifyCat(p.name, code, p.group) || "" });
  }
  rows.sort((a, b) => (a.name === b.name ? (a.m < b.m ? 1 : -1) : b.sales - a.sales));
  const rowHtml = rows.map(r =>
    `<li class="psrow" data-pn="${esc(String(r.name).toLowerCase())}">
      <button class="psnm" data-scat="${code}:${r.m}:${encodeURIComponent(r.cat)}" title="${esc(r.m)} ${esc(r.cat)} の商品一覧へ">${esc(r.name)}</button>
      <span class="psm">${+r.m.slice(5, 7)}月</span>
      ${r.cat ? `<span class="pscat" style="--kc:${catColor(r.cat)}">${esc(r.cat)}</span>` : "<span></span>"}
      <span class="pss"><b>${yen(r.sales)}</b>${r.rank ? `・${esc(r.rank)}` : ""}</span>
    </li>`).join("");
  return `<section class="block" id="prodsearch">
    <div class="bhead"><h2>商品を探す</h2><span class="bnote">商品名で絞り込み。どの月・区分で動いたかが分かる（${rows.length}件）</span></div>
    <input class="psinput" type="search" placeholder="商品名で絞り込み（例：パフェ / フレジェ）" data-prodsearch="${esc(code)}" aria-label="商品名で絞り込み">
    <ul class="pslist">${rowHtml}<li class="psempty" hidden>該当する商品がありません</li></ul>
  </section>`;
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
  const isHome = homeStore() === code;
  const homeBtn = `<button class="tbtn homebtn${isHome ? " on" : ""}" data-home="${esc(code)}">${
    isHome ? "★ うちの店（次から最初に開きます）" : "☆ うちの店にする"}</button>`;
  const homeBtnRow = `<div class="homerow">${homeBtn}</div>`;
  const kpis = `
    <div class="kpis">
      <div class="kpi"><div class="lbl">期間合計（${METRIC_LABELS[METRIC]}）</div>
        <div class="big">${isRatio ? "―" : man(total)}<span class="unit">${isRatio ? "" : "円"}</span></div></div>
      <div class="kpi"><div class="lbl">直近確定月${latest ? "（" + latest.m + "）" : ""}</div>
        <div class="big">${latest && latest.v != null ? (isRatio ? pct(latest.v) : yen(latest.v)) : "―"}</div></div>
      <div class="kpi"><div class="lbl">前年同月比</div>
        <div class="big ${y && !y.renewal ? (y.pct >= 0 ? "up" : "down") : ""}">${y ? signed(y.pct) + "%" : "―"}</div>
        <div class="delta">${y ? `${man(y.prev)} → ${man(y.cur)}` : "前年データなし"}${y && y.renewal ? `<br><span class="warn">⚠ ${esc(y.renewal)}｜前年は別業態の数字です</span>` : ""}</div></div>
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

  // この店の制作物（PDF・画像・資料）＋アップロード導線
  const myCreatives = creativesFor(code);
  const myCrAdd = CREATIVES_API_OK
    ? `<button class="upbtn" data-upload="store:${code}">＋ POP・写真・資料を追加</button>` : "";
  const myCreativesBlock = (myCreatives.length || CREATIVES_API_OK)
    ? `<section class="block" id="creatives">
        <div class="bhead"><h2>この店の制作物</h2><span class="bnote">${myCreatives.length}件・POP/写真/資料。押すと小窓で開く（×か背景で閉じる）</span></div>
        ${myCreatives.length ? `<div class="cgrid">${myCreatives.map(creativeCard).join("")}</div>`
          : `<p class="muted" style="margin:2px 0 10px">まだありません。PDF・写真・Excelを追加できます。</p>`}
        ${myCrAdd}
      </section>`
    : "";

  // この店の販促。効果サマリ＋並べ替え（効果順/新しい順）＋状態タブ（すべて/実施中/終了）。
  const STATUS_ORDER = { live: 0, soon: 1, done: 2 };
  const effAll = myCamps.map(c => ({ c, e: storeCampEffect(c, code), k: campStatus(c).k }));
  const nGood = effAll.filter(x => x.e.measured && x.e.pct >= 0).length;
  const nWarn = effAll.filter(x => x.e.measured && x.e.pct < 0).length;
  const nWait = effAll.filter(x => !x.e.measured && x.e.state === "確定待ち").length;
  const nNoBasis = effAll.filter(x => !x.e.measured && x.e.state === "測り方 未設定").length;
  const nSoon = effAll.filter(x => x.k === "soon").length;
  const filtered = effAll.filter(x =>
    PROMO_FILTER === "live" ? x.k === "live" : PROMO_FILTER === "done" ? x.k === "done" : true);
  const sorted = filtered.slice().sort((a, b) => {
    if (PROMO_SORT === "effect") {
      // 測定できたものを上に、前年比の高い順。未測定は下（新しい順）。
      const ap = a.e.measured ? a.e.pct : null, bp = b.e.measured ? b.e.pct : null;
      if (ap != null && bp != null) return bp - ap;
      if (ap != null) return -1;
      if (bp != null) return 1;
      return a.c.start < b.c.start ? 1 : -1;
    }
    const d = STATUS_ORDER[a.k] - STATUS_ORDER[b.k];   // 新しい順（実施中→予定→終了、各内は日付降順）
    return d !== 0 ? d : (a.c.start < b.c.start ? 1 : -1);
  });
  const pTab = (val, label) =>
    `<button class="ptab${val === PROMO_FILTER ? " on" : ""}" data-pfilter="${val}">${label}</button>`;
  const pSort = (val, label) =>
    `<button class="ptab${val === PROMO_SORT ? " on" : ""}" data-psort="${val}">${label}</button>`;
  const promoSummary = `<div class="psum">
    <span class="psum-i"><b class="up">◎ ${nGood}</b> 効いた</span>
    <span class="psum-i"><b class="down">△ ${nWarn}</b> 要改善</span>
    ${nWait ? `<span class="psum-i muted">確定待ち ${nWait}</span>` : ""}
    ${nSoon ? `<span class="psum-i muted">予定 ${nSoon}</span>` : ""}
    ${nNoBasis ? `<span class="psum-i muted">測り方未設定 ${nNoBasis}</span>` : ""}
  </div>`;
  const promoControls = `<div class="pctrl">
    <div class="ptabs">${pTab("all", "すべて")}${pTab("live", "実施中")}${pTab("done", "終了")}</div>
    <div class="ptabs"><span class="pctrl-l">並べ替え</span>${pSort("effect", "効果順")}${pSort("recent", "新しい順")}</div>
  </div>
  <div class="pterm">◎効いた=対象区分が前年同月より増／△要改善=減。<b>昨対比</b>=前年の同じ月と比較。<b>前回比</b>=前回の同じ枠と比較。</div>`;
  const promoBlock = !myCamps.length
    ? `<div class="empty">この店の施策はまだ登録されていません。config/schedule.yaml に追記すると、ここと上の売上グラフに並びます。</div>`
    : !sorted.length
      ? `<div class="empty">この条件に当てはまる販促はありません。上のタブを「すべて」に戻してください。</div>`
    : `<ul class="clist">${sorted.map(({ c, e }) => {
        const k = kindOf(c.kind);
        const range = campRange(c);
        const st = campStatus(c);
        const vmark = e.mark
          ? `<span class="cvm ${e.tone}" title="対象区分の前年比 ${signed(e.pct)}%">${e.mark} ${e.text}${e.pct != null ? " " + signed(e.pct) + "%" : ""}</span>`
          : `<span class="cvm wait">${e.state}</span>`;
        // この店ぶんの主指標（その施策が効く部門・商品）。店全体の売上を出すと、
        // 同じ店に重なっている施策が全部そろって同じ数字になる（1728 は6件重なる）。
        const tgt1 = campTargeted(c, code);
        const eff = campEffect(code, c);
        let effHtml = "";
        if (tgt1) {
          // 昨対%は上のバッジ（◎/△ +X%）に集約したので、ここでは実績金額と前年金額だけ。
          // このリストは「前回の同じ販促と比べてどうだったか＋目標・POP・メモ」を担当する。
          const prevTxt = tgt1.prev != null ? `<span class="sub">（前年 ${man(tgt1.prev)}円）</span>` : "";
          effHtml = `<div class="ceff">${esc(tgt1.label)}（確定${tgt1.months}ヶ月）<b>${man(tgt1.cur)}円</b>${prevTxt}</div>`;
          // 前回比（同じ枠の前回の回と、この店ぶんで比べる）。一覧でも一目で分かるように。
          const prevOcc = campPrevOccurrence(c);
          if (tgt1.cur && prevOcc) {
            const pb = campTargeted(prevOcc, code);
            if (pb && pb.cur) {
              const d = (tgt1.cur / pb.cur - 1) * 100;
              effHtml += `<div class="ceff sub2">前回比 <span class="${d >= 0 ? "up" : "down"}">${signed(d)}%</span><span class="sub">（前回 ${esc(prevOcc.title)}｜${man(pb.cur)}→${man(tgt1.cur)}円）</span></div>`;
            }
          }
        } else if (!campBasis(c)) {
          effHtml = `<div class="ceff muted">この販促を何で測るか未設定 — 対象の部門（例: コース）か商品名を決めると数字が出ます</div>`;
        }
        if (eff && METRIC !== "sales") {
          effHtml += `<div class="ceff sub2">店全体の${METRIC_LABELS[METRIC]} ${man(eff.cur)}円<span class="sub">（${esc(overlapNote(c))}）</span></div>`;
        }
        if (eff) {
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
        let goalHtml = "";   // 目標対象外の販促では空（旧: undefined が文字列で出ていた）
        if (tgt != null) {
          // 目標は施策ぜんぶに対して立てたもの。店1軒の数字で割らない。
          const gr1 = campGoalRate(c);
          const actual = gr1 ? gr1.cur : null;
          const rate = gr1 ? gr1.rate : null;
          const prog = rate != null
            ? ` ・ 実績(確定) ${man(actual)}円 ・ <span class="${rate >= 100 ? "up" : "down"}">達成 ${rate.toFixed(0)}%</span>`
            : ` ・ <span class="sub">実績は確定月が出てから</span>`;
          goalHtml = `<div class="cgoal">目標 <b>${man(tgt)}円</b>${prog} <button class="goalbtn" data-goal="${campKey(c)}" title="目標を編集">✎</button></div>`;
        } else if (goalEligible(c)) {
          goalHtml = `<div class="cgoal muted"><button class="goalbtn add" data-goal="${campKey(c)}">＋ 目標を入力</button></div>`;
        }
        // 要因メモ（アプリ内で入力・共有）。終了して未記入なら「振り返り未記入」を強調（PDCAのCheck）。
        const memo = memoOf(c);
        const memoHtml = memo
          ? `<div class="cmemo">${escBr(memo)} <button class="goalbtn" data-memo="${campKey(c)}" title="メモを編集">✎</button></div>`
          : (needsReview(c)
            ? `<div class="cmemo warn"><button class="goalbtn add" data-memo="${campKey(c)}">⚠ 振り返り未記入 — ＋要因メモを書く</button></div>`
            : `<div class="cmemo muted"><button class="goalbtn add" data-memo="${campKey(c)}">＋ 要因メモ</button></div>`);
        // 前回（同じ枠の前回の回）の学び＝要因メモ＋次回提案を、今回のカードに引き継ぎ表示。
        // 「去年こうだったから今年こうする」を、企画時に必ず目に入れる（PDCAのAct→次のPlan）。
        const prevOcc2 = campPrevOccurrence(c);
        let prevLearnHtml = "";
        if (prevOcc2) {
          const pMemo = memoOf(prevOcc2);
          const pProp = proposalFor(prevOcc2.id);
          const pNext = pProp && pProp.next ? pProp.next : "";
          if (pMemo || pNext) {
            prevLearnHtml = `<div class="cprev"><span class="cprev-l">前回「${esc(prevOcc2.title)}」の学び</span>${
              pMemo ? `<div class="cprev-m">${escBr(pMemo)}</div>` : ""}${
              pNext ? `<div class="cprev-n"><b>次回提案</b> ${escBr(pNext)}</div>` : ""}</div>`;
          }
        }
        // 出したPOP・資料を結果のとなりに。押すと小窓でプレビュー。未登録は実施中/予定だけ促す。
        const crs = creativesForCampaign(c.id);
        const addPop = (CREATIVES_API_OK && WRITE_OK) ? ` <button class="upbtn sm" data-upload="campaign:${c.id}">＋追加</button>` : "";
        const popHtml = crs.length
          ? `<div class="cpop"><span class="cpop-l">POP・資料 ${crs.length}</span><div class="cgrid mini">${crs.map(creativeCard).join("")}</div></div>`
          : (st.k !== "done" ? `<div class="cpop muted">POP未登録${addPop}</div>` : "");
        return `<li data-camp="${c.id}">
          <span class="kchip" style="--kc:${k.color}">${k.label}</span>
          <div class="cbody">
            <div class="ctitle">${c.title}${c.planned ? '<span class="plbadge">計画</span>' : ""}${c.scope_all ? '<span class="tagx">全店</span>' : ""}<span class="cvm-wrap">${vmark}</span>${statusControl(c, st.label, st.k)}</div>
            ${c.note ? `<div class="cnote">${c.note}</div>` : ""}
            ${c.planned && c.plan_note ? `<div class="cnote">${escBr(c.plan_note)}</div>` : ""}
            ${c.planned && c.plan_goal ? `<div class="ceff">目標 <b>${man(c.plan_goal)}円</b><span class="sub">（計画）</span></div>` : ""}
            ${effHtml}
            ${popHtml}
            ${campHeadline(c)}
            ${goalHtml}
            ${memoHtml}
            ${prevLearnHtml}
            ${(PLANS_API_OK && WRITE_OK) ? `<div class="pactions">${c.planned
              ? `<button class="plbtn" data-planedit="${c.id}">✎ 編集</button>`
              : `<button class="plbtn" data-plandup="${c.id}">⧉ 複製して起票</button>`}</div>` : ""}
            <div class="cgo">詳細を確認 →</div>
          </div>
          <span class="crange">${range}</span>
        </li>`;
      }).join("")}</ul>`;

  // 各セクションを先に組んでおき、実在するものだけをジャンプナビに載せる。
  const heroHtml = storeHero(code);
  const annualHtml = storeAnnual(code);
  const enginesHtml = storeEngines(code);
  // ページが縦に長いので、上部に「どこへでも飛べる」固定ナビを置く（誰が触っても迷わない）。
  const shareHtml = storeShareCard(code);
  const prodSearchHtml = storeProductSearch(code);
  const navItems = [
    ["hero", "今の状況"],
    shareHtml ? ["share", "共有"] : null,
    annualHtml ? ["annual", "年間"] : null,
    enginesHtml ? ["engines", "販促エンジン"] : null,
    myCamps.length ? ["promos", "販促リスト"] : null,
    myCreativesBlock ? ["creatives", "制作物"] : null,
    prodSearchHtml ? ["prodsearch", "商品検索"] : null,
    ["basics", "基礎データ"],
  ].filter(Boolean);
  const storeNav = `<nav class="snav" aria-label="店内ジャンプ">
    ${navItems.map(([id, label]) => `<button class="snavb" data-jump="${id}">${label}</button>`).join("")}
  </nav>`;

  return `
    <div class="crumbs"><button class="linkbtn" data-view="schedule">← 全店スケジュール</button></div>
    <section class="block">
      <div class="shd"><span class="rtag" style="--rc:${color}">${s.region}</span>
        <h2 class="sname">${s.name}</h2>${s.shared_facility ? '<span class="tagx">共営施設</span>' : ""}</div>
      ${homeBtnRow}
    </section>
    ${storeNav}
    ${heroHtml}
    ${shareHtml}
    ${annualHtml}
    ${enginesHtml}
    <section class="block" id="promos">
      <div class="bhead"><h2>この店の販促（個別のPDCA）</h2>
        <span class="bnote">${myCamps.length}件・各販促の効果◎/△・前回比・目標・POP・メモ。効いた/要改善で並べ替え。通年トレンドは上の「販促エンジン」で。</span>
        ${(PLANS_API_OK && WRITE_OK) ? `<button class="plannew" data-plannew="${esc(code)}">＋ 販促を起票</button>` : ""}</div>
      ${myCamps.length ? promoSummary + promoControls : ""}
      ${promoBlock}
    </section>
    ${myCreativesBlock}
    ${prodSearchHtml}
    <details class="moredet" id="basics">
      <summary>店の基礎データを見る（売上推移・部門・商品・時間帯・近隣）</summary>
      <section class="block">${kpis}${storeTargetChip(code)}</section>
      ${renderProfitability(code)}
      <section class="block">
        <div class="bhead"><h2>売上推移</h2><span class="bnote">${METRIC_LABELS[METRIC]}</span></div>
        ${own}
      </section>
      ${renderHourly(code)}
      ${renderLunchCard(code)}
      ${renderDepartments(code)}
      ${renderProducts(code)}
      ${neighBlock}
    </details>
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
  const monthLbl = abcMonthLbl(code);
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
// 原価率の直近推移ミニ折れ線（確定月・最大n ヶ月）。2点未満は出さない。
function costSpark(code, n = 6) {
  const cr = DATA.cost_rate[code] || {};
  const pts = [];
  for (const m of DATA.months) {
    if (m >= CURRENT_MONTH) continue;
    if (typeof cr[m] === "number") pts.push({ m, v: cr[m] });
  }
  const series = pts.slice(-n);
  if (series.length < 2) return "";
  const vs = series.map(p => p.v);
  const min = Math.min(...vs), max = Math.max(...vs);
  const span = (max - min) || 0.01;
  const W = 280, H = 46, padX = 6, padY = 8;
  const x = i => padX + i * (W - 2 * padX) / (series.length - 1);
  const y = v => padY + (1 - (v - min) / span) * (H - 2 * padY);
  const line = series.map((p, i) => `${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const last = series[series.length - 1], prev = series[series.length - 2];
  const cls = last.v > prev.v ? "down" : (last.v < prev.v ? "up" : "");   // 上昇＝悪化=down色
  const dots = series.map((p, i) =>
    `<circle cx="${x(i).toFixed(1)}" cy="${y(p.v).toFixed(1)}" r="${i === series.length - 1 ? 3 : 2}"/>`).join("");
  return `
    <div class="cspark ${cls}">
      <div class="cshead"><span>原価率の推移</span><span class="csrange">${series[0].m}〜${last.m}</span></div>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="cssvg" aria-hidden="true">
        <polyline points="${line}" fill="none"/>${dots}</svg>
      <div class="csfoot"><span>最小 ${pct(min)}</span><span>最大 ${pct(max)}</span>
        <span class="cscur ${cls}">直近 ${pct(last.v)}</span></div>
    </div>`;
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
  // 原価率 → 粗利率（＋前月比トレンド）。DATA.cost_rate は理論原価率。
  if (lcr) {
    const tr = costTrend(code);
    let trend = `理論粗利率 ${pct(1 - lcr.v)}`;
    if (tr) {
      const up = tr.deltaPt > 0;
      const cls = tr.deltaPt >= 1.0 ? "down" : (tr.deltaPt <= -1.0 ? "up" : "");
      const arrow = up ? "▲" : (tr.deltaPt < 0 ? "▼" : "→");
      trend += `・<span class="${cls}">前月比 ${arrow}${signed(tr.deltaPt)}pt</span>`;
    }
    cards.push(`<div class="kpi"><div class="lbl">理論原価率（${lcr.m}）</div>
      <div class="big ${lcr.v <= 0.35 ? "up" : "down"}">${pct(lcr.v)}</div>
      <div class="delta">${trend}</div></div>`);
  }
  // FW ABC の原価率→粗利率（実績ベース・全部門加重／ロス・棚卸差異は含まない）。理論と別枠で。
  const abcCr = abcCostRate(code);
  if (abcCr != null) {
    cards.push(`<div class="kpi"><div class="lbl">ABC原価率（${abcMonthOf(code) || ""}）</div>
      <div class="big ${abcCr <= 0.35 ? "up" : "down"}">${pct(abcCr)}</div>
      <div class="delta">FW ABC 粗利率 ${pct(1 - abcCr)}<small>・ロス・棚卸差異は含まない</small></div></div>`);
  }
  // 粗利（推計）＝売上×粗利率。売上と原価率がそろう最新月で
  if (ls && lcr) {
    const grM = latestWith(code, (c, m) =>
      (typeof salesAt(c, m) === "number" && typeof crAt(c, m) === "number") ? salesAt(c, m) : undefined);
    if (grM) {
      const cr = crAt(code, grM.m);
      cards.push(`<div class="kpi"><div class="lbl">粗利（推計・${grM.m}）</div>
        <div class="big">${man(grM.v * (1 - cr))}<span class="unit">円</span></div>
        <div class="delta">売上 ${man(grM.v)}円 × 理論粗利率 ${pct(1 - cr)}</div></div>`);
    }
  }
  if (!cards.length && !deptCr.length) return "";
  // 部門別 原価率→粗利率（bucket.cost_rate は % 単位＝FW ABC 実績）
  const deptChips = deptCr.length
    ? `<div class="prof-depts">${deptCr
        .slice()
        .sort((a, b) => b.sales - a.sales)
        .map(b => `<span class="pchip" style="--dc:${DEPT_COLORS[b.name] || "var(--ink-3)"}">
          <i>${esc(b.name)}</i>原価${b.cost_rate}%<b>→粗利${(100 - b.cost_rate).toFixed(0)}%</b></span>`)
        .join("")}</div>`
    : "";
  const abcNote = (deptCr.length || abcCostRate(code) != null)
    ? "　ABCは実績（FW ABC）・ロス・棚卸差異は含まない／理論は理論原価率" : "";
  return `
    <section class="block">
      <div class="bhead"><h2>収益性・客単価</h2>
        <span class="bnote">既存データ（売上・客数・理論/ABC原価率）から算出${abcNote}</span></div>
      <div class="panel">
        <div class="kpis">${cards.join("")}</div>
        ${costSpark(code)}
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
  const anyGp = items.some(p => p.sales > 0 && (p.gross != null || p.cost != null));
  const m = abcMonthOf(code);
  const rows = items.map((p, i) => {
    const pct = Math.max(3, Math.round(p.sales / max * 100));
    const rank = p.rank
      ? `<span class="prank" style="--pc:${rankColor(p.rank)}">${esc(p.rank)}</span>` : "";
    // FW ABC の原価/粗利（ロス・棚卸差異を含まない）。粗利率＝粗利÷売上。
    const gp = prodGross(p);
    const gpChip = gp != null
      ? `<span class="pgp ${gp >= 0.6 ? "up" : gp < 0.5 ? "down" : ""}" title="FW ABC 粗利率（ロス・棚卸差異は含まない）／原価率 ${pct100(1 - gp)}">粗${pct100(gp)}</span>`
      : (anyGp ? `<span class="pgp muted">―</span>` : "");
    // 0円サブ（選択メニュー内訳）を持つ親商品には開閉ボタンを付ける。内訳行は常に描画し、
    // 畳んでいるときは hidden にする（トグルはその場でDOM切替＝基礎データの折りたたみを畳まない）。
    const hasSubs = !!(p.subs && p.subs.length);
    const k = hasSubs ? subKey(code, m, p.name) : "";
    const open = hasSubs && SUBS_OPEN.has(k);
    const tog = hasSubs ? subToggleCtrl(code, m, p) : "";
    let subs = "";
    if (hasSubs) {
      subs = p.subs.map(s => {
        const q = (s.qty != null) ? `<span class="psub-q">${ten(s.qty)}点</span>` : "";
        const v = (s.sales > 0) ? `<span class="psub-v">${yen(s.sales)}</span>` : "";
        return `<li class="prowsub" data-subrow="${esc(k)}"${open ? "" : " hidden"}>` +
          `<span class="psub-n">${esc(s.name)}</span>${q}${v}</li>`;
      }).join("");
    }
    return `<li class="prow">
      <span class="pno">${i + 1}</span>
      <span class="pname">${esc(p.name)}</span>${rank}${tog}
      <span class="pbar"><span class="pfill" style="width:${pct}%"></span></span>
      <span class="psales">${yen(p.sales)}</span>${gpChip}</li>${subs}`;
  }).join("");
  const monthLbl = abcMonthLbl(code);
  const gpNote = anyGp ? "　粗＝FW ABC粗利率（ロス・棚卸差異は含まない）" : "";
  return `
    <section class="block">
      <div class="bhead"><h2>売れ筋商品（ABC）</h2>
        <span class="bnote">売上上位${items.length}品${monthLbl}　ランクはFWのABC${gpNote}</span></div>
      <div class="panel"><ul class="plist">${rows}</ul></div>
    </section>`;
}
// FW ABC 商品の粗利率（分数 0-1）。粗利があれば 粗利÷売上、無く原価があれば 1−原価÷売上。
// どちらも取り込まれていなければ null（後方互換：cost/gross は未取込店で欠落）。
function prodGross(p) {
  if (!p || !p.sales) return null;
  if (p.gross != null) return p.gross / p.sales;
  if (p.cost != null) return 1 - p.cost / p.sales;
  return null;
}
const pct100 = f => `${Math.round(f * 100)}%`;
// 店舗の FW ABC 実績原価率（分数 0-1）。全部門加重（部門売上×原価率の合計÷部門売上合計）を
// 優先し、部門原価率が無ければ売れ筋商品の原価/粗利の合計から出す。どちらも無ければ null。
function abcCostRate(code) {
  const d = deptFor(code);
  if (d && d.buckets) {
    let sales = 0, cost = 0;
    for (const b of d.buckets) {
      if (b.cost_rate == null || !(b.sales > 0)) continue;
      sales += b.sales;
      cost += b.sales * b.cost_rate / 100;
    }
    if (sales > 0) return cost / sales;
  }
  const items = (DATA.products || {})[code] || [];
  let sales = 0, cost = 0, ok = false;
  for (const p of items) {
    if (!(p.sales > 0)) continue;
    let c = null;
    if (p.cost != null) c = p.cost;
    else if (p.gross != null) c = p.sales - p.gross;
    if (c == null) continue;
    sales += p.sales; cost += c; ok = true;
  }
  return (ok && sales > 0) ? cost / sales : null;
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
    // store_sales が無い店は 0 ではなく null。0 にすると「店売上の0%」と読めてしまい、
    // 判定の構成比しきい値も永久に成立しない。
    lunchShare: e.store_sales ? lunchSales / e.store_sales : null,
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
  const store = (DATA.stores || []).find(s => s.code === code);
  const nm = (store && store.name) || e.store_name || code;
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
        <span class="bnote">ランチ部門 ${man(m.lunchSales)}・原価${pct(m.lunchCost)}・店内構成比 ${m.lunchShare == null ? "―（店売上 未取得）" : pct(m.lunchShare)}</span></div>
      <div class="panel"><ul class="llist">${mealRows}</ul>${riceNote}</div>
    </section>
    ${cmpNote}`;
}

// 時間帯別 客数・売上（FW時間帯別売上）。棒＝客数、ピーク時間帯を強調、売上は折れ線で重ねる。
// 時間帯別販促（ランチ強化・アイドルタイム対策など）の検討材料。未取込の店は空案内を出す。
function renderHourly(code) {
  const per = (DATA.hourly || {})[code];
  const monthLbl = DATA.hourly_month ? `（代表月 ${DATA.hourly_month}）` : "";
  const hours = per ? Object.keys(per).map(Number).sort((a, b) => a - b) : [];
  if (!hours.length) {
    // 時間帯別が未取込の店でも節ごと消さず、取込待ちと分かる案内を出す。
    return `
    <section class="block">
      <div class="bhead"><h2>時間帯別ピーク</h2>
        <span class="bnote">FW時間帯別売上・客数のピーク把握</span></div>
      <div class="panel"><div class="empty">時間帯別データは未取込です</div></div>
    </section>`;
  }
  const salesAt = h => (per[String(h)] || {}).sales || 0;
  const coversAt = h => (per[String(h)] || {}).covers || 0;
  const maxC = Math.max(1, ...hours.map(coversAt));
  const maxS = Math.max(1, ...hours.map(salesAt));
  // ピークは客数基準（複数タイの時は全部強調）。
  const peakC = Math.max(...hours.map(coversAt));
  const peaks = hours.filter(h => coversAt(h) === peakC);
  const peakLbl = peaks.map(h => `${h}時台`).join("・");
  const totCovers = hours.reduce((a, h) => a + coversAt(h), 0);
  const totSales = hours.reduce((a, h) => a + salesAt(h), 0);
  const bars = hours.map(h => {
    const s = salesAt(h), c = coversAt(h);
    const isPeak = peaks.includes(h);
    const pct = Math.max(2, Math.round(c / maxC * 100));
    return `<div class="hbar${isPeak ? " peak" : ""}" title="${h}時台　客数 ${c}人・売上 ${yen(s)}">
      <div class="hcol"><div class="hfill" style="height:${pct}%"></div></div>
      <div class="hlbl">${h}</div></div>`;
  }).join("");
  // 売上の折れ線を棒の上に重ねる（客数の山と売上の山のズレ＝客単価差が見える）。
  const W = 100, H = 100;  // viewBox 相対（preserveAspectRatio=none で棒に合わせて伸縮）
  const n = hours.length;
  const px = i => n > 1 ? (i + 0.5) / n * W : W / 2;
  const py = h => H - (salesAt(h) / maxS) * H * 0.92 - 4;
  const pts = hours.map((h, i) => `${px(i).toFixed(2)},${py(h).toFixed(2)}`).join(" ");
  const dots = hours.map((h, i) =>
    `<circle cx="${px(i).toFixed(2)}" cy="${py(h).toFixed(2)}" r="1.6"/>`).join("");
  const line = `<svg class="hline" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <polyline points="${pts}" fill="none"/>${dots}</svg>`;
  return `
    <section class="block">
      <div class="bhead"><h2>時間帯別ピーク</h2>
        <span class="bnote">1時間ごとの客数${monthLbl}　ピーク ${peakLbl}　棒にカーソルで売上</span></div>
      <div class="panel">
        <div class="hlegend"><span class="hlg hlg-c">■ 客数（棒）</span><span class="hlg hlg-s">— 売上（線）</span></div>
        <div class="hwrap"><div class="hbars">${bars}</div>${line}</div>
        <figcaption>客数 計${nin(totCovers)}・売上 計${man(totSales)}円／客数の山（ピーク ${peakLbl}）とアイドルタイムを見て、時間帯別の販促を検討できます。</figcaption>
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

// チャートのツールチップ（PCはホバー追従／スマホはタップで表示）。data-tip の文字を
// 「｜」で区切り、先頭を見出し・以降を各行にした暗色の小窓で出す。参考: Steppy のUI。
// スマホでは「1度目タップ＝ツールチップ表示（遷移は抑止）／同じ所を2度目タップ＝遷移」。
let TIP_POINTER = "mouse";
let TIP_TAP_EL = null;
function tipEl() {
  let t = document.getElementById("charttip");
  if (!t) {
    t = document.createElement("div");
    t.id = "charttip"; t.className = "charttip"; t.hidden = true;
    document.body.appendChild(t);
  }
  return t;
}
function tipShow(el, x, y) {
  const raw = el.getAttribute("data-tip"); if (!raw) return;
  const parts = raw.split("｜").filter(s => s !== "");
  if (!parts.length) return;
  const t = tipEl();
  t.innerHTML = `<div class="ct-h">${esc(parts[0])}</div>` +
    parts.slice(1).map(p => `<div class="ct-r">${esc(p)}</div>`).join("");
  t.hidden = false;
  tipMove(x, y);
}
function tipMove(x, y) {
  const t = document.getElementById("charttip"); if (!t || t.hidden) return;
  const w = t.offsetWidth, h = t.offsetHeight, pad = 10;
  let left = x + 14, top = y - h - 14;
  if (left + w + pad > window.innerWidth) left = x - w - 14;
  if (left < pad) left = pad;
  if (top < pad) top = y + 20;
  if (top + h + pad > window.innerHeight) top = Math.max(pad, window.innerHeight - h - pad);
  t.style.left = left + "px"; t.style.top = top + "px";
}
function tipHide() { const t = document.getElementById("charttip"); if (t) t.hidden = true; TIP_TAP_EL = null; }
function wireTips(app) {
  // ポインタ種別を覚える（click では pointerType が取れないため）。
  app.addEventListener("pointerdown", e => { TIP_POINTER = e.pointerType || "mouse"; }, true);
  // PC：ホバーで表示・追従
  app.addEventListener("pointermove", e => {
    if (TIP_POINTER !== "mouse") return;
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (el) { tipShow(el, e.clientX, e.clientY); } else { tipHide(); }
  });
  app.addEventListener("pointerleave", () => { if (TIP_POINTER === "mouse") tipHide(); });
  // スマホ：1度目タップで表示（遷移を止める）、同じ所の2度目で遷移させる
  app.addEventListener("click", e => {
    if (TIP_POINTER === "mouse") return;   // マウスのクリックは通常どおり遷移
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (!el) { tipHide(); return; }
    // 小窓/シートを開くセル（構成比の金額・F/D比）は、タップ1回で直接開く。
    // ツールチップの「2度タップ」を挟まない（携帯で開かない不具合の対策）。
    if (e.target.closest("[data-compocell],[data-fdcell]")) { tipHide(); return; }
    if (TIP_TAP_EL === el) { tipHide(); return; }  // 2度目：ツールチップを消して遷移させる
    e.preventDefault(); e.stopPropagation();       // 1度目：遷移を止めてツールチップだけ
    TIP_TAP_EL = el;
    const r = el.getBoundingClientRect();
    tipShow(el, r.left + r.width / 2, r.top + Math.min(10, r.height / 2));
  }, true);   // capture＝要素自身の遷移ハンドラより先に走らせて止める
}
// スクロールや別の場所をタップしたらツールチップを消す。
document.addEventListener("scroll", () => tipHide(), true);
document.addEventListener("pointerdown", e => {
  if (TIP_TAP_EL && (!e.target.closest || !e.target.closest("[data-tip]"))) tipHide();
}, true);

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
     <p><b>数字の読み方</b>　当月は締め前なので点線・淡色です。施策の効果は、その施策が効く部門・商品を前年の同じ月と比べています（GM改定は店全体）。理論原価率はレシピ上の値で、ロスや棚卸差異は含みません。</p>
     <p class="fine">最終更新 ${gen}</p>`;
}

document.addEventListener("DOMContentLoaded", boot);
