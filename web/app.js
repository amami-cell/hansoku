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
const KIND = {
  fair:    { label: "フェア",     color: "#C8791E" },
  menu:    { label: "新メニュー", color: "#2E8B57" },
  promo:   { label: "販促",       color: "#2E4A7D" },
  renewal: { label: "改装明け",   color: "#1F5FBF" },
  closure: { label: "休業",       color: "#8A8F99" },
  switch:  { label: "設備",       color: "#7A4FA0" },
};
const kindOf = k => KIND[k] || KIND.promo;

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

let DATA = null;
let VIEW = { kind: "schedule" };   // schedule(TOP) | store,code | list(店カード) | overview
let METRIC = "sales";

const CURRENT_MONTH = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
})();
const isProvisional = m => m === CURRENT_MONTH;

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
  else if (VIEW.kind === "overview") app.innerHTML = renderOverview();
  else if (VIEW.kind === "list") app.innerHTML = renderList();
  else app.innerHTML = renderSchedule();

  app.querySelectorAll("[data-store]").forEach(el =>
    el.addEventListener("click", () => go({ kind: "store", code: el.dataset.store })));
  app.querySelectorAll("[data-view]").forEach(el =>
    el.addEventListener("click", () => go({ kind: el.dataset.view })));
  wireEmphasis(app);
}
function go(v) { VIEW = v; render(); window.scrollTo({ top: 0, behavior: "smooth" }); }

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
      return `<div class="cmk" style="left:${P(frac(c.start))};--lane:${lane};--kc:${k.color}" title="${tip}">
        <span class="cmk-t">${c.title}</span></div>`;
    }
    const l = frac(c.start), w = Math.max(frac(c.end) - l, 0.015);
    return `<div class="cbar" style="left:${P(l)};width:${P(w)};--lane:${lane};--kc:${k.color}" title="${tip}">${c.title}</div>`;
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
    <div class="ovrlink">
      <button class="linkbtn" data-view="list">店舗カードで見る →</button>
      <span class="sep">／</span>
      <button class="linkbtn" data-view="overview">エリア・全店の表 →</button>
    </div>`;
}

// ── 店一覧（店舗カード）──────────────────────────────────────────────────
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
      return `
        <button class="scard" data-store="${code}" style="--rc:${color}">
          <div class="stop"><span class="rtag">${r.name}</span>${storeName(code)}</div>
          <div class="sbig">${man(total)}<span class="unit">円</span></div>
          ${spark}
          <div class="sfoot">${yline}<span class="more">詳しく →</span></div>
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

// ── 店舗詳細（販促×売上×近隣）───────────────────────────────────────────
function renderStore(code) {
  const s = store(code);
  const months = DATA.months;
  const color = regionColor(s.region);
  const isRatio = METRIC === "cost_rate";
  const y = yoy(code);

  // 見出しKPI
  const total = periodTotal(code);
  const latest = latestConfirmed(code);
  const kpis = `
    <div class="kpis">
      <div class="kpi"><div class="lbl">期間合計（${METRIC_LABELS[METRIC]}）</div>
        <div class="big">${isRatio ? "―" : man(total)}<span class="unit">${isRatio ? "" : "円"}</span></div></div>
      <div class="kpi"><div class="lbl">直近確定月${latest ? "（" + latest.m + "）" : ""}</div>
        <div class="big">${latest && latest.v != null ? (isRatio ? pct(latest.v) : yen(latest.v)) : "―"}</div></div>
      <div class="kpi"><div class="lbl">前年同月比</div>
        <div class="big ${y ? (y.pct >= 0 ? "up" : "down") : ""}">${y ? signed(y.pct) + "%" : "―"}</div>
        <div class="delta">${y ? `${man(y.prev)} → ${man(y.cur)}` : "前年データなし"}</div></div>
    </div>`;

  // この店の施策（config/schedule.yaml 由来）
  const myCamps = (DATA.campaigns || [])
    .filter(c => c.stores.includes(code))
    .sort((a, b) => a.start < b.start ? -1 : 1);

  // 自店の売上推移。施策期間はグラフに帯として重ねる
  const own = `
    <div class="panel"><div class="chartwrap">${singleLine(code, months, color, myCamps)}</div>
      <figcaption>当月は締め前の暫定値（点線）。色帯は施策期間です。</figcaption>
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

  // この店の販促（施策の一覧）
  const promoBlock = myCamps.length
    ? `<ul class="clist">${myCamps.map(c => {
        const k = kindOf(c.kind);
        const range = c.start === c.end ? c.start : `${c.start} 〜 ${c.end}`;
        return `<li>
          <span class="kchip" style="--kc:${k.color}">${k.label}</span>
          <div class="cbody"><div class="ctitle">${c.title}${c.scope_all ? '<span class="tagx">全店</span>' : ""}</div>
            ${c.note ? `<div class="cnote">${c.note}</div>` : ""}</div>
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
    </section>
    <section class="block">
      <div class="bhead"><h2>この店の販促</h2>
        <span class="bnote">${myCamps.length}件</span></div>
      ${promoBlock}
    </section>
    <section class="block">
      <div class="bhead"><h2>売上推移</h2><span class="bnote">${METRIC_LABELS[METRIC]}</span></div>
      ${own}
    </section>
    ${neighBlock}
  `;
}

// ── エリア・全店の一覧（控えめ・下位ページ）──────────────────────────────
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
    <section class="block">
      <div class="bhead"><h2>エリア・全店の一覧</h2>
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
  const { W, H, x, y, grid, xlab, PT, PB } = chartFrame(months, vals, isRatio);
  const n = months.length;
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
    ${bands}${grid}${xlab}${area}
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
