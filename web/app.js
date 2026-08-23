/* 販促マネジメント・ダッシュボード（閲覧SPA）
   dashboard.json を読むだけ。表示のたびに DB は叩かない。 */
"use strict";

const METRIC_LABELS = {
  sales: "売上", food_sales: "フード売上", drink_sales: "ドリンク売上",
  food_theory_cost: "フード理論原価", drink_theory_cost: "ドリンク理論原価",
  cost_rate: "理論原価率",
};
const yen = n => "¥" + Math.round(n).toLocaleString("ja-JP");
const pct = n => (n * 100).toFixed(1) + "%";
const monthLabel = m => { const [, mo] = m.split("-"); return `${+mo}月`; };
// X軸用。年が変わる境目と先頭では "YY年M月" を出し、それ以外は "M月"
function axisLabel(months, i) {
  const [y, mo] = months[i].split("-");
  const prevYear = i > 0 ? months[i - 1].split("-")[0] : null;
  return (i === 0 || y !== prevYear) ? `${y.slice(2)}年${+mo}月` : `${+mo}月`;
}

let DATA = null, storeSel, metricSel;
// 当月（YYYY-MM）。これに一致する月だけを「暫定（中間値）」として点線・淡色にする。
const CURRENT_MONTH = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
})();
const isProvisional = m => m === CURRENT_MONTH;

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
  setupControls();
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

function setupControls() {
  storeSel = document.getElementById("storesel");
  metricSel = document.getElementById("metricsel");

  storeSel.innerHTML = `<option value="*">全店（合計）</option>` +
    DATA.stores.map(s => `<option value="${s.code}">${s.name}</option>`).join("");

  const metrics = [...DATA.metrics, "cost_rate"];
  metricSel.innerHTML = metrics
    .map(m => `<option value="${m}">${METRIC_LABELS[m] || m}</option>`).join("");

  storeSel.addEventListener("change", render);
  metricSel.addEventListener("change", render);
  document.getElementById("gen").textContent =
    "更新 " + (DATA.generated_at || "").replace("T", " ").slice(0, 16);
}

/* ある店・ある指標の、月ごとの値を返す */
function series(code, metric) {
  const months = DATA.months;
  if (metric === "cost_rate") {
    if (code === "*") {
      // 全店の原価率は、原価合計 ÷ 売上合計で組み直す
      return months.map(m => {
        let cost = 0, sales = 0;
        for (const s of DATA.stores) {
          const row = (DATA.monthly[s.code] || {})[m];
          if (!row) continue;
          cost += (row.food_theory_cost || 0) + (row.drink_theory_cost || 0);
          sales += row.sales || 0;
        }
        return sales ? cost / sales : null;
      });
    }
    return months.map(m => (DATA.cost_rate[code] || {})[m] ?? null);
  }
  if (code === "*") {
    return months.map(m => {
      let sum = 0, seen = false;
      for (const s of DATA.stores) {
        const v = ((DATA.monthly[s.code] || {})[m] || {})[metric];
        if (v != null) { sum += v; seen = true; }
      }
      return seen ? sum : null;
    });
  }
  return months.map(m => ((DATA.monthly[code] || {})[m] || {})[metric] ?? null);
}

function render() {
  const code = storeSel.value, metric = metricSel.value;
  const isRate = metric === "cost_rate";
  const vals = series(code, metric);
  const months = DATA.months;
  const fmt = isRate ? pct : yen;

  const app = document.getElementById("app");
  app.innerHTML = "";

  // ── KPI ──
  const valid = vals.map((v, i) => [months[i], v]).filter(x => x[1] != null);
  const latest = valid.length ? valid[valid.length - 1] : null;
  const prev = valid.length > 1 ? valid[valid.length - 2] : null;
  let kpiHtml = "";
  if (latest) {
    const [lm, lv] = latest;
    let delta = "";
    if (prev && prev[1]) {
      const diff = isRate ? (lv - prev[1]) * 100 : (lv / prev[1] - 1) * 100;
      const better = isRate ? diff < 0 : diff > 0;   // 原価率は下がるほど良い
      const cls = Math.abs(diff) < 0.05 ? "flat" : better ? "up" : "down";
      const arrow = cls === "flat" ? "→" : better ? "▲" : "▼";
      const shown = isRate ? (diff >= 0 ? "+" : "") + diff.toFixed(1) + "pt"
                           : (diff >= 0 ? "+" : "") + diff.toFixed(1) + "%";
      delta = `<div class="delta ${cls}"><span>${arrow}</span>前月比 ${shown}</div>`;
    }
    kpiHtml = `<div class="kpis">
      <div class="kpi"><div class="lbl">${monthLabel(lm)}の${METRIC_LABELS[metric]}</div>
        <div class="big">${fmt(lv)}</div>${delta}</div>
      <div class="kpi"><div class="lbl">期間合計</div>
        <div class="big">${isRate ? "—" : fmt(valid.reduce((a, x) => a + x[1], 0))}</div>
        <div class="delta flat">${valid.length}ヶ月分</div></div>
    </div>`;
  }

  // ── 折れ線 ──
  app.innerHTML = `
    <section>
      <div class="shead"><h2>${storeName(code)}</h2>
        <span class="snote">${METRIC_LABELS[metric]}の推移</span></div>
      ${kpiHtml}
      <div style="height:14px"></div>
      <div class="panel">
        <div class="legend"><span><i style="background:var(--accent)"></i>${METRIC_LABELS[metric]}</span>
          <span><i style="background:var(--ink-3);opacity:.5"></i>暫定（当月）</span></div>
        <div class="chartwrap">${lineChart(months, vals, fmt, isRate)}</div>
        <figcaption>当月は月途中の「中間」値のため点線・淡色で示します。前年同月と比べる際は暫定値であることに注意してください。</figcaption>
      </div>
    </section>
    ${code === "*" ? storeTable(metric, isRate, fmt) : ""}
  `;
  attachHover();
}

function storeName(code) {
  if (code === "*") return "全店合計";
  const s = DATA.stores.find(x => x.code === code);
  return s ? s.name : code;
}

/* SVG 折れ線。最後の月は暫定として点線・淡色にする */
function lineChart(months, vals, fmt, isRate) {
  const W = Math.max(560, months.length * 92), H = 250;
  const padL = 62, padR = 24, padT = 24, padB = 42;
  const nums = vals.filter(v => v != null);
  if (!nums.length) return `<div class="empty">この指標のデータがありません</div>`;
  const max = Math.max(...nums), min = isRate ? Math.min(...nums, 0) : 0;
  const span = max - min || 1;
  const x = i => padL + (months.length === 1 ? (W - padL - padR) / 2 : i * (W - padL - padR) / (months.length - 1));
  const y = v => padT + (1 - (v - min) / span) * (H - padT - padB);

  const gl = [0, .25, .5, .75, 1].map(t => {
    const yy = padT + t * (H - padT - padB);
    const v = max - t * span;
    return `<line x1="${padL}" y1="${yy.toFixed(1)}" x2="${W - padR}" y2="${yy.toFixed(1)}" stroke="var(--line)"/>
      <text x="${padL - 8}" y="${(yy + 3).toFixed(1)}" font-size="10" fill="var(--ink-3)" text-anchor="end" style="font-variant-numeric:tabular-nums">${isRate ? (v * 100).toFixed(0) + "%" : compact(v)}</text>`;
  }).join("");

  // 確定は実線、暫定月（当月）へ向かう区間だけ点線にする
  const pts = vals.map((v, i) => v == null ? null : [x(i), y(v)]);
  const solid = pts.filter(Boolean);
  const dash = [];
  const provIdx = months.findIndex(isProvisional);
  if (provIdx > 0 && pts[provIdx] && pts[provIdx - 1]) {
    dash.push(pts[provIdx - 1], pts[provIdx]);
  }
  const line = arr => arr.filter(Boolean).map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
  const area = (() => {
    const p = pts.filter(Boolean);
    if (p.length < 2) return "";
    return `<path d="${line(p)} L${p[p.length - 1][0].toFixed(1)},${(H - padB)} L${p[0][0].toFixed(1)},${(H - padB)} Z" fill="var(--accent)" opacity=".11"/>`;
  })();

  const dots = vals.map((v, i) => {
    if (v == null) return "";
    const prov = isProvisional(months[i]);
    return `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4" fill="var(--surface)" stroke="${prov ? "var(--ink-3)" : "var(--accent)"}" stroke-width="2" ${prov ? 'stroke-dasharray="2 2"' : ""} class="dot" data-i="${i}"/>`;
  }).join("");

  const xlab = months.map((m, i) => `<text x="${x(i).toFixed(1)}" y="${H - padB + 20}" font-size="11" fill="var(--ink-3)" text-anchor="middle">${axisLabel(months, i)}</text>`).join("");
  const hot = months.map((m, i) => vals[i] == null ? "" :
    `<rect class="hit" data-i="${i}" x="${(x(i) - 26).toFixed(1)}" y="${padT}" width="52" height="${H - padT - padB}" fill="transparent"/>`).join("");

  // データを hover 用に埋める
  window.__chart = { months, vals, fmt };
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" style="min-width:${W > 620 ? 560 : W}px"
    aria-label="${storeName(storeSel.value)}の${METRIC_LABELS[metricSel.value]}推移">
    ${gl}${area}
    <path d="${line(solid)}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <path d="${line(dash)}" fill="none" stroke="var(--ink-3)" stroke-width="2" stroke-dasharray="4 3" stroke-linecap="round" opacity=".8"/>
    ${dots}${xlab}${hot}
  </svg>`;
}

function compact(n) {
  if (n >= 1e8) return (n / 1e8).toFixed(1) + "億";
  if (n >= 1e4) return Math.round(n / 1e4) + "万";
  return Math.round(n).toString();
}

/* 全店表示のときだけ、店舗別の表を出す */
function storeTable(metric, isRate, fmt) {
  const month = DATA.months[DATA.months.length - 1];
  const rows = DATA.stores.map(s => {
    let v;
    if (isRate) v = (DATA.cost_rate[s.code] || {})[month] ?? null;
    else v = ((DATA.monthly[s.code] || {})[month] || {})[metric] ?? null;
    return { s, v };
  }).filter(r => r.v != null);
  if (isRate) rows.sort((a, b) => b.v - a.v); else rows.sort((a, b) => b.v - a.v);
  const max = Math.max(...rows.map(r => Math.abs(r.v)), 1);
  const body = rows.map(r => `<tr>
    <td><i class="bchip" style="background:${brandColor(r.s.brand)}"></i>${r.s.name}</td>
    <td class="num ${isRate && r.v > 0.30 ? "rate hot" : ""}">${fmt(r.v)}</td>
    <td style="width:32%"><i class="magnitude" style="width:${Math.max(2, Math.abs(r.v) / max * 100).toFixed(0)}%"></i></td>
  </tr>`).join("");
  return `<section>
    <div class="shead"><h2>店舗別</h2><span class="snote">${monthLabel(month)}・${METRIC_LABELS[metric]}${isRate ? "（30%超は赤）" : ""}</span></div>
    <div class="panel"><table>
      <thead><tr><th>店舗</th><th class="num">${METRIC_LABELS[metric]}</th><th>　</th></tr></thead>
      <tbody>${body}</tbody></table></div>
  </section>`;
}

const BRAND_COLORS = {
  SUSABIYU:"#3E5C8C", GOLD:"#7C6A55", LARGO:"#5B8A72", GIFUYA:"#A66A4E",
  ARATA:"#6B7A8F", UMAMI:"#8C7A3E", NDANDA:"#7C5568", NAGAGUTSU:"#4E7C8C",
  KUMANOTORIYAKI:"#8C5A3E", CHACHAN:"#B0761A", TAIDAI:"#5568A0",
  AWAKURAI:"#7A4E8C", HIYOKOHANTEN:"#8C8340", TANUKIYA:"#6E6E6E",
};
const brandColor = b => BRAND_COLORS[b] || "#6E6E6E";

function attachHover() {
  const tip = document.getElementById("tip");
  const svg = document.querySelector("svg");
  if (!svg || !window.__chart) return;
  const { months, vals, fmt } = window.__chart;
  svg.querySelectorAll(".hit").forEach(hit => {
    const i = +hit.dataset.i;
    const show = e => {
      const prov = isProvisional(months[i]);
      tip.innerHTML = `<div class="tname">${months[i]}${prov ? "（暫定）" : ""}</div>
        <div class="trow"><span>${METRIC_LABELS[metricSel.value]}</span><span>${fmt(vals[i])}</span></div>`;
      tip.classList.add("on");
      const p = (e.touches ? e.touches[0] : e);
      tip.style.left = Math.min(p.clientX + 14, innerWidth - 200) + "px";
      tip.style.top = (p.clientY - 10) + "px";
    };
    hit.addEventListener("mousemove", show);
    hit.addEventListener("mouseleave", () => tip.classList.remove("on"));
    hit.addEventListener("touchstart", show, { passive: true });
  });
}

function fillNotice() {
  const n = document.getElementById("notice");
  const p = DATA.period;
  n.innerHTML = `<p><b>このデータについて</b>　FW（Foodist Journal）の月次実績を集約したものです。
    対象期間 ${p.from} 〜 ${p.to}、全 ${DATA.stores.length} 店。表示のたびにデータベースは参照せず、
    夜間バッチが書き出した結果を読んでいます。</p>
    <p><b>暫定値</b>　当月は月途中の「中間」値です。確定は月末締め後に置き換わります。</p>
    <p><b>これから</b>　施策の登録・目標対比・制作物ギャラリーはこの先で追加します。
    日別売上が入れば、改装や施策の前後を日単位で追えるようになります。</p>`;
}

boot();
