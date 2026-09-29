/* 云端总览前端 */
"use strict";

// ---- 调色板(已按暗色面板 #161b22 校验)：场地序列色按 config 顺序固定分配，不随筛选重排 ----
const SERIES = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"];
const INK = { grid: "#21262d", axis: "#30363d", tick: "#6e7681", label: "#adbac7" };
const METRIC = { hashrate: "#3987e5", online: "#199e70", power: "#c98500" };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// 数值字段一律过 num() 再拼进 innerHTML：场地来数不可信，数值列里若混进字符串
// (如 "<img onerror=...>")，这里变成 null/0，绝不会被当成 HTML 执行。
const num = (v) => (v == null || v === "" || !isFinite(Number(v)) ? null : Number(v));
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
  if (kw == null) return "–";
  if (Math.abs(kw) >= 1000) return (kw / 1000).toLocaleString(undefined, { maximumFractionDigits: 2 }) + " MW";
  return Number(kw).toLocaleString(undefined, { maximumFractionDigits: 1 }) + " kW";
}
function fmtEnergy(kwh) {
  if (kwh == null) return "–";
  if (Math.abs(kwh) >= 1e6) return (kwh / 1e6).toLocaleString(undefined, { maximumFractionDigits: 2 }) + " GWh";
  if (Math.abs(kwh) >= 1e3) return (kwh / 1e3).toLocaleString(undefined, { maximumFractionDigits: 2 }) + " MWh";
  return Number(kwh).toLocaleString(undefined, { maximumFractionDigits: 1 }) + " kWh";
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
const endLabels = {  // 多序列时在线尾直接标注场地名(文字用墨色，线端色块即身份)
  id: "endLabels",
  afterDatasetsDraw(chart) {
    if (chart.data.datasets.length < 2) return;
    const ctx = chart.ctx;
    ctx.save(); ctx.font = "11px sans-serif"; ctx.textBaseline = "middle"; ctx.fillStyle = INK.label;
    chart.data.datasets.forEach((ds, i) => {
      const meta = chart.getDatasetMeta(i);
      if (meta.hidden || !meta.data.length) return;
      const last = meta.data[meta.data.length - 1];
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
          callbacks: {
            title: (items) => items.length ? fmtFull(items[0].parsed.x) : "",
            label: (item) => ` ${item.dataset.label}: ${Number(item.parsed.y).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${yUnit || ""}`,
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
  const maxTh = Math.max(1, ...d.sites.flatMap((s) => s.points.map((p) => p[1] || 0)));
  const u = hashUnit(maxTh);
  const datasets = d.sites.map((s, i) => ({
    label: s.name, borderColor: SERIES[i % SERIES.length], backgroundColor: SERIES[i % SERIES.length],
    data: withGaps(s.points.map((p) => ({ x: p[0], y: (p[1] || 0) / u.div }))),
  }));
  mkLine("trendChart", datasets, { hours, yUnit: u.unit + "/s", padRight: 70 });
}
$("trendRange").onchange = () => {
  localStorage.setItem("co_trend_hours", $("trendRange").value);
  loadTrend().catch(() => {});
};
if (localStorage.getItem("co_trend_hours")) $("trendRange").value = localStorage.getItem("co_trend_hours");

// ---- 总览渲染 ----
const pct = (a, b) => (b ? ((100 * a) / b).toFixed(1) + "%" : "–");

function siteCard(s, color) {
  const st = STATE[s.state] || STATE.pending;
  const tp = TYPE[s.type] || TYPE.air;
  const hasData = num(s.total) != null;
  const cf = int0(s.containers_faulty), co = int0(s.containers_offline);
  const contRow = (s.type === "hydro" || s.type === "mixed" || int0(s.containers) > 0)
    ? `<div><span class="k">${t("card.containers")}</span><span class="num-tab">${intTxt(s.containers)}${cf || co ? ` <span style="color:#ec835a">${t("card.fault_offline", { cf, co })}</span>` : ""}</span></div>`
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
    <div class="hr">${hasData ? fmtHash(num(s.hashrate_ths)).replace(/ (\w+)$/, ' <span class="unit">$1/s</span>') : "–"}</div>
    <div class="stats">
      <div><span class="k">${t("card.online")}</span><span class="num-tab">${hasData ? `${int0(s.online)}/${int0(s.total)} (${pct(int0(s.online), int0(s.total))})` : "–"}</span></div>
      <div><span class="k">${t("card.power")}</span><span class="num-tab">${hasData ? fmtPower(num(s.power_kw)) : "–"}</span></div>
      <div><span class="k">${t("card.alerts")}</span><span class="num-tab">${intTxt(s.active_alerts)}</span></div>
      <div><span class="k">${t("card.customers")}</span><span class="num-tab">${int0(s.customers)}</span></div>
      ${contRow}
    </div>
    ${err}
    <div class="foot"><span>${t("card.updated", { ago: fmtAgo(s.data_age_s) })}</span><span>${t("card.click_trend")}</span></div>
  </div>`;
}

function renderOverview(o) {
  const totals = o.totals;
  $("kSites").textContent = `${totals.sites_ok}/${totals.sites}`;
  $("kMachines").textContent = `${totals.machines_online.toLocaleString()}/${totals.machines_total.toLocaleString()}`;
  $("kOnlinePct").textContent = pct(totals.machines_online, totals.machines_total);
  $("kHashrate").textContent = fmtHash(totals.hashrate_ths, "/s");
  $("kPower").textContent = fmtPower(totals.power_kw);
  $("kAlerts").textContent = totals.active_alerts;
  $("kAlerts").className = "num" + (totals.active_alerts > 0 ? " hot" : "");
  $("kContainers").textContent = `${totals.containers} (${totals.containers_faulty}/${totals.containers_offline})`;
  $("kContainers").className = "num" + (totals.containers_faulty + totals.containers_offline > 0 ? " hot" : "");
  $("kCustomers").textContent = totals.customers;

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

function renderCustomers(d) {
  $("tblWorkers").querySelector("tbody").innerHTML = d.by_worker.map((w) => `<tr>
    <td>${esc(w.worker)}</td><td>${int0(w.sites)}</td><td>${intTxt(w.machines)}</td>
    <td>${fmtHash(num(w.delivered_th_h), "·h")}</td><td>${fmtEnergy(num(w.power_kwh))}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">${t("empty.customers")}</td></tr>`;
  $("tblCustomers").querySelector("tbody").innerHTML = d.rows.map((r) => `<tr>
    <td>${esc(r.site_name)}</td><td>${esc(r.worker)}</td><td>${intTxt(r.machines)}</td>
    <td>${num(r.uptime_pct) != null ? num(r.uptime_pct).toFixed(1) + "%" : "–"}</td>
    <td>${fmtHash(num(r.delivered_th_h), "·h")}</td><td>${fmtEnergy(num(r.power_kwh))}</td></tr>`).join("")
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
function openSite(id) { curSite = id; $("siteModal").classList.remove("hidden"); loadSite().catch(() => {}); }
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
  const hours = Number($("siteRange").value);
  const d = await api(`/api/site/${encodeURIComponent(curSite)}?hours=${hours}`);
  const s = d.site, st = STATE[s.state] || STATE.pending, tp = TYPE[s.type] || TYPE.air;
  $("siteTitle").innerHTML = `${esc(s.name)} <span class="pill ${tp.cls}">${tp.txt()}</span> <span class="pill ${st.cls}">${st.txt(s)}</span>`;
  $("siteMeta").innerHTML =
    t("site.meta", { data: fmtAgo(s.data_age_s), scan: fmtAgo(s.scan_age_s) }) +
    (int0(s.fails) ? t("site.fails", { n: int0(s.fails) }) : "") +
    (s.error ? `<br><span style="color:#f0a0a0">${esc(s.error)}</span>` : "");
  renderSiteAlerts(d.alert_summary);

  const tr = d.trend;
  const maxTh = Math.max(1, ...tr.map((p) => p.hashrate_ths || 0));
  const u = hashUnit(maxTh);
  mkLine("chHr", [{ label: t("chart.hashrate"), borderColor: METRIC.hashrate, backgroundColor: METRIC.hashrate,
    data: withGaps(tr.map((p) => ({ x: p.ts, y: (p.hashrate_ths || 0) / u.div }))) }], { hours, yUnit: u.unit + "/s" });
  mkLine("chOnline", [{ label: t("chart.online"), borderColor: METRIC.online, backgroundColor: METRIC.online,
    data: withGaps(tr.map((p) => ({ x: p.ts, y: p.online || 0 }))) }], { hours, yUnit: t("chart.unit_machines") });
  mkLine("chPower", [{ label: t("chart.power"), borderColor: METRIC.power, backgroundColor: METRIC.power,
    data: withGaps(tr.map((p) => ({ x: p.ts, y: p.power_kw || 0 }))) }], { hours, yUnit: "kW" });

  $("tblSiteCustomers").querySelector("tbody").innerHTML = d.customers.map((r) => `<tr>
    <td>${esc(r.worker)}</td><td>${intTxt(r.machines)}</td>
    <td>${num(r.uptime_pct) != null ? num(r.uptime_pct).toFixed(1) + "%" : "–"}</td>
    <td>${fmtHash(num(r.delivered_th_h), "·h")}</td><td>${fmtEnergy(num(r.power_kwh))}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">${t("empty.none")}</td></tr>`;

  loadDaily(d.site.id).catch(() => {});
}

async function loadDaily(id) {
  const d = await api(`/api/daily?site=${encodeURIComponent(id)}&days=14`);
  $("tblDaily").querySelector("tbody").innerHTML = d.days.map((r) => `<tr>
    <td>${esc(r.day)}</td><td>${fmtHash(num(r.avg_hashrate_ths), "/s")}</td>
    <td>${fmtHash(num(r.max_hashrate_ths), "/s")}</td>
    <td>${intTxt(r.avg_online)}</td>
    <td>${num(r.avg_total) ? (100 * num(r.avg_online) / num(r.avg_total)).toFixed(1) + "%" : "–"}</td>
    <td>${fmtEnergy(num(r.power_kwh))}</td></tr>`).join("")
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
  sessions_revoke: () => t("audit.sessions_revoke"),
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
  });
})();
