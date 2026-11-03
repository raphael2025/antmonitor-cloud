/* 云端总览前端 */
"use strict";

// ---- 调色板(已按暗色面板 #161b22 校验)：场地序列色按 config 顺序固定分配，不随筛选重排 ----
const SERIES = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"];
const INK = {
  get grid() { return window.AntTheme?.palette().grid || "#21262d"; },
  get axis() { return window.AntTheme?.palette().axis || "#30363d"; },
  get tick() { return window.AntTheme?.palette().tick || "#6e7681"; },
  get label() { return window.AntTheme?.palette().label || "#adbac7"; },
};
const METRIC = { hashrate: "#3987e5", online: "#199e70", power: "#c98500" };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// 数值字段一律过 num() 再拼进 innerHTML：场地来数不可信，数值列里若混进字符串
// (如 "<img onerror=...>")，这里变成 null/0，绝不会被当成 HTML 执行。
const num = (v) => AntDataQuality.number(v);
const int0 = (v) => Math.round(num(v) || 0);
const intTxt = (v) => (num(v) == null ? "–" : int0(v).toLocaleString());

// ---- 单位格式化(与本地面板同口径：千进制 TH→PH→EH) ----
function hashUnit(maxTh) {
  if (maxTh >= 1e6) return { div: 1e6, unit: "EH" };
  if (maxTh >= 1e3) return { div: 1e3, unit: "PH" };
  return { div: 1, unit: "TH" };
}
function fmtHash(th, suffix) {
  if (th == null) return "–";
  const u = hashUnit(Math.abs(Number(th)));
  return (Number(th) / u.div).toLocaleString(undefined, { maximumFractionDigits: 2 }) + " " + u.unit + (suffix || "");
}
function fmtPower(kw) {
  const v = num(kw);
  return v == null ? "–" : v.toLocaleString(undefined, { maximumFractionDigits: 3 }) + " kW";
}
function fmtEnergy(kwh) {
  const v = num(kwh);
  return v == null ? "–" : v.toLocaleString(undefined, { maximumFractionDigits: 2 }) + " kWh";
}
function fmtAgo(s) {
  if (s == null) return "—";
  if (s < 90) return t("time.sec_ago", { n: Math.round(s) });
  if (s < 5400) return t("time.min_ago", { n: Math.round(s / 60) });
  if (s < 172800) return t("time.hour_ago", { n: (s / 3600).toFixed(1) });
  return t("time.day_ago", { n: Math.round(s / 86400) });
}
const p2 = (n) => String(n).padStart(2, "0");
function fmtTs(ts, hours) {
  const d = new Date(ts * 1000);
  const hm = p2(d.getHours()) + ":" + p2(d.getMinutes());
  return hours > 48 ? `${d.getMonth() + 1}/${d.getDate()} ${hm}` : hm;
}
const fmtFull = (ts) => { const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${p2(d.getHours())}:${p2(d.getMinutes())}`; };

// ---- 状态标签：图标+文字，不只靠颜色 ----
const STATE = {
  ok:          { cls: "st-ok",          txt: () => t("state.ok") },
  alert:       { cls: "st-alert",       txt: (s) => t("state.alert", { n: int0(s.active_alerts) }) },
  stalled:     { cls: "st-stalled",     txt: () => t("state.stalled") },
  unreachable: { cls: "st-unreachable", txt: () => t("state.unreachable") },
  pending:     { cls: "st-pending",     txt: () => t("state.pending") },
};
const TYPE = {
  air: { cls: "air", txt: () => t("type.air") },
  hydro: { cls: "hydro", txt: () => t("type.hydro") },
  mixed: { cls: "mixed", txt: () => t("type.mixed") },
};

// ---- HTTP ----
async function api(path) {
  const r = await fetch(path);
  if (r.status === 401) { showLogin(); throw new Error("unauth"); }
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
// 写操作统一走这里：带 X-CO-CSRF 头(服务端据此拦跨站伪造请求)
function send(method, path, body) {
  const opt = { method, headers: { "X-CO-CSRF": "1" } };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  return fetch(path, opt);
}
async function errText(r) {
  try { const d = await r.json(); return d.detail || d.error || ("HTTP " + r.status); }
  catch (e) { return "HTTP " + r.status; }
}

// ---- 失联横幅 ----
let _staleOn = false;
function banner(msg) {
  const b = $("staleBanner");
  if (msg) { b.textContent = msg; b.className = "stale-banner lost"; _staleOn = true; }
  else if (_staleOn) { b.className = "stale-banner hidden"; _staleOn = false; }
}

// ---- 登录 ----
function showLogin() { $("loginOverlay").classList.remove("hidden"); $("loginUser").focus(); }
async function doLogin() {
  $("loginErr").textContent = "";
  const r = await send("POST", "/api/login",
    { username: $("loginUser").value.trim(), password: $("loginPass").value });
  if (!r.ok) {
    $("loginErr").textContent = r.status === 429 ? t("login.err_rate")
      : r.status === 403 ? await errText(r) : t("login.err_bad");
    return;
  }
  $("loginPass").value = "";
  const d = await r.json();
  $("loginOverlay").classList.add("hidden");
  setUser(d.user, d.role);
  startLoops();
}
let curRole = null;
let authOn = true;
function setUser(user, role) {
  curRole = role;
  $("userBadge").textContent = user ? `${user} · ${role}` : "";
  $("btnNotifySettings").classList.toggle("hidden", role !== "admin");
  $("btnConnect").classList.toggle("hidden", role !== "admin");
  $("btnDeletedSites").classList.toggle("hidden", role !== "admin");
  $("siteManage").classList.toggle("hidden", role !== "admin");
  $("btnSecurity").classList.toggle("hidden", !authOn);
  if (role === "admin") loadSecurity(false).catch(() => {});
}
$("loginBtn").onclick = doLogin;
$("loginPass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
$("loginUser").addEventListener("keydown", (e) => { if (e.key === "Enter") $("loginPass").focus(); });
$("btnLogout").onclick = async () => { await send("POST", "/api/logout"); location.reload(); };

// ---- 图表 ----
const charts = {};   // canvasId -> Chart

// 断档处理：相邻点间隔明显超过正常节奏时插入 null 断线(spanGaps:false)。
// 否则断报/停电时段会被一条直线连过去，看起来"一切正常"——监控图不允许说谎。
function withGaps(pts) {
  if (pts.length < 3) return pts;
  const dts = [];
  for (let i = 1; i < pts.length; i++) dts.push(pts[i].x - pts[i - 1].x);
  const med = dts.slice().sort((a, b) => a - b)[Math.floor(dts.length / 2)] || 60;
  const gap = Math.max(300, med * 4);
  const out = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    if (pts[i].x - pts[i - 1].x > gap) out.push({ x: pts[i - 1].x + med, y: null });
    out.push(pts[i]);
  }
  return out;
}

function trendSeries(label, color, full, partial = [], legacy = [], div = 1) {
  const data = withGaps(AntDataQuality.points(full, 0, 1, div));
  const datasets = [{ label, borderColor: color, backgroundColor: color, data,
    pointRadius: c => c.raw?.y != null && data[c.dataIndex - 1]?.y == null &&
      data[c.dataIndex + 1]?.y == null ? 2 : 0 }];
  const byTime = new Map(partial.map(p => [num(p.ts), p]));
  const observed = data.map(p => {
    const part = byTime.get(p.x), value = num(part?.value);
    return p.y == null && value != null
      ? { x: p.x, y: value / div, partial: true, coverage: part.coverage }
      : { ...p, partial: false };
  });
  datasets[0].trendHasGap = observed.some(p => p.y == null || p.partial && p.coverage?.continuous !== true);
  if (observed.some(p => p.partial)) datasets.push({
    label: label + " · " + t("trend.partial"), trendPartial: true,
    borderColor: color, backgroundColor: color, borderDash: [5, 4],
    fill: false, tension: 0, data: observed,
    pointRadius: c => c.raw?.partial ? 2 : 0,
    pointHoverRadius: c => c.raw?.partial ? 4 : 0,
    segment: { borderColor: c => {
      const a = observed[c.p0DataIndex], b = observed[c.p1DataIndex];
      if (!a?.partial && !b?.partial) return "transparent";
      // A bucket with missing time is a visible isolated reading, never a
      // line through its unknown periods. Complete neighbors only anchor
      // continuously observed, machine-partial readings.
      if ((a?.partial && a.coverage?.continuous !== true) ||
          (b?.partial && b.coverage?.continuous !== true)) return "transparent";
      return color;
    } },
  });
  if (legacy.some(p => num(p[1]) != null)) datasets.push({
    label: label + " · " + t("quality.legacy"), borderColor: "#8b949e",
    backgroundColor: "#8b949e", borderDash: [5, 4],
    data: withGaps(AntDataQuality.points(legacy, 0, 1, div)),
  });
  return datasets;
}

function trendCoverage(point) {
  const c = point.coverage || {}, notes = [];
  const counts = [c.known_min, c.known_max, c.total_min, c.total_max].map(num);
  if (counts.every(n => n != null && Number.isInteger(n)) &&
      counts[0] <= counts[1] && counts[2] <= counts[3]) {
    const range = (a, b) => a === b ? a : a + "–" + b;
    notes.push(t("quality.counted_machines", {
      known: range(counts[0], counts[1]), total: range(counts[2], counts[3]),
    }));
  }
  notes.push(t("trend.partial_tooltip"));
  if (c.continuous !== true) {
    notes.push(num(c.seconds) > 0 && num(c.window_seconds) > 0
      ? t("trend.time_coverage", { seconds: c.seconds, total: c.window_seconds })
      : t("trend.instant"));
  }
  return notes;
}

function trendNotice(id, datasets) {
  const partial = datasets.filter(d => d.trendPartial);
  const hasPartial = partial.some(d => d.data.some(p => p.partial));
  const hasGap = datasets.some(d => d.trendHasGap);
  const note = $(id);
  note.textContent = [hasPartial ? t("trend.partial_note") : "", hasGap ? t("trend.gap_note") : ""].filter(Boolean).join(" ");
  note.classList.toggle("hidden", !note.textContent);
}
const endLabels = {  // 多序列时在线尾直接标注场地名(文字用墨色，线端色块即身份)
  id: "endLabels",
  afterDatasetsDraw(chart) {
    if (chart.data.datasets.length < 2) return;
    const ctx = chart.ctx;
    ctx.save(); ctx.font = "11px sans-serif"; ctx.textBaseline = "middle"; ctx.fillStyle = INK.label;
    chart.data.datasets.forEach((ds, i) => {
      if (ds.trendPartial) return;
      const meta = chart.getDatasetMeta(i);
      if (meta.hidden || !meta.data.length) return;
      let index = ds.data.length - 1;
      while (index >= 0 && num(ds.data[index].y) == null) index--;
      if (index < 0) return;
      const last = meta.data[index];
      if (!last || !Number.isFinite(last.x) || !Number.isFinite(last.y)) return;
      ctx.fillText(ds.label, Math.min(last.x + 6, chart.chartArea.right + 2), last.y);
    });
    ctx.restore();
  },
};

function mkLine(canvasId, datasets, { hours, yUnit, padRight }) {
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart($(canvasId), {
    type: "line",
    data: { datasets },
    plugins: [endLabels],
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      spanGaps: false,   // null 即断线(配合 withGaps, 断报时段留空不连线)
      layout: { padding: { right: padRight || 8 } },
      interaction: { mode: "nearest", axis: "x", intersect: false },
      elements: { line: { borderWidth: 2, tension: 0.15 }, point: { radius: 0, hoverRadius: 4, hitRadius: 8 } },
      plugins: {
        legend: { display: datasets.length > 1, labels: { color: INK.label, boxWidth: 12, boxHeight: 12 } },
        tooltip: {
          filter: item => !item.dataset.trendPartial || item.raw?.partial === true,
          callbacks: {
            title: (items) => items.length ? fmtFull(items[0].parsed.x) : "",
            label: (item) => ` ${item.dataset.label}: ${item.parsed.y == null ? "—" : Number(item.parsed.y).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${yUnit || ""}`,
            afterLabel: item => item.dataset.trendPartial ? trendCoverage(item.raw) : [],
          },
        },
      },
      scales: {
        x: { type: "linear", grid: { color: INK.grid }, border: { color: INK.axis },
             ticks: { color: INK.tick, maxTicksLimit: 8, callback: (v) => fmtTs(v, hours) } },
        y: { beginAtZero: true, grid: { color: INK.grid }, border: { color: INK.axis },
             ticks: { color: INK.tick },
             title: { display: !!yUnit, text: yUnit || "", color: INK.tick, font: { size: 11 } } },
      },
    },
  });
}

async function loadTrend() {
  const hours = Number($("trendRange").value);
  const d = await api(`/api/trend?hours=${hours}`);
  const maxTh = Math.max(1, ...d.sites.flatMap(s => [
    ...[...s.points, ...(s.legacy_points || [])].map(p => num(p[1]) || 0),
    ...(s.partial_points || []).map(p => num(p.value) || 0),
  ]));
  const u = hashUnit(maxTh);
  const datasets = d.sites.flatMap((s, i) => trendSeries(s.name, SERIES[i % SERIES.length],
    s.points, s.partial_points || [], s.legacy_points || [], u.div));
  trendNotice("trendQuality", datasets);
  mkLine("trendChart", datasets, { hours, yUnit: u.unit + "/s", padRight: 70 });
}
$("trendRange").onchange = () => {
  localStorage.setItem("co_trend_hours", $("trendRange").value);
  loadTrend().catch(() => {});
};
if (localStorage.getItem("co_trend_hours")) $("trendRange").value = localStorage.getItem("co_trend_hours");

// ---- 总览渲染 ----
const pct = (a, b) => (num(a) != null && num(b) > 0 ? ((100 * num(a)) / num(b)).toFixed(1) + "%" : "–");

function siteQuality(s) {
  // Partial power/hashrate has its own short coverage beside the value.
  const complete = s.metric_complete || {}, available = s.metric_available || {};
  if (s.data_quality === "partial" && complete.online && complete.total &&
      ["hashrate_ths", "power_kw"].every(k => complete[k] || available[k])) return "";
  const key = { partial: "quality.partial", stale: "quality.stale", legacy_unverified: "quality.legacy_site", missing: "quality.missing" }[s.data_quality];
  return key ? `<div class="site-warning">${esc(t(key))}</div>` : "";
}

function siteMetric(s, key) {
  const observed = num((s.observed_metrics || {})[key]);
  return (s.metric_available || {})[key] === true && observed != null ? observed : num(s[key]);
}

function siteMetricNote(s, key, countKey) {
  if ((s.metric_available || {})[key] !== true || (s.metric_complete || {})[key] !== false) return "";
  const known = num(s[countKey]), total = num(s.total);
  return AntDataQuality.note(known != null && total != null && known < total
    ? t("quality.counted_machines", { known, total }) : t("quality.partial_readings"), true);
}

function siteCard(s, color) {
  const st = STATE[s.state] || STATE.pending;
  const tp = TYPE[s.type] || TYPE.air;
  const hasData = num(s.total) != null;
  const hashrate = siteMetric(s, "hashrate_ths");
  const cf = int0(s.containers_faulty), co = int0(s.containers_offline);
  const contRow = (s.type === "hydro" || s.type === "mixed" || int0(s.containers) > 0)
    ? `<div><span class="k">${t("card.containers")}</span><span class="num-tab">${intTxt(s.containers)}${cf || co ? ` <span style="color:#ec835a">${t("card.fault_offline", { cf, co })}</span>` : ""}${s.containers_unknown ? ` · ${esc(t("quality.unknown_count", { n: int0(s.containers_unknown) }))}` : ""}</span></div>`
    : "";
  const err = (s.state === "unreachable" || s.state === "stalled") && s.error
    ? `<div class="err">${esc(s.error)}</div>` : "";
  return `<div class="site s-${s.state}" data-site-id="${esc(s.id)}">
    <div class="rowtop">
      <span style="width:10px;height:10px;border-radius:2px;background:${color};display:inline-block"></span>
      <span class="name">${esc(s.name)}</span>
      <span class="pill ${tp.cls}">${tp.txt()}</span>
      <span class="pill ${st.cls}">${st.txt(s)}</span>
    </div>
    <div class="hr">${hashrate != null ? fmtHash(hashrate).replace(/ (\w+)$/, ' <span class="unit">$1/s</span>') : "–"}</div>
    ${siteQuality(s)}
    ${siteMetricNote(s, "hashrate_ths", "hashrate_known_machines")}
    <div class="stats">
      <div><span class="k">${t("card.online")}</span><span class="num-tab">${hasData ? `${intTxt(s.online)}/${intTxt(s.total)} (${s.unknown ? "–" : pct(s.online, s.total)})` : "–"}</span></div>
      <div><span class="k">${t("card.power")}</span><span class="num-tab">${fmtPower(siteMetric(s, "power_kw"))}${siteMetricNote(s, "power_kw", "power_known_machines")}</span></div>
      <div><span class="k">${t("card.alerts")}</span><span class="num-tab">${intTxt(s.active_alerts)}</span></div>
      <div><span class="k">${t("card.customers")}</span><span class="num-tab">${int0(s.customers)}</span></div>
      ${contRow}
    </div>
    ${err}
    ${s.duplicate_name ? `<div class="site-warning">${esc(t("site.duplicate"))} · ${esc(s.id)}</div>` : ""}
    <div class="foot"><span>${t("card.updated", { ago: fmtAgo(s.data_age_s) })}</span><span>${t("card.click_trend")}</span></div>
  </div>`;
}

function renderOverview(o) {
  const totals = o.totals;
  const online = AntDataQuality.totalValue(o, "machines_online");
  const machines = AntDataQuality.totalValue(o, "machines_total");
  const machinesNote = AntDataQuality.totalNote(o, "machines_total", t) || AntDataQuality.totalNote(o, "machines_online", t);
  $("kSites").textContent = `${totals.sites_ok}/${totals.sites}`;
  $("kMachines").textContent = `${intTxt(online)}/${intTxt(machines)}`;
  $("kMachinesNote").textContent = machinesNote;
  $("kOnlinePct").textContent = pct(online, machines);
  $("kOnlinePctNote").textContent = machinesNote;
  $("kHashrate").innerHTML = AntDataQuality.total(o, "hashrate_ths", t, n => fmtHash(n, "/s"));
  $("kPower").innerHTML = AntDataQuality.total(o, "power_kw", t, fmtPower);
  $("kAlerts").innerHTML = AntDataQuality.total(o, "active_alerts", t, intTxt);
  $("kAlerts").className = "num" + (AntDataQuality.totalValue(o, "active_alerts") > 0 ? " hot" : "");
  $("kContainers").innerHTML = `${intTxt(totals.containers)} (${intTxt(totals.containers_faulty)}/${intTxt(totals.containers_offline)})`
    + (totals.containers_unknown ? AntDataQuality.note(t("quality.unknown_count", { n: int0(totals.containers_unknown) }), true) : "");
  $("kContainers").className = "num" + (totals.containers_faulty + totals.containers_offline > 0 ? " hot" : "");
  $("kCustomers").textContent = totals.customers;
  $("kCustomers").title = t("quality.customer_identity");
  const affected = o.sites.filter(s => siteQuality(s)).map(s => s.name);
  $("overviewQuality").textContent = affected.length ? t("quality.overview_help", { sites: affected.join(", ") }) : "";
  $("overviewQuality").classList.toggle("hidden", !affected.length);

  const groups = { air: [], hydro: [], mixed: [] };
  o.sites.forEach((s, i) => {
    const html = siteCard(s, SERIES[i % SERIES.length]);
    (groups[s.type] || groups.air).push(html);
  });
  $("grpAir").classList.toggle("hidden", !groups.air.length);
  $("grpHydro").classList.toggle("hidden", !groups.hydro.length);
  $("grpMixed").classList.toggle("hidden", !groups.mixed.length);
  $("cntAir").textContent = groups.air.length;
  $("cntHydro").textContent = groups.hydro.length;
  $("cntMixed").textContent = groups.mixed.length;
  $("gridAir").innerHTML = groups.air.join("");
  $("gridHydro").innerHTML = groups.hydro.join("");
  $("gridMixed").innerHTML = groups.mixed.join("");
  $("lastUpdate").textContent = t("overview.updated", { time: new Date(o.ts * 1000).toLocaleTimeString() });
}

function renderRateNotice(id, rows) {
  const names = [...new Set((rows || [])
    .filter(r => r.hashrate_unavailable_reason === "not_reported" || r.avg_unavailable_reason === "not_reported")
    .map(r => r.site_name || r.site_id).filter(Boolean))];
  const el = $(id);
  el.textContent = names.length ? t("rate.missing_sites_help", { sites: names.join(", ") }) : "";
  el.classList.toggle("hidden", !names.length);
}

function renderCustomers(d) {
  renderRateNotice("customerRateNotice", d.rows);
  $("workerAverageHead").textContent = AntCustomerRates.header(d.by_worker, t);
  $("customerAverageHead").textContent = AntCustomerRates.header(d.rows, t);
  $("tblWorkers").querySelector("tbody").innerHTML = d.by_worker.map((w) => `<tr>
    <td>${esc(w.worker)}</td><td>${int0(w.sites)}</td><td>${AntCustomerRates.machines(w, t)}</td>
    <td>${AntCustomerRates.cell(w, "current", t, fmtHash, fmtAgo)}</td>
    <td>${AntCustomerRates.cell(w, "average", t, fmtHash, fmtAgo)}</td>
    <td>${AntDataQuality.billing(w, "power", t, fmtEnergy)}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">${t("empty.customers")}</td></tr>`;
  $("tblCustomers").querySelector("tbody").innerHTML = d.rows.map((r) => `<tr>
    <td>${esc(r.site_name)}</td><td>${esc(r.worker)}</td><td>${AntCustomerRates.machines(r, t)}</td>
    <td>${AntDataQuality.billing(r, "uptime", t, n => n.toFixed(1) + "%")}</td>
    <td>${AntCustomerRates.cell(r, "current", t, fmtHash, fmtAgo)}</td>
    <td>${AntCustomerRates.cell(r, "average", t, fmtHash, fmtAgo)}</td>
    <td>${AntDataQuality.billing(r, "power", t, fmtEnergy)}</td></tr>`).join("")
    || `<tr><td colspan="7" class="muted">${t("empty.none")}</td></tr>`;
  if (curSite && !$("siteModal").classList.contains("hidden"))
    renderSiteCustomers(d.rows.filter(r => r.site_id === curSite));
}

function renderSiteCustomers(rows) {
  renderRateNotice("siteCustomerRateNotice", rows);
  $("siteAverageHead").textContent = AntCustomerRates.header(rows, t);
  $("tblSiteCustomers").querySelector("tbody").innerHTML = rows.map((r) => `<tr>
    <td>${esc(r.worker)}</td><td>${AntCustomerRates.machines(r, t)}</td>
    <td>${AntDataQuality.billing(r, "uptime", t, n => n.toFixed(1) + "%")}</td>
    <td>${AntCustomerRates.cell(r, "current", t, fmtHash, fmtAgo)}</td>
    <td>${AntCustomerRates.cell(r, "average", t, fmtHash, fmtAgo)}</td>
    <td>${AntDataQuality.billing(r, "power", t, fmtEnergy)}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">${t("empty.none")}</td></tr>`;
}

async function refresh() {
  try {
    const o = await api("/api/overview");
    banner(null);
    renderOverview(o);
    renderCustomers(await api("/api/customers"));
    await loadTrend();
  } catch (e) {
    if (e.message !== "unauth") banner(t("banner.lost"));
  }
}

// ---- 场地详情(按稳定 site_id 寻址，界面只显示名字) ----
let curSite = null;
let curSiteData = null;
function openSite(id) {
  curSite = id; curSiteData = null;
  $("siteManageMsg").textContent = "";
  $("siteName").value = "";
  setSiteBusy(true);
  $("siteModal").classList.remove("hidden");
  loadSite().catch(() => { $("siteManageMsg").textContent = t("site.load_error"); });
}
function setSiteBusy(busy) {
  $("siteManage").querySelectorAll("button, input").forEach((el) => { el.disabled = busy; });
}

async function saveSiteName(name) {
  if (curRole !== "admin" || !curSiteData) return;
  const id = curSiteData.id;
  setSiteBusy(true);
  $("siteManageMsg").textContent = "";
  try {
    const r = await send("PATCH", `/api/site/${encodeURIComponent(id)}`, { name });
    if (!r.ok) throw new Error(await errText(r));
    await refresh();
    if (curSite === id) await loadSite();
  } catch (e) { $("siteManageMsg").textContent = e.message; }
  finally { setSiteBusy(false); }
}
$("siteManage").onsubmit = (e) => { e.preventDefault(); saveSiteName($("siteName").value.trim()); };
$("siteFollowName").onclick = () => saveSiteName("");
$("siteDelete").onclick = async () => {
  if (curRole !== "admin" || !curSiteData || curSiteData.mode !== "push") return;
  const s = curSiteData;
  if (!confirm(t("site.delete_confirm", { name: s.name, id: s.id, ago: fmtAgo(s.data_age_s) }))) return;
  setSiteBusy(true);
  try {
    const r = await send("DELETE", `/api/site/${encodeURIComponent(s.id)}`);
    if (!r.ok) throw new Error(await errText(r));
    removeConnSite(s.id);
    if (curSite === s.id) { $("siteModal").classList.add("hidden"); curSite = null; curSiteData = null; }
    await refresh();
  } catch (e) { $("siteManageMsg").textContent = e.message; }
  finally { setSiteBusy(false); }
};

async function loadDeletedSites() {
  const d = await api("/api/sites/deleted");
  $("deletedSitesList").innerHTML = d.sites.map((s) => `<div class="deleted-site">
    <div class="identity">${esc(s.name)}<div class="muted">${esc(s.id)} · ${fmtFull(s.deleted_ts)}</div></div>
    <button data-restore-site="${esc(s.id)}">${esc(t("site.restore"))}</button></div>`).join("")
    || `<p class="muted">${esc(t("site.deleted_empty"))}</p>`;
}
$("btnDeletedSites").onclick = async () => {
  $("deletedSitesMsg").textContent = "";
  $("deletedSitesModal").classList.remove("hidden");
  try { await loadDeletedSites(); } catch (e) { $("deletedSitesMsg").textContent = e.message; }
};
$("deletedSitesClose").onclick = () => $("deletedSitesModal").classList.add("hidden");
$("deletedSitesModal").addEventListener("click", (e) => {
  if (e.target === $("deletedSitesModal")) $("deletedSitesModal").classList.add("hidden");
});
$("deletedSitesList").addEventListener("click", async (e) => {
  const button = e.target.closest("[data-restore-site]");
  if (!button || curRole !== "admin") return;
  const id = button.dataset.restoreSite;
  let restored = false;
  button.disabled = true;
  $("deletedSitesMsg").textContent = "";
  try {
    const r = await send("POST", `/api/site/${encodeURIComponent(id)}/restore`);
    if (!r.ok) throw new Error(await errText(r));
    restored = true;
    _connListVersion++;
    _connDeletedSites.delete(id);
    invalidateConnCredentials(id);
    await loadDeletedSites();
    await refresh();
    $("deletedSitesModal").classList.add("hidden");
    const loaded = await openConnect();
    connMsg(t(loaded ? "site.restored" : "site.restored_refresh_failed"), !loaded);
  } catch (err) {
    $("deletedSitesMsg").textContent = restored ? t("site.restored_refresh_failed") : err.message;
    if (!restored) button.disabled = false;
  }
});
// 事件委托：卡片每30s重建DOM，绑在document上点击永远有效
document.addEventListener("click", (e) => {
  const card = e.target.closest(".site[data-site-id]");
  if (card) openSite(card.dataset.siteId);
});
$("siteClose").onclick = () => $("siteModal").classList.add("hidden");
$("siteModal").addEventListener("click", (e) => { if (e.target === $("siteModal")) $("siteModal").classList.add("hidden"); });
$("siteRange").onchange = () => loadSite().catch(() => {});

function fmtDuration(s) {
  if (s == null || s < 0) return "—";
  if (s < 90) return t("time.sec", { n: Math.round(s) });
  if (s < 5400) return t("time.min", { n: Math.round(s / 60) });
  if (s < 172800) return t("time.hour", { n: (s / 3600).toFixed(1) });
  return t("time.day", { n: Math.round(s / 86400) });
}

// 活跃告警摘要(不含单机IP，只标类型/台数/受影响客户/持续时长——场地本地未推送该字段时为空数组)
function renderSiteAlerts(groups) {
  const el = $("siteAlerts");
  if (!groups || !groups.length) {
    el.innerHTML = `<div class="muted">${t("alert.none")}</div>`;
    return;
  }
  const now = Date.now() / 1000;
  el.innerHTML = groups.map((g) => {
    const ws = g.workers || [];
    const shown = ws.slice(0, 6).map((w) => esc(w.worker) + (int0(w.count) > 1 ? `×${int0(w.count)}` : "")).join("、");
    const more = ws.length > 6 ? t("alert.more_cust", { n: ws.length }) : "";
    const since = num(g.since_ts) ? t("alert.since", { dur: fmtDuration(now - num(g.since_ts)) }) : "";
    return `<div class="alertrow">${t("alert.row", { label: esc(g.label), n: int0(g.count) })}` +
      (shown ? ` · ${shown}${more}` : "") + since + `</div>`;
  }).join("");
}

async function loadSite() {
  const id = curSite;
  if (!id) return;
  const hours = Number($("siteRange").value);
  const d = await api(`/api/site/${encodeURIComponent(id)}?hours=${hours}`);
  if (curSite !== id) return;
  const s = d.site, st = STATE[s.state] || STATE.pending, tp = TYPE[s.type] || TYPE.air;
  curSiteData = s;
  $("siteName").value = s.name;
  $("siteIdentity").textContent = t("site.identity", { id: s.id, name: s.reported_name });
  $("siteDuplicate").classList.toggle("hidden", !s.duplicate_name);
  $("siteManage").classList.toggle("hidden", curRole !== "admin");
  setSiteBusy(false);
  $("siteDelete").disabled = s.mode !== "push";
  $("siteDelete").title = s.mode === "pull" ? t("site.pull_delete") : "";
  $("siteTitle").innerHTML = `${esc(s.name)} <span class="pill ${tp.cls}">${tp.txt()}</span> <span class="pill ${st.cls}">${st.txt(s)}</span>`;
  $("siteMeta").innerHTML =
    t("site.meta", { data: fmtAgo(s.data_age_s), scan: fmtAgo(s.scan_age_s) }) +
    (int0(s.fails) ? t("site.fails", { n: int0(s.fails) }) : "") +
    (s.error ? `<br><span style="color:#f0a0a0">${esc(s.error)}</span>` : "") + siteQuality(s);
  renderSiteAlerts(d.alert_summary);

  const tr = d.trend;
  const maxTh = Math.max(1, ...tr.flatMap(p => [num(p.hashrate_ths) || 0,
    num(p.partial_hashrate_ths) || 0, num(p.legacy_hashrate_ths) || 0]));
  const u = hashUnit(maxTh);
  const series = (key, label, color, div = 1) => trendSeries(t(label), color,
    tr.map(p => [p.ts, p[key]]),
    tr.filter(p => num(p["partial_" + key]) != null).map(p => ({ ts: p.ts,
      value: p["partial_" + key], coverage: p[(key === "hashrate_ths" ? "hashrate" : "power") + "_coverage"] })),
    tr.map(p => [p.ts, p["legacy_" + key]]), div);
  const hashrateSeries = series("hashrate_ths", "chart.hashrate", METRIC.hashrate, u.div);
  const powerSeries = series("power_kw", "chart.power", METRIC.power);
  trendNotice("siteTrendQuality", [...hashrateSeries, ...powerSeries]);
  mkLine("chHr", hashrateSeries, { hours, yUnit: u.unit + "/s" });
  mkLine("chOnline", series("online", "chart.online", METRIC.online), { hours, yUnit: t("chart.unit_machines") });
  mkLine("chPower", powerSeries, { hours, yUnit: "kW" });

  renderSiteCustomers(d.customers);

  loadDaily(d.site.id).catch(() => {});
}

async function loadDaily(id) {
  const d = await api(`/api/daily?site=${encodeURIComponent(id)}&days=14`);
  if (curSite !== id) return;
  $("tblDaily").querySelector("tbody").innerHTML = d.days.map((r) => `<tr>
    <td>${esc(r.day)}</td><td>${AntDataQuality.daily(r, "avg_hashrate_ths", "hashrate_ths", t, n => fmtHash(n, "/s"))}</td>
    <td>${AntDataQuality.daily(r, "max_hashrate_ths", "hashrate_ths", t, n => fmtHash(n, "/s"))}</td>
    <td>${AntDataQuality.daily(r, "avg_online", "online", t, n => n.toLocaleString(undefined, { maximumFractionDigits: 1 }))}</td>
    <td>${AntDataQuality.daily({ ...r, online_pct: num(r.avg_online) != null && num(r.avg_total) > 0 ? r.avg_online / r.avg_total * 100 : null,
      legacy_online_pct: num(r.legacy_avg_online) != null && num(r.legacy_avg_total) > 0 ? r.legacy_avg_online / r.legacy_avg_total * 100 : null }, "online_pct", "online", t, n => n.toFixed(1) + "%")}</td>
    <td>${AntDataQuality.daily(r, "power_kwh", "power_kw", t, fmtEnergy)}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">${t("empty.daily")}</td></tr>`;
}

// ---- 版本更新(仅 admin；git 部署时可用) ----
async function checkUpdate() {
  if (curRole !== "admin") return;
  try {
    const st = await api("/api/update/check");
    const btn = $("btnUpdate");
    if (st.behind > 0) {
      btn.textContent = t("update.btn", { n: st.behind });
      btn.title = t("update.title", { changes: (st.changes || []).join("\n") });
      btn.classList.remove("hidden");
    } else {
      btn.classList.add("hidden");
    }
  } catch (e) { /* 非 git 部署/网络问题都静默 */ }
}
$("btnUpdate").onclick = async () => {
  if (!confirm(t("update.confirm"))) return;
  try {
    const r = await (await send("POST", "/api/update/apply")).json();
    if (!r.ok) { alert(t("update.fail", { msg: r.msg || "" })); return; }
    banner(t("update.progress"));
    const timer = setInterval(async () => {   // 等服务回来自动刷新
      try {
        const h = await (await fetch("/api/health")).json();
        if (h.ok) { clearInterval(timer); location.reload(); }
      } catch (e) { /* 还没起来 */ }
    }, 3000);
  } catch (e) { alert(t("update.req_fail", { msg: e.message })); }
};

// ---- 通知设置(仅 admin：Telegram/Webhook/邮件，网页改即时生效，见 GET/POST /api/settings) ----
function fillNotifyForm(a) {
  $("nfEnabled").checked = !!a.enabled;
  $("nfCooldown").value = a.cooldown;
  $("nfDropPct").value = a.hashrate_drop_pct;
  $("nfOfflineThreshold").value = a.offline_threshold;

  $("tgEnabled").checked = !!a.telegram.enabled;
  $("tgToken").value = "";
  $("tgToken").placeholder = a.telegram.bot_token_set ? t("ph.set_keep") : "";
  $("tgChatId").value = a.telegram.chat_id || "";

  $("whEnabled").checked = !!a.webhook.enabled;
  $("whUrl").value = "";
  $("whUrl").placeholder = a.webhook.url_set ? t("ph.set_keep") : "";
  $("whKind").value = a.webhook.kind || "wecom";

  $("emEnabled").checked = !!a.email.enabled;
  $("emHost").value = a.email.smtp_host || "";
  $("emPort").value = a.email.smtp_port || 465;
  $("emSsl").checked = !!a.email.use_ssl;
  $("emStarttls").checked = !!a.email.use_starttls;
  $("emUser").value = a.email.username || "";
  $("emPass").value = "";
  $("emPass").placeholder = a.email.password_set ? t("ph.set_keep") : "";
  $("emFrom").value = a.email.from_addr || "";
  $("emTo").value = (a.email.to || []).join(", ");
  $("emSubject").value = a.email.subject_prefix || "";

  // 重置测试结果标记(打开/保存后旧结果失效)
  ["bgTelegram", "bgWebhook", "bgEmail"].forEach((id) => { $(id).textContent = ""; $(id).className = "chbadge"; });
}

async function openNotify() {
  $("notifyMsg").textContent = "";
  $("notifyModal").classList.remove("hidden");
  try {
    const d = await api("/api/settings");
    fillNotifyForm(d.alerts);
  } catch (e) {
    $("notifyMsg").textContent = t("notify.load_fail", { msg: e.message });
  }
}
$("btnNotifySettings").onclick = openNotify;
$("notifyClose").onclick = () => $("notifyModal").classList.add("hidden");
$("notifyModal").addEventListener("click", (e) => { if (e.target === $("notifyModal")) $("notifyModal").classList.add("hidden"); });

function buildNotifyPayload() {
  return { alerts: {
    enabled: $("nfEnabled").checked,
    cooldown: Number($("nfCooldown").value) || 0,
    hashrate_drop_pct: Number($("nfDropPct").value) || 0,
    offline_threshold: Number($("nfOfflineThreshold").value) || 0,
    telegram: { enabled: $("tgEnabled").checked, bot_token: $("tgToken").value, chat_id: $("tgChatId").value },
    webhook: { enabled: $("whEnabled").checked, url: $("whUrl").value, kind: $("whKind").value },
    email: {
      enabled: $("emEnabled").checked, smtp_host: $("emHost").value, smtp_port: Number($("emPort").value) || 465,
      use_ssl: $("emSsl").checked, use_starttls: $("emStarttls").checked, username: $("emUser").value,
      password: $("emPass").value, from_addr: $("emFrom").value, to: $("emTo").value,
      subject_prefix: $("emSubject").value,
    },
  } };
}

$("nfSave").onclick = async () => {
  $("notifyMsg").textContent = t("notify.saving");
  try {
    const r = await send("POST", "/api/settings", buildNotifyPayload());
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || d.detail || ("HTTP " + r.status));
    fillNotifyForm(d.alerts);
    $("notifyMsg").textContent = t("notify.saved");
  } catch (e) {
    $("notifyMsg").textContent = t("notify.save_fail", { msg: e.message });
  }
};

$("nfTest").onclick = async () => {
  $("notifyMsg").textContent = t("notify.testing");
  try {
    const r = await send("POST", "/api/notify/test");
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || d.detail || ("HTTP " + r.status));
    const badge = { telegram: "bgTelegram", webhook: "bgWebhook", email: "bgEmail" };
    Object.entries(d.result).forEach(([ch, v]) => {
      const el = $(badge[ch]);
      if (v === true) { el.textContent = t("notify.ok"); el.className = "chbadge ok"; }
      else if (v === false) { el.textContent = t("notify.fail"); el.className = "chbadge fail"; }
      else { el.textContent = t("notify.off"); el.className = "chbadge off"; }
    });
    $("notifyMsg").textContent = t("notify.test_done");
  } catch (e) {
    $("notifyMsg").textContent = t("notify.test_fail", { msg: e.message });
  }
};

// ---- 账号安全(改密码：所有人；安全自检/会话/审计：仅 admin) ----
const AUDIT_ACTION = {
  login: () => t("audit.login"), login_fail: () => t("audit.login_fail"), logout: () => t("audit.logout"),
  password_change: () => t("audit.password_change"), password_fail: () => t("audit.password_fail"),
  settings_save: () => t("audit.settings_save"), notify_test: () => t("audit.notify_test"),
  update_apply: () => t("audit.update_apply"), site_delete: () => t("audit.site_delete"),
  site_rename: () => t("audit.site_rename"), site_restore: () => t("audit.site_restore"),
  sessions_revoke: () => t("audit.sessions_revoke"),
  token_reveal: () => t("audit.token_reveal"), token_rotate: () => t("audit.token_rotate"),
  token_revoke: () => t("audit.token_revoke"), token_new_site: () => t("audit.token_new_site"),
  connect_settings: () => t("audit.connect_settings"), connect_pw_fail: () => t("audit.connect_pw_fail"),
};
const LEVEL_TXT = { high: () => t("sec.level_high"), warn: () => t("sec.level_warn"), info: () => t("sec.level_info") };

function secBanner(checks) {
  const high = checks.filter((c) => c.level === "high").length;
  const b = $("secBanner");
  b.textContent = high ? t("sec.banner", { n: high }) : "";
  b.classList.toggle("hidden", !high);
}

async function loadSecurity(render) {
  if (curRole !== "admin") return;
  const d = await api("/api/security");
  secBanner(d.checks);
  if (!render) return;
  $("secChecks").innerHTML = d.checks.length
    ? d.checks.map((c) => `<div class="seccheck ${esc(c.level)}">${(LEVEL_TXT[c.level] ? LEVEL_TXT[c.level]() : "")} ${esc(c.msg)}</div>`).join("")
    : `<div class="seccheck ok">${t("sec.ok")}</div>`;
  $("secSessions").textContent = t("sec.sessions", { n: int0(d.sessions) });
  $("tblAudit").querySelector("tbody").innerHTML = d.audit.map((a) => `<tr>
    <td>${fmtFull(int0(a.ts))}</td><td>${esc(a.user)}</td><td>${esc(a.ip)}</td>
    <td>${esc(AUDIT_ACTION[a.action] ? AUDIT_ACTION[a.action]() : a.action)}</td><td>${esc(a.detail)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">${t("empty.none")}</td></tr>`;
}

function openSecurity() {
  $("pwMsg").textContent = "";
  ["pwOld", "pwNew", "pwNew2"].forEach((id) => { $(id).value = ""; });
  $("secAdmin").classList.toggle("hidden", curRole !== "admin");
  $("secModal").classList.remove("hidden");
  loadSecurity(true).catch((e) => { $("secChecks").textContent = t("notify.load_fail", { msg: e.message }); });
}
$("btnSecurity").onclick = openSecurity;
$("secBanner").onclick = openSecurity;
$("secClose").onclick = () => $("secModal").classList.add("hidden");
$("secModal").addEventListener("click", (e) => { if (e.target === $("secModal")) $("secModal").classList.add("hidden"); });

$("pwSave").onclick = async () => {
  const oldPw = $("pwOld").value, newPw = $("pwNew").value;
  if (newPw !== $("pwNew2").value) { $("pwMsg").textContent = t("pw.mismatch"); return; }
  $("pwMsg").textContent = t("pw.submitting");
  const r = await send("POST", "/api/password", { old: oldPw, new: newPw });
  if (!r.ok) { $("pwMsg").textContent = "✗ " + await errText(r); return; }
  ["pwOld", "pwNew", "pwNew2"].forEach((id) => { $(id).value = ""; });
  $("pwMsg").textContent = t("pw.ok");
  loadSecurity(true).catch(() => {});
};

$("secRevoke").onclick = async () => {
  if (!confirm(t("pw.revoke_confirm"))) return;
  const r = await send("POST", "/api/sessions/revoke");
  if (!r.ok) { alert(t("pw.revoke_fail", { msg: await errText(r) })); return; }
  const d = await r.json();
  alert(t("pw.revoke_ok", { n: int0(d.revoked) }));
  loadSecurity(true).catch(() => {});
};

// ---- 场地接入：云端地址 / 证书指纹 / 上报 token / 接入码(仅 admin) ----
let _connHideTimer = null;
let _connSavedUrl = null;
let _connAddressError = "";
let _connSites = new Map();
const _connDeletedSites = new Set();
const _connDeleting = new Set();
const _connCredentialVersions = new Map();
const _connActionBusy = new Map();
let _connListVersion = 0;
let _connLoadSequence = 0;
function connMsg(text, bad) {
  const el = $("connMsg");
  el.textContent = text || "";
  el.style.color = bad ? "var(--theme-danger, #ff9b9b)" : "";
}
async function copyText(text, manualField, successKey = "conn.copied", current = () => true) {
  if (!text) { connMsg(t("conn.copy_empty"), true); return false; }
  try {
    await navigator.clipboard.writeText(text);
    if (!current()) return false;
    connMsg(t(successKey)); return true;
  } catch (e) {}
  if (!current()) return false;
  const ta = document.createElement("textarea");   // 非安全上下文/老浏览器兜底
  ta.value = text; document.body.appendChild(ta); ta.select();
  let copied = false;
  try { copied = document.execCommand("copy") === true; } catch (e) {}
  ta.remove();
  if (copied) { connMsg(t(successKey)); return true; }
  if (manualField) {
    if (manualField === $("connCode")) $("connDetails").open = true;
    manualField.focus();
    if (manualField.select) manualField.select();
  }
  connMsg(t("conn.copy_manual"), true);
  return false;
}
function readConnPacket(value) {
  const raw = String(value || "").trim();
  if (raw.length > 16384) return null;
  const match = raw.match(/^AMC1-([A-Za-z0-9_-]+)$/);
  if (!match) return null;
  try {
    const encoded = match[1].replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(encoded + "=".repeat((4 - encoded.length % 4) % 4));
    const d = JSON.parse(new TextDecoder("utf-8", { fatal: true })
      .decode(Uint8Array.from(binary, c => c.charCodeAt(0))));
    if (!d || Array.isArray(d) || typeof d.u !== "string" || !d.u.trim()
        || typeof d.t !== "string" || !d.t.trim()) return null;
    if (/[\s\x00-\x1f\x7f]/.test(d.u.trim()) || /[\x00-\x1f\x7f]/.test(d.t.trim())
        || !/^https?:\/\//i.test(d.u.trim())) return null;
    const url = new URL(d.u.trim());
    if (!url.hostname || url.username || url.password || url.search || url.hash) return null;
    for (const key of ["s", "n", "y", "f"]) {
      if (d[key] != null && typeof d[key] !== "string") return null;
    }
    if (d.y && !["air", "hydro", "mixed"].includes(d.y)) return null;
    for (const key of ["s", "n"]) {
      if (d[key] && ([...d[key]].length > 64 || /[\x00-\x1f\x7f]/.test(d[key]))) return null;
    }
    return { ...d, u: d.u.trim(), t: d.t.trim() };
  } catch (e) { return null; }
}
async function copyConnInfo() {
  if (!connAddressReady()) return false;
  const code = $("connCode").value, packet = readConnPacket(code);
  if (!packet || _connDeletedSites.has(packet.s) || _connDeleting.has(packet.s)
      || connUrlValue(packet.u) !== connUrlValue(_connSavedUrl)) {
    hideConnResult(); connMsg(t("conn.invalid_code"), true); return false;
  }
  return copyText(code, $("connCode"), "conn.copied_info", () => $("connCode").value === code
    && !_connDeletedSites.has(packet.s) && !_connDeleting.has(packet.s));
}
function hideConnResult() {
  $("connResult").classList.add("hidden");
  $("connCode").value = ""; $("connTok").value = "";
  $("connResultUrl").textContent = "";
  $("connResultSiteId").textContent = "";
  $("connDetails").open = false;
  $("connRawToken").open = false;
  if (_connHideTimer) { clearTimeout(_connHideTimer); _connHideTimer = null; }
}
function showConnResult(label, d) {
  // Preview the actual packet that will be pasted locally, never a separate
  // metadata response or a raw token that cannot identify the destination.
  const packet = readConnPacket(d.code);
  if (!packet || connUrlValue(packet.u) !== connUrlValue(_connSavedUrl)) {
    hideConnResult(); connMsg(t("conn.invalid_code"), true); return false;
  }
  const kind = packet.y ? t("type." + packet.y) : "";
  $("connResultFor").textContent = [packet.n, kind, label].filter(Boolean).join(" · ");
  $("connCode").value = d.code;
  $("connTok").value = packet.t;
  $("connResultUrl").textContent = packet.u;
  $("connResultSiteId").textContent = packet.s || "";
  $("connResultSiteRow").classList.toggle("hidden", !packet.s);
  $("connIncludes").textContent = t(packet.s && packet.n && packet.y ? "conn.includes" : "conn.includes_basic");
  $("connResult").classList.remove("hidden");
  renderConnInstall();
  if (_connHideTimer) clearTimeout(_connHideTimer);
  _connHideTimer = setTimeout(hideConnResult, 120000);
  return true;
}
const MODE_TXT = {
  domain: ["ok", "conn.mode_domain"], ip: ["ok", "conn.mode_ip"], self_signed: ["ok", "conn.mode_selfsigned"],
  http: ["fail", "conn.mode_http"], unset: ["off", "conn.mode_unset"],
};
function renderConnInstall() {
  const windows = $("connInstallOs").value === "windows";
  const roots = {
    Github: "https://raw.githubusercontent.com/raphael2025/antmonitor/main",
    Gitee: "https://gitee.com/raphael01/antmonitor/raw/main",
  };
  for (const [label, root] of Object.entries(roots)) {
    const source = label.toLowerCase();
    // 这里只生成公开安装命令，长期凭据始终由接入码单独交接。
    $("connInstall" + label).textContent = windows
      ? `& ([scriptblock]::Create((irm '${root}/install.ps1'))) -Source ${source}`
      : `curl -fsSL ${root}/install.sh | sudo bash -s -- --source ${source} --connect-code -`;
  }
  $("connInstallHelp").textContent = t(windows ? "conn.install_windows" : "conn.install_linux");
}
function connUrlValue(value) { return String(value || "").trim().replace(/\/+$/, ""); }
function invalidateConnCredentials(id) {
  _connCredentialVersions.set(id, (_connCredentialVersions.get(id) || 0) + 1);
  const packet = readConnPacket($("connCode").value);
  if (id === "shared" || (packet && packet.s === id)) hideConnResult();
}
function connActionCurrent(id, version) {
  return !_connDeleting.has(id) && !_connDeletedSites.has(id)
    && (_connCredentialVersions.get(id) || 0) === version;
}
function removeConnSite(id) {
  _connListVersion++;
  _connDeletedSites.add(id);
  invalidateConnCredentials(id);
  _connActionBusy.delete(id);
  _connSites.delete(id);
  renderConnSites([..._connSites.values()]);
}
async function deleteConnSite(id) {
  const site = _connSites.get(id);
  if (curRole !== "admin" || !site || site.mode === "pull" || _connDeleting.has(id)) return;
  if (!confirm(t("conn.delete_confirm", { name: site.name || id, id }))) return;
  let deleted = false;
  let responded = false;
  _connDeleting.add(id);
  renderConnSites([..._connSites.values()]);
  connMsg("");
  try {
    const r = await send("DELETE", `/api/site/${encodeURIComponent(id)}`);
    responded = true;
    if (!r.ok) {
      if (r.status >= 500) invalidateConnCredentials(id);
      throw new Error(await errText(r));
    }
    deleted = true;
    removeConnSite(id);
    if (curSite === id) { $("siteModal").classList.add("hidden"); curSite = null; curSiteData = null; }
    await loadConnect();
    await refresh();
    connMsg(t("conn.deleted"));
  } catch (e) {
    if (!responded) invalidateConnCredentials(id); // The server may have completed a request whose response was lost.
    connMsg(deleted ? t("conn.deleted_refresh_failed") : e.message, true);
  } finally {
    _connDeleting.delete(id);
    renderConnSites([..._connSites.values()]);
  }
}
function connAddressReady() {
  const unchanged = connUrlValue($("connUrl").value) === connUrlValue(_connSavedUrl);
  if (_connSavedUrl !== null && _connSavedUrl && unchanged && !_connAddressError) return true;
  hideConnResult();
  connMsg(unchanged && _connAddressError ? _connAddressError
    : t(_connSavedUrl === null ? "conn.not_loaded" : "conn.url_unsaved"), true);
  $("connAdvanced").open = true;
  $("connUrl").focus();
  return false;
}
function renderConnect(d, savedAddress = false, listVersion = _connListVersion) {
  const dirty = _connSavedUrl !== null && connUrlValue($("connUrl").value) !== connUrlValue(_connSavedUrl);
  _connSavedUrl = d.public_url || "";
  _connAddressError = d.address_ready === false ? (d.address_error || t("conn.url_unsaved")) : "";
  if (!dirty || savedAddress) $("connUrl").value = _connSavedUrl;
  $("connUrlCurrent").textContent = _connSavedUrl || t("conn.mode_unset");
  const m = MODE_TXT[d.mode] || MODE_TXT.unset;
  $("connMode").className = "chbadge " + m[0];
  $("connMode").textContent = t(m[1]);
  $("connFpRow").classList.toggle("hidden", !d.fingerprint);
  $("connFpHelp").classList.toggle("hidden", !d.fingerprint);
  $("connFp").textContent = d.fingerprint || "";
  $("connWarn").textContent = (d.warnings || []).join("\n");
  $("connShared").textContent = d.shared.set
    ? t("conn.shared_set", { hint: d.shared.hint || "****" }) + (d.shared.from_config ? " · config.yaml" : "")
    : t("conn.shared_unset");
  $("connShared").dataset.set = d.shared.set ? "1" : "";
  $("connSharedShow").disabled = !d.shared.set;
  $("connSharedRevoke").disabled = !d.shared.set;
  $("connSharedRotate").textContent = d.shared.set ? t("conn.rotate") : t("conn.generate");
  $("connAutoReg").checked = !!d.auto_register;
  if (listVersion === _connListVersion) renderConnSites(d.sites || []);
}
function renderConnSites(sites) {
  // A response started before deletion must not put its stale row back.
  _connSites = new Map(sites.filter(x => !_connDeletedSites.has(x.id)).map(x => [x.id, x]));
  $("tblConnSites").querySelector("tbody").innerHTML = [..._connSites.values()].map((x) => {
    const id = esc(x.id);
    const name = x.name ? esc(x.name) : `<span class="muted">${t("conn.no_name")}</span>`;
    const kind = ["air", "hydro", "mixed"].includes(x.type) ? t("type." + x.type) : "";
    const status = [kind, t(x.registered ? "conn.registered" : "conn.waiting")].filter(Boolean).join(" · ");
    const tok = x.own_token ? `…${esc(x.hint || "****")}${x.from_config ? " · config.yaml" : ""}`
      : x.revoked ? `<span class="muted">${t("conn.revoked_label")}</span>`
      : `<span class="muted">${t("conn.use_shared")}</span>`;
    const button = (act, label, danger = false) => `<button${danger ? ' class="danger"' : ""}`
      + ((_connDeleting.has(x.id) || (act !== "delete" && _connActionBusy.has(x.id))) ? " disabled" : "")
      + ` data-act="${act}" data-id="${id}">${t(label)}</button>`;
    let ops = x.own_token
      ? button("reveal", "conn.show") + button("rotate", "conn.rotate")
        + (x.from_config ? "" : button("revoke", "conn.revoke", true))
      : (x.mode === "pull" ? "" : (x.can_reveal ? button("reveal", "conn.show") : "")
        + button("rotate", "conn.make_own"));
    if (x.mode !== "pull") ops += button("delete", "site.delete", true);
    return `<tr><td>${name}<div class="muted">${esc(status)}</div></td><td class="conn-mono">${id}</td><td>${tok}</td><td>${ops}</td></tr>`;
  }).join("") || `<tr><td colspan="4" class="muted">${t("empty.none")}</td></tr>`;
}
async function loadConnect() {
  const version = _connListVersion, sequence = ++_connLoadSequence;
  const d = await api("/api/connect");
  if (version !== _connListVersion || sequence !== _connLoadSequence) return null;
  // A fresh authoritative response may include a site restored in another tab.
  for (const site of d.sites || []) _connDeletedSites.delete(site.id);
  renderConnect(d);
  return d;
}
async function openConnect() {
  hideConnResult(); connMsg("");
  _connSavedUrl = null;
  _connAddressError = "";
  $("connAdvanced").open = false;
  $("connPw").value = "";
  $("connectModal").classList.remove("hidden");
  try { await loadConnect(); return true; }
  catch (e) { connMsg(t("notify.load_fail", { msg: e.message }), true); return false; }
}
async function connPost(path, body, current = () => true) {
  const pw = $("connPw").value;
  if (!pw) { connMsg(t("conn.need_pw"), true); $("connPw").focus(); return null; }
  try {
    const r = await send("POST", path, Object.assign({ password: pw }, body));
    if (!current()) return null;
    if (!r.ok) {
      const error = await errText(r);
      if (current()) connMsg("✗ " + error, true);
      return null;
    }
    const d = await r.json();
    if (!current()) return null;
    connMsg("");
    return d;
  } catch (e) {
    if (current()) connMsg(t("notify.load_fail", { msg: e.message }), true);
    return null;
  }
}
async function connAction(act, target) {
  const version = _connCredentialVersions.get(target) || 0;
  const current = () => connActionCurrent(target, version);
  if (!current() || _connActionBusy.has(target)) return;
  if ((act === "reveal" || act === "rotate") && !connAddressReady()) return;
  if (act === "revoke" && !confirm(target === "shared" ? t("conn.confirm_disable") : t("conn.confirm_revoke", { id: target }))) return;
  if (act === "rotate" && target === "shared" && $("connShared").dataset.set === "1"
      && !confirm(t("conn.confirm_rotate"))) return;
  const operation = {};
  _connActionBusy.set(target, operation);
  renderConnSites([..._connSites.values()]);
  try {
    if (act === "reveal") {
      const d = await connPost("/api/connect/reveal", { target }, current);
      if (d && current()) {
        if (showConnResult(target === "shared" ? t("conn.for_shared") : t("conn.for_site", { id: target }), d))
          await copyConnInfo();
      }
      return;
    }
    const d = await connPost("/api/connect/token", { target, action: act }, current);
    if (!d || !current()) return;
    invalidateConnCredentials(target);
    if (d.token) showConnResult(target === "shared" ? t("conn.for_shared") : t("conn.for_site", { id: d.target }), d);
    else connMsg(t("conn.revoked"));
    await loadConnect();
  } catch (e) {
    if (current()) connMsg(t("notify.load_fail", { msg: e.message }), true);
  } finally {
    if (_connActionBusy.get(target) === operation) _connActionBusy.delete(target);
    renderConnSites([..._connSites.values()]);
  }
}
$("btnConnect").onclick = openConnect;
$("connectClose").onclick = () => { hideConnResult(); $("connPw").value = ""; $("connectModal").classList.add("hidden"); };
$("connSharedShow").onclick = () => connAction("reveal", "shared");
$("connSharedRotate").onclick = () => connAction("rotate", "shared");
$("connSharedRevoke").onclick = () => connAction("revoke", "shared");
$("connNewSite").onclick = async () => {
  if (!connAddressReady()) return;
  const name = $("connSiteName").value.trim();
  if (!name || Array.from(name).length > 64 || /[\x00-\x1f\x7f]/.test(name)) {
    connMsg(t("conn.name_invalid"), true); $("connSiteName").focus(); return;
  }
  $("connNewSite").disabled = true;
  try {
    const d = await connPost("/api/connect/token", { action: "new_site", site_name: name, site_type: $("connSiteType").value });
    if (!d) return;
    if (showConnResult(t("conn.for_new_site", { id: d.target }), d)) await copyConnInfo();
    await loadConnect();
  } catch (e) { connMsg(t("notify.load_fail", { msg: e.message }), true); }
  finally { $("connNewSite").disabled = false; }
};
$("tblConnSites").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b || b.disabled) return;
  if (b.dataset.act === "delete") await deleteConnSite(b.dataset.id);
  else await connAction(b.dataset.act, b.dataset.id);
});
$("connUrlSave").onclick = async () => {
  const version = _connListVersion;
  const d = await connPost("/api/connect/settings", { public_url: $("connUrl").value.trim() });
  if (d) { hideConnResult(); renderConnect(d, true, version); connMsg(t("conn.saved")); }
};
$("connAutoReg").onchange = async () => {
  const version = _connListVersion;
  const want = $("connAutoReg").checked;
  const d = await connPost("/api/connect/settings", { auto_register: want });
  if (d) { renderConnect(d, false, version); connMsg(t("conn.saved")); } else $("connAutoReg").checked = !want;
};
$("connCodeCopy").onclick = copyConnInfo;
$("connTokCopy").onclick = () => {
  const token = $("connTok").value;
  return copyText(token, $("connTok"), "conn.token_copied", () => $("connTok").value === token);
};
$("connFpCopy").onclick = () => copyText($("connFp").textContent);
$("connUrl").oninput = hideConnResult;
$("connSiteName").oninput = hideConnResult;
$("connSiteType").onchange = hideConnResult;
$("connInstallOs").onchange = renderConnInstall;
$("connInstallGithubCopy").onclick = () => copyText($("connInstallGithub").textContent);
$("connInstallGiteeCopy").onclick = () => copyText($("connInstallGitee").textContent);
$("connCliLinuxCopy").onclick = () => copyText($("connCliLinux").textContent);
$("connCliWindowsCopy").onclick = () => copyText($("connCliWindows").textContent);

// ---- 启动 ----
let _loops = false;
function startLoops() {
  if (_loops) return;
  _loops = true;
  refresh();
  setInterval(refresh, 30000);
  checkUpdate();
  setInterval(checkUpdate, 6 * 3600 * 1000);
  setInterval(() => loadSecurity(false).catch(() => {}), 3600 * 1000);
}
(async function init() {
  try {
    const me = await (await fetch("/api/me")).json();
    authOn = !!me.auth_enabled;
    if (me.auth_enabled && !me.authenticated) { showLogin(); return; }
    setUser(me.user, me.role);
    startLoops();
  } catch (e) {
    banner(t("banner.lost_short"));
    setTimeout(init, 5000);
  }
})();


(function bootI18n(){
  if (!window.I18N) return;
  I18N.init();
  function sync(){
    const v = I18N.lang;
    ["langSelect","langSelectLogin"].forEach(id => { const s = document.getElementById(id); if (s) s.value = v; });
  }
  sync();
  document.addEventListener("i18n:change", () => {
    sync();
    if (!$("loginOverlay").classList.contains("hidden")) return;
    refresh().catch(() => {});
    if (curSite && !$("siteModal").classList.contains("hidden")) loadSite().catch(() => {});
    if (!$("deletedSitesModal").classList.contains("hidden")) loadDeletedSites().catch(() => {});
  });
})();
