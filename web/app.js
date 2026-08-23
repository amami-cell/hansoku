/* 販促マネジメント・ダッシュボード（閲覧SPA）
   dashboard.json を読むだけ。表示のたびに DB は叩かない。

   画面の骨格:
     全社ビュー … エリア（大阪/東京/京都/兵庫/福岡）別のサマリカード
     エリアビュー … そのエリアの店を1つのグラフに重ねて近隣比較＋ランキング
     総合（最下部）… 全店の月別表（集約） */
"use strict";

const METRIC_LABELS = {
  sales: "売上", food_sales: "フード売上", drink_sales: "ドリンク売上",
  food_theory_cost: "フード理論原価", drink_theory_cost: "ドリンク理論原価",
  cost_rate: "理論原価率",
};
const yen = n => "¥" + Math.round(n).toLocaleString("ja-JP");
const man = n => (n / 10000).toFixed(0) + "万";
const pct = n => (n * 100).toFixed(1) + "%";

// X軸用。年が変わる境目と先頭では "YY年M月"、それ以外は "M月"
function axisLabel(months, i) {
  const [y, mo] = months[i].split("-");
  const prevYear = i > 0 ? months[i - 1].split("-")[0] : null;
  return (i === 0 || y !== prevYear) ? `${y.slice(2)}年${+mo}月` : `${+mo}月`;
}

// エリアごとのベース色（彩度は抑えめ。状態色と混同しない）
const REGION_COLORS = {
  "大阪": "#2E4A7D", "東京": "#1F7A5C", "京都": "#8A5A2B",
  "兵庫": "#6E4B8A", "福岡": "#9A3B54", "未分類": "#6B7280",
};
const regionColor = r => REGION_COLORS[r] || "#6B7280";

let DATA = null;
let VIEW = "all";      // "all"（全社）または エリア名
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
  buildTabs();
  buildMetricSelect();
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
const storeName = code => (DATA.stores.find(s => s.code === code) || {}).name || code;
const storesOf = region =>
  (DATA.regions.find(r => r.name === region) || { stores: [] }).stores
    .filter(c => DATA.monthly[c]);           // 実績のある店だけ

// 店 × 月 の値（原価率だけは別テーブル）
function valueAt(code, month) {
  if (METRIC === "cost_rate") return (DATA.cost_rate[code] || {})[month];
  return ((DATA.monthly[code] || {})[month] || {})[METRIC];
}
// 複数店を合算した月次系列。原価率は合算しない（比率のため）
function seriesSum(codes, months) {
  return months.map(m => {
    let sum = 0, any = false;
    for (const c of codes) {
      const v = ((DATA.monthly[c] || {})[m] || {})[METRIC];
      if (typeof v === "number") { sum += v; any = true; }
    }
    return any ? sum : null;
  });
}
const periodTotal = codes => {
  let sum = 0;
  for (const c of codes) for (const m of DATA.months) {
    const v = ((DATA.monthly[c] || {})[m] || {})[METRIC];
    if (typeof v === "number" && m !== CURRENT_MONTH) sum += v;
  }
  return sum;
};

// ── タブ（全社＋エリア）────────────────────────────────────────────────
function buildTabs() {
  const nav = document.getElementById("tabs");
  const tabs = [["all", "全社"]].concat(DATA.regions.map(r => [r.name, r.name]));
  nav.innerHTML = tabs.map(([id, label]) => {
    const n = id === "all" ? DATA.stores.length
      : (DATA.regions.find(r => r.name === id) || { stores: [] }).stores.length;
    return `<button class="tab" data-id="${id}">${label}<span class="cnt">${n}</span></button>`;
  }).join("");
  nav.querySelectorAll(".tab").forEach(b =>
    b.addEventListener("click", () => { VIEW = b.dataset.id; syncTabs(); render(); }));
  syncTabs();
}
function syncTabs() {
  document.querySelectorAll("#tabs .tab").forEach(b =>
    b.classList.toggle("on", b.dataset.id === VIEW));
}

function buildMetricSelect() {
  const sel = document.getElementById("metricsel");
  sel.innerHTML = DATA.metrics.concat(["cost_rate"])
    .map(m => `<option value="${m}">${METRIC_LABELS[m] || m}</option>`).join("");
  sel.value = METRIC;
  sel.addEventListener("change", () => { METRIC = sel.value; render(); });
}

// ── 描画 ─────────────────────────────────────────────────────────────────
function render() {
  const app = document.getElementById("app");
  app.innerHTML = VIEW === "all" ? renderAll() : renderRegion(VIEW);
  app.querySelectorAll("[data-goto]").forEach(el =>
    el.addEventListener("click", () => {
      VIEW = el.dataset.goto; syncTabs(); render();
      window.scrollTo({ top: 0, behavior: "smooth" });
    }));
  wireEmphasis(app);
}

// 全社: エリア別サマリカード ＋ 最下部に総合表
function renderAll() {
  const months = DATA.months;
  const isRatio = METRIC === "cost_rate";
  const cards = DATA.regions.map(r => {
    const codes = storesOf(r.name);
    const total = isRatio ? null : periodTotal(codes);
    return regionCard(r.name, codes, months, total);
  }).join("");
  return `
    <section class="block">
      <div class="bhead"><h2>エリア別</h2>
        <span class="bnote">${isRatio ? "原価率（低いほど良い）" : METRIC_LABELS[METRIC] + "・期間合計（当月の暫定は除く）"}</span></div>
      <div class="rgrid">${cards}</div>
    </section>
    ${renderOverviewTable()}
  `;
}

function regionCard(region, codes, months, total) {
  const color = regionColor(region);
  const series = METRIC === "cost_rate" ? null : seriesSum(codes, months);
  const spark = series ? sparkline(series, color) : "";
  const totalLine = total == null ? ""
    : `<div class="rbig">${man(total)}<span class="unit">円</span></div>`;
  return `
    <button class="rcard" data-goto="${region}" style="--rc:${color}">
      <div class="rtop"><span class="dot"></span>${region}<span class="rn">${codes.length}店</span></div>
      ${totalLine}
      ${spark}
      <div class="rmore">エリアを見る →</div>
    </button>`;
}

// エリア: そのエリアの店を1グラフに重ねて比較＋ランキング
function renderRegion(region) {
  const codes = storesOf(region);
  const months = DATA.months;
  const color = regionColor(region);
  if (!codes.length) {
    return `<section class="block"><div class="empty">${region}エリアに実績のある店舗がありません。</div></section>`;
  }
  const isRatio = METRIC === "cost_rate";
  const ranked = codes.map(c => ({
    code: c,
    total: isRatio ? latestRatio(c) : periodTotal([c]),
  })).sort((a, b) => (b.total || 0) - (a.total || 0));
  const max = Math.max(...ranked.map(r => r.total || 0), 1);
  const rows = ranked.map((r, i) => {
    const shown = r.total == null ? "―" : isRatio ? pct(r.total) : yen(r.total);
    const w = ((r.total || 0) / max * 100).toFixed(1);
    return `<tr>
      <td class="rk">${i + 1}</td>
      <td>${storeName(r.code)}</td>
      <td class="num">${shown}</td>
      <td><i class="mag" style="width:${w}%;background:${color}"></i></td>
    </tr>`;
  }).join("");
  return `
    <section class="block">
      <div class="bhead">
        <h2><span class="dot" style="background:${color}"></span>${region}エリア</h2>
        <span class="bnote">${codes.length}店の${METRIC_LABELS[METRIC]}推移</span>
      </div>
      <div class="panel"><div class="chartwrap">${multiLine(codes, months, region)}</div>
        <div class="lg">${legend(codes, region)}</div>
      </div>
    </section>
    <section class="block">
      <div class="bhead"><h2>店舗ランキング</h2>
        <span class="bnote">${isRatio ? "最新月の原価率" : "期間合計（当月の暫定は除く）"}</span></div>
      <div class="panel"><table class="rank"><tbody>${rows}</tbody></table></div>
    </section>
  `;
}

function latestRatio(code) {
  const t = DATA.cost_rate[code] || {};
  for (let i = DATA.months.length - 1; i >= 0; i--) {
    const m = DATA.months[i];
    if (m !== CURRENT_MONTH && typeof t[m] === "number") return t[m];
  }
  return null;
}

// 総合表（全店 × 直近4ヶ月・エリアごとに区切る）
function renderOverviewTable() {
  const months = DATA.months.slice(-4);
  const head = months.map(m =>
    `<th class="num">${axisLabel(DATA.months, DATA.months.indexOf(m))}</th>`).join("");
  const body = DATA.regions.map(r => {
    const codes = storesOf(r.name);
    const stores = codes.map(c => {
      const cells = months.map(m => {
        const v = valueAt(c, m);
        const prov = isProvisional(m) ? " prov" : "";
        const shown = v == null ? "―" : METRIC === "cost_rate" ? pct(v) : yen(v);
        return `<td class="num${prov}">${shown}</td>`;
      }).join("");
      return `<tr><td class="rgn" style="--rc:${regionColor(r.name)}">${storeName(c)}</td>${cells}</tr>`;
    }).join("");
    return `<tr class="grp"><td colspan="${months.length + 1}">${r.name}（${codes.length}店）</td></tr>${stores}`;
  }).join("");
  return `
    <section class="block">
      <div class="bhead"><h2>総合</h2><span class="bnote">全店 × 直近4ヶ月・${METRIC_LABELS[METRIC]}（当月は暫定）</span></div>
      <div class="panel"><div class="chartwrap">
        <table class="ovr"><thead><tr><th>店舗</th>${head}</tr></thead><tbody>${body}</tbody></table>
      </div></div>
    </section>`;
}

// ── SVG 描画 ─────────────────────────────────────────────────────────────
function sparkline(series, color) {
  const vals = series.filter(v => v != null);
  if (vals.length < 2) return "";
  const max = Math.max(...vals), min = Math.min(...vals, 0);
  const W = 220, H = 40, n = series.length;
  const x = i => (i / (n - 1)) * (W - 4) + 2;
  const y = v => H - 4 - (v - min) / (max - min || 1) * (H - 8);
  let d = "", started = false;
  series.forEach((v, i) => {
    if (v == null) { started = false; return; }
    d += (started ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1) + " ";
    started = true;
  });
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${d}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
}

function multiLine(codes, months, region) {
  const W = 720, H = 300, PL = 56, PR = 16, PT = 18, PB = 34;
  const all = [];
  const seriesByCode = codes.map(c => months.map(m => {
    const v = METRIC === "cost_rate" ? (DATA.cost_rate[c] || {})[m] : valueAt(c, m);
    if (typeof v === "number") all.push(v);
    return typeof v === "number" ? v : null;
  }));
  if (!all.length) return `<div class="empty">データがありません</div>`;
  const max = Math.max(...all), min = METRIC === "cost_rate" ? Math.min(...all) : 0;
  const n = months.length;
  const x = i => PL + (i / Math.max(n - 1, 1)) * (W - PL - PR);
  const y = v => PT + (1 - (v - min) / (max - min || 1)) * (H - PT - PB);
  const base = regionColor(region);

  let grid = "";
  for (let g = 0; g <= 4; g++) {
    const gy = PT + (g / 4) * (H - PT - PB);
    const gv = max - (g / 4) * (max - min);
    grid += `<line x1="${PL}" y1="${gy}" x2="${W - PR}" y2="${gy}" stroke="var(--line)"/>`;
    const lbl = METRIC === "cost_rate" ? (gv * 100).toFixed(0) + "%" : man(gv);
    grid += `<text x="${PL - 8}" y="${gy + 3}" text-anchor="end" class="axt">${lbl}</text>`;
  }
  let xlab = "";
  const step = Math.ceil(n / 8);
  months.forEach((m, i) => {
    if (i % step === 0 || i === n - 1)
      xlab += `<text x="${x(i)}" y="${H - 12}" text-anchor="middle" class="axt">${axisLabel(months, i)}</text>`;
  });
  // 店数が多いと線が密集して読めない。既定は細く薄く、hover/凡例で1店だけ強調する。
  const many = codes.length > 5;
  const lines = seriesByCode.map((ser, si) => {
    const col = shade(base, si, codes.length);
    let d = "", started = false;
    ser.forEach((v, i) => {
      if (v == null) { started = false; return; }
      d += (started ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1) + " ";
      started = true;
    });
    const w = many ? 1.4 : 2;
    const op = many ? 0.5 : 1;
    return `<path class="ml" data-si="${si}" d="${d}" fill="none" stroke="${col}"
      stroke-width="${w}" stroke-opacity="${op}" stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join("");

  return `<svg class="mlsvg" viewBox="0 0 ${W} ${H}" width="100%" height="${H}" style="min-width:560px" role="img"
      aria-label="${region}エリア ${codes.length}店の${METRIC_LABELS[METRIC]}推移">
    ${grid}${xlab}${lines}</svg>`;
}

function legend(codes, region) {
  const base = regionColor(region);
  return codes.map((c, i) =>
    `<span class="lgi" data-si="${i}"><i style="background:${shade(base, i, codes.length)}"></i>${storeName(c)}</span>`).join("");
}

// エリアグラフ: 凡例や線に触れた店だけ強調する
function wireEmphasis(root) {
  const svg = root.querySelector(".mlsvg");
  if (!svg) return;
  const paths = [...svg.querySelectorAll(".ml")];
  const items = [...root.querySelectorAll(".lgi")];
  const many = paths.length > 5;
  const focus = si => {
    paths.forEach(p => {
      const on = si == null || p.dataset.si === String(si);
      p.setAttribute("stroke-opacity", on ? "1" : "0.12");
      p.setAttribute("stroke-width", (p.dataset.si === String(si)) ? "3" : (many ? "1.4" : "2"));
    });
    items.forEach(li => li.classList.toggle("mut", si != null && li.dataset.si !== String(si)));
  };
  const reset = () => {
    paths.forEach(p => {
      p.setAttribute("stroke-opacity", many ? "0.5" : "1");
      p.setAttribute("stroke-width", many ? "1.4" : "2");
    });
    items.forEach(li => li.classList.remove("mut"));
  };
  paths.forEach(p => { p.addEventListener("mouseenter", () => focus(p.dataset.si)); p.addEventListener("mouseleave", reset); });
  items.forEach(li => {
    li.addEventListener("mouseenter", () => focus(li.dataset.si));
    li.addEventListener("mouseleave", reset);
  });
}

// ベース色を店ごとに濃淡へ散らす（HSL明度を変える）
function shade(hex, i, total) {
  const { h, s, l } = hexToHsl(hex);
  if (total <= 1) return hex;
  const span = 34;
  const nl = Math.max(28, Math.min(72, l - span / 2 + (span * i) / (total - 1)));
  return `hsl(${h} ${s}% ${nl}%)`;
}
function hexToHsl(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255,
        g = parseInt(hex.slice(3, 5), 16) / 255,
        b = parseInt(hex.slice(5, 7), 16) / 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0; const l = (mx + mn) / 2;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  if (d !== 0) {
    if (mx === r) h = ((g - b) / d) % 6;
    else if (mx === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60; if (h < 0) h += 360;
  }
  return { h: Math.round(h), s: Math.round(s * 100), l: Math.round(l * 100) };
}

// ── 注記 ─────────────────────────────────────────────────────────────────
function fillNotice() {
  const gen = DATA.generated_at ? DATA.generated_at.replace("T", " ").replace("+00:00", " UTC") : "";
  document.getElementById("notice").innerHTML =
    `<p><b>データ</b>　FW実績・全${DATA.stores.length}店（大阪16／東京3／京都3／兵庫1／福岡1）。当月は締め前の暫定値のため点線・淡色で示します。</p>
     <p><b>これから</b>　施策の登録・目標対比・ランチ／ディナー比（時間帯別）は順次追加します。時間帯比はFWの時間帯別売上を取り込んでから出せます。</p>
     <p class="fine">最終更新 ${gen}</p>`;
}

document.addEventListener("DOMContentLoaded", boot);
