"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");
const q = require("../web/data-quality.js");
const rates = require("../web/customer-rates.js");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
const app = fs.readFileSync(path.join(root, "web/app.js"), "utf8");
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
const isCloud = app.includes("function renderOverview");
const dicts = {};
for (const lang of ["zh", "en", "ru", "es", "de", "ar"])
  vm.runInNewContext(fs.readFileSync(path.join(root,"web/i18n",lang+".js"),"utf8"),
    {I18N:{register:(name,values)=>{dicts[name]=values;}}});
const t = (key,args={}) => (dicts.zh[key] || key).replace(/\{(\w+)\}/g,(_,k)=>args[k] ?? "");
const format = n => n + " kWh";
const visibleText = html => html.replace(/<[^>]*>/g, "").replace(/\s+/g, " ").trim();

// Exercise the shipped Chart.js parser and segment resolver. Only canvas
// drawing is inert; line gaps, skip flags and scriptable styles are real.
function realTrendChart(config) {
  const ActualChart = require("../web/vendor/chart.umd.min.js");
  const plain = value => Array.isArray(value) ? Array.from(value, plain) :
    value && typeof value === "object" ? Object.fromEntries(
      Object.entries(value).map(([key,item])=>[key,plain(item)])) : value;
  config = plain(config);  // Normalize VM option objects while keeping callbacks.
  const canvas = {width:800, height:300};
  const context = new Proxy({canvas, measureText:s=>({width:String(s).length*7}), getLineDash:()=>[]}, {
    get:(target,key)=>key in target ? target[key] : ()=>{},
    set:(target,key,value)=>(target[key]=value,true)
  });
  canvas.getContext=()=>context;
  return new ActualChart(canvas, {...config, options:{...config.options, responsive:false,
    plugins:{...config.options.plugins, legend:{display:false}, tooltip:{enabled:false}}}});
}

const coverage = (known=538, total=539, seconds=300, window=300) => ({
  known_min:known, known_max:known, total_min:total, total_max:total,
  seconds, window_seconds:window, continuous:window>0 && seconds===window,
});
const observed = {billing_method:"time_weighted_v2",billing_source:"observed",billing_hours:24,
  power_kwh:3, power_observed_hours:1,power_coverage_pct:100/24,
  delivered_th_h:293,delivery_observed_hours:1,delivery_coverage_pct:100/24,
  uptime_pct:100,uptime_observed_hours:1,uptime_coverage_pct:100/24};

test("unknown and measured zero stay different; discarded estimates are not rendered", () => {
  for (const n of [null,undefined,true,false,-1,Infinity,NaN,"", " ", [], {}, "bad"])
    assert.equal(q.number(n),null);
  assert.equal(q.number(0),0);
  assert.equal(q.number("293"),293);
  assert.equal(visibleText(q.billing({},"power",t,format)),"—");
  assert.equal(visibleText(q.billing({...observed,power_kwh:0},"power",t,format)),"0 kWh");
  const old=q.billing({power_kwh:99},"power",t,format);
  assert.equal(visibleText(old),"—");
  assert.doesNotMatch(old,/99|旧口径|完整性未验证|<details/);
});

test("energy and availability keep their own coverage only in hover context", () => {
  const row={...observed,power_observed_hours:6,power_coverage_pct:25,observed_hours:24,coverage_pct:100};
  const cell=q.billing(row,"power",t,format);
  assert.equal(visibleText(cell),"3 kWh");
  assert.match(cell,/title="[^"]*6\/24h/);
  const up=q.billing({...row,uptime_observed_hours:12,uptime_coverage_pct:50},"uptime",t,n=>n+"%");
  assert.equal(visibleText(up),"100%");
  assert.match(up,/title="[^"]*12\/24h/);
  assert.doesNotMatch(up,/6\/24h/);
  assert.doesNotMatch(cell+up,/rate-note|<details/);
});

test("incompatible site windows stay unavailable without extra table rows", () => {
  for (const alignment of ["unaligned","unverified"]){
    const r={...observed,sites:2,billing_window_alignment:alignment,power_unavailable_reason:alignment+"_windows"};
    const cell=q.billing(r,"power",t,format);
    assert.equal(visibleText(cell),"—");
    assert.match(cell,/title="[^"]*时间未对齐/);
    assert.doesNotMatch(cell,/3 kWh/);
    const avg=rates.cell({avg_hashrate_ths:586,hashrate_hours:24,observed_hours:24,coverage_pct:100,sites:2,window_alignment:alignment},"average",t,n=>n+" TH/s",n=>n+"s");
    assert.equal(visibleText(avg),"—");
    assert.doesNotMatch(avg,/586 TH/);
  }
  const stale=q.billing({...observed,report_stale:true},"power",t,format);
  assert.equal(visibleText(stale),"3 kWh");
  assert.match(stale,/title="报表已过期"/);
});

test("daily cells show valid measurements and never fall back to discarded records",()=>{
  const d={method:"time_weighted_v2",elapsed_hours:6,power_kwh:12,
    metric_observed_hours:{power_kw:3},metric_coverage_pct:{power_kw:50},legacy_power_kwh:36};
  const cell=q.daily(d,"power_kwh","power_kw",t,format);
  assert.equal(visibleText(cell),"12 kWh");
  assert.match(cell,/title="[^"]*3\/6h/);
  assert.doesNotMatch(cell,/3\/24h|36 kWh|旧口径|<details/);
  const old=q.daily({method:"legacy_sample_mean",power_kwh:36},"power_kwh","power_kw",t,format);
  assert.equal(visibleText(old),"—");
  assert.doesNotMatch(old,/36|完整性未验证/);
});

test("partial totals and plotting never manufacture measured zero",()=>{
  const cell=q.total({totals:{sites:2,power_kw:null},observed_totals:{power_kw:3},
    metric_known_sites:{power_kw:1},metrics_complete:{power_kw:false}},"power_kw",t,format);
  assert.equal(visibleText(cell),isCloud ? "3 kWh" : "—");
  assert.match(cell,/title="已统计 1\/2 个场地"/);
  assert.deepEqual(q.points([[1,null],[2,0],[3,293],[4,true]],0,1),[
    {x:1,y:null},{x:2,y:0},{x:3,y:293},{x:4,y:null}]);
});

test("all six locales cover quality and unit statements and preserve placeholders",()=>{
  const powerKeys = ["card.power", "th.power", "worker.kwh", "worker.hint", "miner.detail_hr",
    "seg.stat", "cbox.power_detail", "cbox.detail_power", "kpi.power", "chart.power", "th.kwh", "th.kwh24"];
  const keys=Object.keys(dicts.zh).filter(k=>k.startsWith("quality.") || k.startsWith("power.") || k.startsWith("trend.") ||
    powerKeys.includes(k) || k==="kind.inventory");
  for (const [lang,values] of Object.entries(dicts)) for (const key of keys){
    assert.ok(values[key],lang+": "+key);
    assert.deepEqual((values[key].match(/\{\w+\}/g)||[]).sort(),(dicts.zh[key].match(/\{\w+\}/g)||[]).sort(),lang+": "+key);
  }
});

function setup(){
  const elements=new Map(), configs={};
  function el(id){
    assert.ok(ids.has(id),"real element exists: "+id);
    if(!elements.has(id)){
      const cls=new Set(["hidden"]),body={innerHTML:""};
      elements.set(id,{id,value:"24",textContent:"",innerHTML:"",title:"",style:{},disabled:false,
        classList:{contains:k=>cls.has(k),add:k=>cls.add(k),remove:k=>cls.delete(k),toggle:(k,v)=>v?cls.add(k):cls.delete(k)},
        querySelector:()=>body,addEventListener(){},focus(){}});
    }
    return elements.get(id);
  }
  class Chart{
    constructor(canvas,config){this.data=config.data;this.options=config.options;configs[canvas.id]=config;}
    destroy(){} update(){}
  }
  let response={};
  const ctx=vm.createContext({document:{getElementById:el,addEventListener(){}},window:{Chart},Chart,
    localStorage:{getItem:()=>null,setItem(){}},AntDataQuality:q,AntCustomerRates:rates,t,Date,console,
    updateProgress(){},setSiteBusy(){},renderSiteAlerts(){},setInterval(){},setTimeout(){}});
  if(isCloud){
    vm.runInContext(app.slice(0,app.indexOf("// ---- HTTP ----")),ctx);
    ctx.api=async()=>response;
    vm.runInContext("let curSite=null,curSiteData=null,curRole='admin';",ctx);
    vm.runInContext(app.slice(app.indexOf("// ---- 图表 ----"),app.indexOf("async function refresh()")),ctx);
    vm.runInContext(app.slice(app.indexOf("let _siteSeq = 0"),app.indexOf("// ---- 版本更新")),ctx);
    vm.runInContext(app.slice(app.indexOf("function openSite(id)"),app.indexOf("function setSiteBusy(busy)")),ctx);
    vm.runInContext(app.slice(app.indexOf("function closeSite()"),app.indexOf("function fmtDuration(s)")),ctx);
  }else{
    vm.runInContext(app.slice(0,app.indexOf("async function ackAlert")),ctx);
    ctx.jget=async()=>response;
    vm.runInContext(app.slice(app.indexOf("function trendYScale"),app.indexOf("let lastMiners")),ctx);
    vm.runInContext(app.slice(app.indexOf("let lastMiners"),app.indexOf("/* ---------------- 货架视图 ---------------- */")),ctx);
    vm.runInContext(app.slice(app.indexOf("async function openMiner"),app.indexOf("async function triggerScan")),ctx);
    vm.runInContext("let view='worker';",ctx);
    vm.runInContext(app.slice(app.indexOf("const CBOX_PUMP_KEYS"),app.indexOf("function refreshAll()")),ctx);
    vm.runInContext(app.slice(app.indexOf("async function refreshWorkers()"),app.indexOf('let view = "list"')),ctx);
  }
  return {ctx,el,configs,respond:r=>{response=r;}};
}

function deferredRequests(h) {
  const pending = [];
  h.ctx.api = url => new Promise((resolve, reject) => pending.push({url, resolve, reject}));
  return pending;
}

test("overview trend keeps the latest range even after an older response arrives", async()=>{
  const h=setup(), pending=deferredRequests(h);
  const old=h.ctx.loadTrend();
  h.el("trendRange").value="168";
  const current=h.ctx.loadTrend();
  pending[1].resolve({sites:[{name:"NEW_168_HOURS",points:[[1,100]]}]});
  await current;
  pending[0].resolve({sites:[{name:"OLD_24_HOURS",points:[[1,200]]}]});
  await old;
  assert.equal(h.configs.trendChart.data.datasets[0].label,"NEW_168_HOURS");
});

test("trend selection returning to the same period still discards the oldest request", async()=>{
  const h=setup(), pending=deferredRequests(h);
  const oldest=h.ctx.loadTrend();
  h.el("trendRange").value="168";
  const middle=h.ctx.loadTrend();
  h.el("trendRange").value="24";
  const current=h.ctx.loadTrend();
  pending[2].resolve({sites:[{name:"CURRENT_24_HOURS",points:[[1,100]]}]});
  await current;
  pending[1].resolve({sites:[{name:"STALE_168",points:[[1,200]]}]});
  pending[0].resolve({sites:[{name:"STALE_24",points:[[1,300]]}]});
  await Promise.all([oldest,middle]);
  assert.equal(h.configs.trendChart.data.datasets[0].label,"CURRENT_24_HOURS");
});

test("site detail and daily data cannot overwrite a newer request for the same site", async()=>{
  const h=setup(), pending=deferredRequests(h);
  vm.runInContext("curSite='a'",h.ctx);
  const detail=name=>({site:{id:"a",name,type:"air",state:"ok",mode:"push"},trend:[],customers:[],alert_summary:[]});
  const old=h.ctx.loadSite();
  pending[0].resolve(detail("OLD_SITE_RESPONSE"));
  await old; // Its daily request remains in flight.
  h.el("siteRange").value="168";
  const current=h.ctx.loadSite();
  pending[2].resolve(detail("NEW_SITE_RESPONSE"));
  await current;
  pending[3].resolve({days:[{method:"time_weighted_v2",day:"NEW_DAY"}]});
  await Promise.resolve();
  pending[1].resolve({days:[{method:"time_weighted_v2",day:"OLD_DAY"}]});
  await Promise.resolve();
  assert.match(h.el("siteTitle").innerHTML,/NEW_SITE_RESPONSE/);
  assert.match(h.el("tblDaily").querySelector().innerHTML,/NEW_DAY/);
  assert.doesNotMatch(h.el("tblDaily").querySelector().innerHTML,/OLD_DAY/);
});

test("out-of-order site range responses and a closed detail view are ignored", async()=>{
  const h=setup(), pending=deferredRequests(h);
  vm.runInContext("curSite='a'",h.ctx);
  const detail=name=>({site:{id:"a",name,type:"air",state:"ok",mode:"push"},trend:[],customers:[],alert_summary:[]});
  const old=h.ctx.loadSite();
  h.el("siteRange").value="168";
  const current=h.ctx.loadSite();
  pending[1].resolve(detail("CURRENT_SITE"));
  await current;
  pending[0].resolve(detail("STALE_SITE"));
  await old;
  assert.match(h.el("siteTitle").innerHTML,/CURRENT_SITE/);
  const afterClose=h.ctx.loadSite();
  vm.runInContext("curSite=null",h.ctx);
  pending[pending.length-1].resolve(detail("CLOSED_SITE"));
  await afterClose;
  assert.doesNotMatch(h.el("siteTitle").innerHTML,/CLOSED_SITE/);
});

function trackSiteLoads(h) {
  const load=h.ctx.loadSite, requests=[];
  h.ctx.loadSite=(...args)=>{
    const request=load(...args);
    requests.push(request);
    return request;
  };
  return requests;
}
const siteDetail=(name,id="a")=>({site:{id,name,type:"air",state:"ok",mode:"push"},trend:[],customers:[],alert_summary:[]});

test("the real close button and same-site reopen discard the former detail request", async()=>{
  const h=setup(), pending=deferredRequests(h), loads=trackSiteLoads(h);
  h.ctx.openSite("a");
  h.el("siteClose").onclick();
  assert.equal(vm.runInContext("curSite",h.ctx),null);
  assert.equal(vm.runInContext("curSiteData",h.ctx),null);
  assert.equal(h.el("siteModal").classList.contains("hidden"),true);
  h.ctx.openSite("a");
  assert.equal(h.el("siteModal").classList.contains("hidden"),false);
  assert.equal(pending[0].url,pending[1].url);
  pending[1].resolve(siteDetail("REOPENED_SITE"));
  await loads[1];
  pending[0].resolve(siteDetail("CLOSED_SITE"));
  await loads[0];
  assert.equal(h.el("siteName").value,"REOPENED_SITE");
  assert.equal(vm.runInContext("curSiteData.name",h.ctx),"REOPENED_SITE");
  assert.equal(pending.length,3); // Only the accepted response starts a daily request.
  pending[2].resolve({days:[]});
  await Promise.resolve();
});

test("site A to B to A at one period keeps the last A response", async()=>{
  const h=setup(), pending=deferredRequests(h), loads=trackSiteLoads(h);
  h.ctx.openSite("a");
  h.ctx.openSite("b");
  h.ctx.openSite("a");
  pending[2].resolve(siteDetail("CURRENT_A"));
  await loads[2];
  pending[0].resolve(siteDetail("STALE_A"));
  pending[1].resolve(siteDetail("STALE_B","b"));
  await Promise.all(loads);
  assert.equal(h.el("siteName").value,"CURRENT_A");
  assert.match(h.el("siteTitle").innerHTML,/CURRENT_A/);
  assert.equal(pending.length,4);
  pending[3].resolve({days:[]});
  await Promise.resolve();
});

test("closing and reopening a site invalidates its in-flight daily response", async()=>{
  const h=setup(), pending=deferredRequests(h), loads=trackSiteLoads(h);
  h.ctx.openSite("a");
  pending[0].resolve(siteDetail("FIRST_OPEN"));
  await loads[0];
  h.el("siteClose").onclick();
  h.ctx.openSite("a");
  pending[2].resolve(siteDetail("SECOND_OPEN"));
  await loads[1];
  pending[3].resolve({days:[{method:"time_weighted_v2",day:"REOPENED_DAY"}]});
  await Promise.resolve();
  pending[1].resolve({days:[{method:"time_weighted_v2",day:"CLOSED_DAY"}]});
  await Promise.resolve();
  const html=h.el("tblDaily").querySelector().innerHTML;
  assert.match(html,/REOPENED_DAY/);
  assert.doesNotMatch(html,/CLOSED_DAY/);
});

test("a rejected request from a closed site cannot show an error on its reopened view", async()=>{
  const h=setup(), pending=deferredRequests(h), loads=trackSiteLoads(h);
  h.ctx.openSite("a");
  h.el("siteClose").onclick();
  h.ctx.openSite("a");
  pending[1].resolve(siteDetail("CURRENT_SITE"));
  await loads[1];
  pending[0].reject(new Error("obsolete request failed"));
  await loads[0];
  await Promise.resolve();
  assert.equal(h.el("siteManageMsg").textContent,"");
  assert.equal(h.el("siteName").value,"CURRENT_SITE");
  pending[2].resolve({days:[]});
  await Promise.resolve();
});

test("real power and energy renderers keep kW distinct from period kWh at site scale",async()=>{
  const h=setup();
  const compact = s => s.replace(/[\s,]/g, "");
  const rawPower = kw => isCloud ? kw : kw * 1000;
  for (const kw of [0, 5.355, 2870, 7200, 10070]) {
    assert.equal(compact(h.ctx.fmtPower(rawPower(kw))), kw + "kW");
  }
  for (const kwh of [0, 54080, 134568, 1000000]) {
    assert.equal(compact(h.ctx.fmtEnergy(kwh)), kwh + "kWh");
  }
  for (const bad of [null, undefined, true, false, -1, Infinity, NaN, "", "bad"]) {
    assert.match(h.ctx.fmtPower(bad), /^[-–—]$/);
    assert.match(h.ctx.fmtEnergy(bad), /^[-–—]$/);
  }
  const row = {...observed, worker:"A", site_id:"a", site_name:"A", sites:1,
    power_kwh:54080, power_observed_hours:24, power_coverage_pct:100};
  if (isCloud) {
    h.ctx.renderOverview({ts:1800000000, totals:{sites:2, sites_ok:2, power_kw:10070},
      sites:[{id:"a", name:"A", type:"hydro", state:"ok", total:1330, online:1330, power_kw:7200},
        {id:"b", name:"B", type:"hydro", state:"ok", total:539, online:539, power_kw:2870}]});
    assert.match(compact(h.el("kPower").innerHTML), /10070kW/);
    assert.match(compact(h.el("gridHydro").innerHTML), /7200kW/);
    assert.match(compact(h.el("gridHydro").innerHTML), /2870kW/);
    h.ctx.renderCustomers({rows:[row], by_worker:[row]});
    h.ctx.renderSiteCustomers([row]);
    for (const id of ["tblCustomers", "tblWorkers", "tblSiteCustomers"]) {
      const body=h.el(id).querySelector().innerHTML;
      assert.match(compact(body), /54080kWh/);
      assert.doesNotMatch(body, /MWh|GWh/);
    }
  } else {
    h.respond({scanned:true, scan_ts:1800000000, total_power_kw:10070, known_power_kw:10070, power_complete:true});
    await h.ctx.refreshSummary();
    assert.match(compact(h.el("cPower").innerHTML), /10070/);
    h.respond({miners:[{ip:"10.0.0.1", status:"online", power:5355}], agg:{total_power:5355}});
    await h.ctx.refreshMiners();
    assert.match(h.el("minerBody").innerHTML, /<td>5.355 kW<\/td>/);
    h.respond({current:{power:5355}, history:[]});
    await h.ctx.openMiner("10.0.0.1");
    assert.match(h.el("mInfo").innerHTML, /当前功率：5.355 kW/);
    assert.doesNotMatch(h.el("mInfo").innerHTML, /kW W/);
    h.respond({customers:[row]});
    await h.ctx.refreshWorkers();
    assert.match(compact(h.el("workerList").innerHTML), /54080kWh/);
    assert.doesNotMatch(h.el("workerList").innerHTML, /MWh|GWh/);
  }
  for (const lang of Object.keys(dicts)) {
    assert.match(dicts[lang][isCloud ? "th.kwh" : "worker.kwh"], /kWh/);
    assert.match(dicts[lang][isCloud ? "kpi.power" : "card.power"], /kW/);
    assert.match(dicts[lang]["power.units_help"], /1000 kW/);
  }
});

test("actual dashboard handlers handle incomplete APIs without null crashes or invented totals",async()=>{
  const h=setup();
  if(isCloud){
    h.ctx.renderOverview({ts:1800000000,totals:{sites:2,sites_ok:1,machines_online:null,machines_total:null,
      hashrate_ths:null,power_kw:null,active_alerts:null,containers:null,containers_faulty:null,containers_offline:null,customers:1},
      observed_totals:{machines_online:1,machines_total:1,hashrate_ths:293,power_kw:3},
      metrics_complete:{hashrate_ths:false,power_kw:false,machines_total:false},metric_known_sites:{hashrate_ths:1,power_kw:1,machines_total:1},
      sites:[{id:"a",name:"<旧场地>",type:"air",state:"stalled",data_quality:"stale",online:100,total:100,hashrate_ths:29300}]});
    assert.equal(h.el("kMachines").textContent,"1/1");
    assert.equal(h.el("kOnlinePct").textContent,"100.0%");
    assert.match(h.el("kMachines").title,/已统计 1\/2 个场地/);
    assert.equal(h.el("kOnlinePct").title,h.el("kMachines").title);
    assert.match(h.el("kHashrate").innerHTML,/rate-value"[^>]*>293 TH\/s/);
    assert.match(h.el("kHashrate").innerHTML,/已统计 1\/2 个场地/);
    assert.match(h.el("gridAir").innerHTML,/site s-stalled/);
    assert.doesNotMatch(h.el("gridAir").innerHTML,/<旧场地>/);
    h.ctx.renderCustomers({rows:[{worker:"A",site_id:"a",...observed,power_kwh:null,uptime_pct:null}],by_worker:[]});
    assert.doesNotMatch(h.el("tblCustomers").querySelector().innerHTML,/null%|0 kWh/);
    h.respond({sites:[{name:"A",points:[[1,293],[2,null],[3,0]],legacy_points:[[1,null],[2,300],[3,null]]}]});
    await h.ctx.loadTrend();
    assert.equal(h.configs.trendChart.data.datasets[0].data[1].y,null);
    assert.equal(h.configs.trendChart.data.datasets[0].data[2].y,0);
    assert.equal(h.configs.trendChart.data.datasets.length,1);
  }else{
    h.respond({scanned:true,scan_ts:1800000000,scan_kind:"inventory",online:1,total:2,unknown:1,offline:0,
      total_hashrate_th:null,known_hashrate_ths:293,hashrate_complete:false,avg_hashrate_th:null,
      total_power_kw:null,known_power_kw:3,power_complete:false,avg_efficiency:null});
    await h.ctx.refreshSummary();
    assert.equal(h.el("cOnline").textContent,"1 / 2");
    assert.match(h.el("cTotalHr").innerHTML,/已知部分：293 TH\/s/);
    assert.match(h.el("cPower").innerHTML,/rate-value"[^>]*>—/);
    assert.equal(h.el("cEff").textContent,"—");
    assert.equal(h.el("cOnline").title,"未知：1");
    assert.equal(h.ctx.boxPower({power1:1000,power2:null}),null);
    assert.equal(h.ctx.boxPower({power1:1000,power2:500}),1500);
    assert.equal(h.ctx.rejCell({accepted:100,rejected:null}),"<td>-</td>");
    h.respond({customers:[{worker:"A",...observed,power_kwh:null,uptime_pct:null}]});
    await h.ctx.refreshWorkers();
    assert.doesNotMatch(h.el("workerList").innerHTML,/null%|0 kWh/);
    h.respond({trend:[{ts:1,total_hr:293},{ts:2,total_hr:null,legacy_total_hr:300},{ts:3,total_hr:0}]});
    await h.ctx.refreshTrend();
    assert.equal(h.configs.trendChart.data.datasets[0].data[1].y,null);
    assert.equal(h.configs.trendChart.data.datasets[0].data[2].y,0);
    assert.equal(h.configs.trendChart.data.datasets.length,1);
    assert.equal(h.configs.trendChart.options.scales.x.type,"linear");
    h.respond({current:{online:1,data_status:"unknown",data_complete:0,faults:[]},stale_after:60,
      history:[{ts:100,supply_temp:-1},{ts:110,supply_temp:0},{ts:3700,supply_temp:3}]});
    await h.ctx.openContainer("10.0.0.1");
    const temperatures=h.configs.mChart.data.datasets[0].data;
    assert.equal(h.configs.mChart.options.scales.x.type,"linear");
    assert.equal(temperatures[0].y,-1);
    assert.equal(temperatures[1].y,0);
    assert.equal(temperatures[2].y,null);
    assert.equal(temperatures[3].x,3700);
    assert.match(h.el("mInfo").innerHTML,/数据不完整/);
    assert.doesNotMatch(h.el("mInfo").innerHTML,/无故障/);
    assert.match(h.ctx.containerFaultBadge({flag:"leakage_fault",sev:"crit",observed:false,measured_ts:100}),/尚未确认恢复/);

  }
});

test("fresh sites retain complete online and hashrate totals while partial power stays visible",()=>{
  const h=setup();
  const site=(id,total,rate,power)=>({id,name:id,type:"hydro",state:"ok",data_quality:"partial",
    total,online:total,unknown:0,hashrate_ths:rate,power_kw:null,
    hashrate_complete:1,power_complete:0,power_known_machines:total-1,
    known_power_kw:power,observed_metrics:{online:total,total,hashrate_ths:rate,power_kw:power},
    metric_complete:{online:true,total:true,hashrate_ths:true,power_kw:false},
    metric_available:{online:true,total:true,hashrate_ths:true,power_kw:true}});
  h.ctx.renderOverview({ts:1800000000,
    totals:{sites:2,sites_ok:2,machines_online:1868,machines_total:1868,hashrate_ths:563380,power_kw:null},
    observed_totals:{machines_online:1868,machines_total:1868,hashrate_ths:563380,power_kw:10054.926},
    metrics_complete:{machines_online:true,machines_total:true,hashrate_ths:true,power_kw:false},
    metric_known_sites:{machines_online:2,machines_total:2,hashrate_ths:2,power_kw:2},
    sites:[site("A",539,161560,2859.426),site("B",1329,401820,7195.5)]});
  assert.equal(h.el("kMachines").textContent.replace(/,/g,""),"1868/1868");
  assert.equal(h.el("kOnlinePct").textContent,"100.0%");
  assert.equal(h.el("kMachines").title,"");
  assert.equal(h.el("kOnlinePct").title,"");
  assert.match(h.el("kHashrate").innerHTML,/rate-value"[^>]*>563.38 PH\/s/);
  assert.match(h.el("kPower").innerHTML.replace(/,/g,""),/rate-value"[^>]*>10054.926 kW/);
  assert.match(h.el("kPower").innerHTML,/部分读数/);
  assert.doesNotMatch(h.el("kPower").innerHTML,/2\/2 个场地/);
  const cards=h.el("gridHydro").innerHTML.replace(/,/g,"");
  assert.match(cards,/2859.426 kW/);
  assert.match(cards,/7195.5 kW/);
  assert.match(cards,/已统计 538\/539 台/);
  assert.match(cards,/已统计 1328\/1329 台/);
  assert.doesNotMatch(cards,/数据不完整/);


  const stale=site("Old",539,161560,9999);
  stale.state="stalled";stale.data_quality="stale";
  stale.observed_metrics={power_kw:null};stale.metric_available={power_kw:false};
  const oldCard=h.ctx.siteCard(stale,"#3987e5");
  assert.doesNotMatch(oldCard,/9999|已统计/);
  assert.match(oldCard,/site s-stalled/);
});

test("available zero power is a number and absent power stays unknown",()=>{
  const packet={totals:{sites:1,power_kw:null},observed_totals:{power_kw:0},
    metrics_complete:{power_kw:false},metric_known_sites:{power_kw:1}};
  assert.match(q.total(packet,"power_kw",t,n=>n+" kW"),/rate-value"[^>]*>0 kW/);
  assert.match(q.total(packet,"power_kw",t,n=>n+" kW"),/部分读数/);
  packet.observed_totals.power_kw=null;
  const cell=q.total(packet,"power_kw",t,n=>n+" kW");
  assert.match(cell,/rate-value"[^>]*>—/);
  assert.doesNotMatch(cell,/0 kW|部分读数/);
});

test("partial trend segments retain site colors and show actual machine coverage",async()=>{
  const h=setup();
  h.respond({sites:[{name:"A",points:[[1000,161700],[1300,null],[1600,161600],[1900,161700]],
    partial_points:[{ts:1300,value:161400,coverage:coverage()}]},
    {name:"B",points:[[1000,401000],[1300,402000],[1600,401500],[1900,402000]]}]});
  await h.ctx.loadTrend();
  const c=h.configs.trendChart, [full,part,other]=c.data.datasets;
  assert.equal(part.trendPartial,true);
  assert.equal(part.borderColor,full.borderColor);
  assert.notEqual(other.borderColor,full.borderColor);
  assert.equal(part.data[1].y,161.4);
  assert.equal(full.data[1].y,null);
  assert.equal(part.fill,false);
  assert.equal(c.options.spanGaps,false);
  const tooltip=c.options.plugins.tooltip;
  const item={dataset:part,raw:part.data[1],parsed:{y:161.4}};
  assert.match(tooltip.callbacks.label(item),/部分实测: 161.4 PH\/s/);
  assert.match(tooltip.callbacks.afterLabel(item).join("\n"),/已统计 538\/539 台/);
  assert.match(tooltip.callbacks.afterLabel(item).join("\n"),/不是完整场地总量/);
  assert.equal(tooltip.filter(item),true);
  assert.equal(tooltip.filter({dataset:part,raw:part.data[0]}),false);


  const chart=realTrendChart(c);
  try {
    const base=chart.getDatasetMeta(0), partial=chart.getDatasetMeta(1);
    assert.equal(base.data[1].skip,true);
    assert.equal(partial.data[1].skip,false);
    assert.equal(partial.data[1].options.radius,2);
    assert.equal(partial.data[0].options.radius,0);
    assert.deepEqual(partial.dataset.segments.filter(s=>s.style.borderColor!=="transparent")
      .map(s=>[s.start,s.end]),[[0,2]]);
    assert.deepEqual([...partial.dataset.options.borderDash],[5,4]);
    assert.equal(partial.dataset.segments[0].style.borderColor,full.borderColor);
    assert.ok(partial.dataset.segments.some(s=>s.start===2&&s.end===3&&s.style.borderColor==="transparent"));
  } finally {chart.destroy();}
  h.respond({sites:[{name:"A",points:[[2000,161700],[2300,161600]]}]});
  await h.ctx.loadTrend();
  assert.equal(h.configs.trendChart.data.datasets.length,1);


});

test("a time-partial bucket is an isolated point and true nulls break both lines",async()=>{
  const h=setup();
  h.respond({sites:[{name:"A",points:[[1000,161700],[1300,null],[1600,161600],
    [1900,null],[2200,161700],[2500,null],[2800,null]],partial_points:[
      {ts:1300,value:161400,coverage:coverage(539,539,180,300)},
      {ts:1900,value:161300,coverage:coverage()},
      {ts:2800,value:161500,coverage:coverage()},
    ]}]});
  await h.ctx.loadTrend();
  const c=h.configs.trendChart, part=c.data.datasets[1];
  const note=c.options.plugins.tooltip.callbacks.afterLabel({dataset:part,raw:part.data[1]}).join("\n");
  assert.match(note,/180\/300 秒有效数据/);
  assert.match(note,/缺测时段不连线/);

  const chart=realTrendChart(c);
  try {
    const full=chart.getDatasetMeta(0), partial=chart.getDatasetMeta(1);
    assert.equal(partial.data[1].skip,false);
    assert.equal(partial.data[1].options.radius,2);
    const painted=partial.dataset.segments.filter(s=>s.style.borderColor!=="transparent");
    assert.deepEqual(painted.map(s=>[s.start,s.end]),[[2,4]]);
    for(const meta of [full,partial]){
      assert.equal(meta.data[5].skip,true);
      assert.ok(meta.dataset.segments.every(s=>s.end<5||s.start>5));
    }
  } finally {chart.destroy();}
});

test("instantaneous partial zero and isolated full points are visible without invented counts",async()=>{
  const h=setup();
  h.respond({sites:[{name:"A",points:[[1000,null]],partial_points:[{
    ts:1000,value:0,coverage:coverage(null,null,0,0)}]}]});
  await h.ctx.loadTrend();
  const c=h.configs.trendChart, part=c.data.datasets[1];
  const notes=c.options.plugins.tooltip.callbacks.afterLabel({dataset:part,raw:part.data[0]}).join("\n");
  assert.match(notes,/瞬时读数/);
  assert.doesNotMatch(notes,/已统计/);
  const chart=realTrendChart(c);
  try {
    const point=chart.getDatasetMeta(1).data[0];
    assert.equal(point.skip,false);
    assert.equal(point.options.radius,2);
    assert.ok(point.y<=chart.chartArea.bottom);
  } finally {chart.destroy();}
  h.respond({sites:[{name:"A",points:[[2000,0]]}]});
  await h.ctx.loadTrend();
  const single=realTrendChart(h.configs.trendChart);
  try {
    assert.equal(single.getDatasetMeta(0).data[0].skip,false);
    assert.equal(single.getDatasetMeta(0).data[0].options.radius,2);
  } finally {single.destroy();}
});

test("site detail plots valid hashrate and power independently and excludes discarded readings",async()=>{
  const h=setup();
  vm.runInContext("curSite='a';",h.ctx);
  h.respond({site:{id:"a",name:"A",reported_name:"A",mode:"push",type:"hydro",state:"ok"},
    trend:[{ts:1000,hashrate_ths:161700,online:539,power_kw:null,partial_power_kw:2800,
      power_coverage:coverage(525),legacy_power_kw:2900},
      {ts:1300,hashrate_ths:null,partial_hashrate_ths:161400,hashrate_coverage:coverage(),
        online:538,power_kw:null,partial_power_kw:2790,power_coverage:coverage(524)}],customers:[],days:[]});
  await h.ctx.loadSite();
  const hr=h.configs.chHr, power=h.configs.chPower, online=h.configs.chOnline;
  assert.equal(hr.data.datasets[1].data[1].y,161.4);
  assert.equal(power.data.datasets[1].data[0].y,2800);
  assert.equal(power.data.datasets[0].data[0].y,null);
  assert.equal(power.data.datasets.length,2);
  assert.ok(power.data.datasets.every(d => d.data.every(p => p.y !== 2900)));
  assert.equal(online.data.datasets.length,1);
  assert.equal(online.data.datasets[0].data[0].y,539);
  const tooltip=power.options.plugins.tooltip;
  assert.match(tooltip.callbacks.label({dataset:power.data.datasets[1],parsed:{y:2800}}),/kW/);
  assert.match(tooltip.callbacks.afterLabel({dataset:power.data.datasets[1],raw:power.data.datasets[1].data[0]}).join("\n"),/525\/539/);

  const chart=realTrendChart(power);
  try {
    assert.equal(chart.getDatasetMeta(0).data[0].skip,true);
    assert.equal(chart.getDatasetMeta(1).data[0].skip,false);
    assert.equal(chart.getDatasetMeta(1).data[0].options.radius,2);
  } finally {chart.destroy();}
});

test("same-name sites retain separate measured and missing series",async()=>{
  const h=setup();
  h.respond({sites:[{id:"a",name:"Same",points:[[1000,null],[1300,null]],partial_points:[
    {ts:1000,value:161400,coverage:coverage()}, {ts:1300,value:161500,coverage:coverage()}]},
    {id:"b",name:"Same",points:[[1000,null],[1300,null]]}]});
  await h.ctx.loadTrend();
  const datasets=h.configs.trendChart.data.datasets;
  assert.equal(datasets.length,3);
  assert.equal(datasets[1].data[0].y,161.4);
  assert.ok(datasets[2].data.every(p => p.y === null));
  assert.notEqual(datasets[1].borderColor,datasets[2].borderColor);
});

test("normal report cells stay concise and exclude discarded legacy statistics", () => {
  const row={...observed,hashrate_ths:293,avg_hashrate_ths:292,hashrate_hours:24,
    observed_hours:23,coverage_pct:95.8,legacy_power_kwh:9999,current_machines:1,
    hashrate_age_s:10,report_age_s:30,period_end_ts:1800000000};
  assert.equal(visibleText(q.billing(row,"power",t,format)),"3 kWh");
  assert.equal(visibleText(q.billing(row,"uptime",t,n=>n+"%")),"100%");
  assert.equal(visibleText(rates.cell(row,"current",t,n=>n+" TH/s",n=>n+"s")),"293 TH/s");
  assert.equal(visibleText(rates.cell(row,"average",t,n=>n+" TH/s",n=>n+"s")),"292 TH/s");
  assert.equal(visibleText(q.daily({method:"legacy_sample_mean",power_kwh:9999},"power_kwh","power_kw",t,format)),"—");
  assert.doesNotMatch(q.billing(row,"power",t,format),/9999|旧口径|完整性未验证|<details/);
});


test("cloud cards use the last completed display snapshot while real connection faults remain visible",()=>{
  const h=setup();
  const display={online:538,total:538,hashrate_ths:161700,power_kw:2870,
    active_alerts:2,containers:4,containers_faulty:1,containers_offline:0,containers_unknown:0};
  const totals={sites:1,sites_ok:1,customers:2,machines_online:null,machines_total:null,
    hashrate_ths:null,power_kw:null,active_alerts:null,containers:null,containers_faulty:null,containers_offline:null};
  const s={id:"a",name:"A",type:"hydro",state:"ok",data_quality:"stale",unknown:0,
    online:null,total:null,hashrate_ths:null,power_kw:null,observed_metrics:{hashrate_ths:null},
    display_metrics:display,display_complete:{online:true,total:true,hashrate_ths:true,power_kw:true},
    display_available:{online:true,total:true,hashrate_ths:true,power_kw:true}};
  const packet={ts:1800000000,totals,display_totals:{machines_online:538,machines_total:538,
    hashrate_ths:161700,power_kw:2870,active_alerts:2,containers:4,containers_faulty:1,
    containers_offline:0,containers_unknown:0},sites:[s]};
  h.ctx.renderOverview(packet);
  assert.equal(h.el("kMachines").textContent,"538/538");
  assert.equal(h.el("kOnlinePct").textContent,"100.0%");
  assert.equal(visibleText(h.el("kHashrate").innerHTML),"161.7 PH/s");
  assert.equal(visibleText(h.el("kPower").innerHTML),"2,870 kW");
  assert.equal(visibleText(h.el("kAlerts").innerHTML),"2");
  assert.equal(visibleText(h.el("kContainers").innerHTML),"4 (1/0)");
  const card=h.el("gridHydro").innerHTML;
  assert.match(visibleText(card),/538\/538 \(100.0%\)/);
  assert.match(visibleText(card),/2,870 kW/);
  assert.doesNotMatch(visibleText(card),/旧口径|已过期|有效数据|完整性/);
  const failed=h.ctx.siteCard({...s,state:"unreachable",error:"CONNECTION_TIMEOUT"},"#fff");
  assert.match(failed,/site s-unreachable/);
  assert.match(visibleText(failed),/CONNECTION_TIMEOUT/);
  assert.match(visibleText(failed),/161\.7/);
  for(const id of ["kMachinesNote","kOnlinePctNote","overviewQuality","trendQuality","siteTrendQuality"])
    assert.equal(ids.has(id),false,id+" has no permanent diagnostic prose");
});

test("explicit missing and zero display fields override older numeric fallbacks",()=>{
  const h=setup();
  const packet={ts:1800000000,totals:{sites:1,sites_ok:1,hashrate_ths:9999,power_kw:9999},
    observed_totals:{hashrate_ths:9999,power_kw:9999},
    display_totals:{hashrate_ths:null,power_kw:0,machines_online:0,machines_total:1},sites:[]};
  h.ctx.renderOverview(packet);
  assert.equal(visibleText(h.el("kHashrate").innerHTML),"—");
  assert.equal(visibleText(h.el("kPower").innerHTML),"0 kW");
  assert.equal(h.el("kMachines").textContent,"0/1");
  const card=h.ctx.siteCard({id:"a",name:"A",type:"air",state:"ok",online:99,total:99,
    hashrate_ths:9999,power_kw:9999,display_metrics:{online:0,total:1,hashrate_ths:null,power_kw:0}},"#fff");
  assert.doesNotMatch(visibleText(card),/9999|9,999|99\/99/);
  assert.match(visibleText(card),/0\/1 \(0.0%\)/);
  assert.match(visibleText(card),/0 kW/);
});

test("daily tables exclude discarded rows and show normal current statistics without explanation stacks",async()=>{
  const h=setup();
  vm.runInContext("curSite='a';",h.ctx);
  h.respond({days:[{day:"OBSOLETE_DAY",method:"legacy_sample_mean",avg_hashrate_ths:9999,power_kwh:9999},
    {day:"CURRENT_DAY",method:"time_weighted_v2",avg_hashrate_ths:161700,max_hashrate_ths:162000,
      avg_online:538,avg_total:538,power_kwh:12345,elapsed_hours:6,
      metric_observed_hours:{hashrate_ths:5,online:5,power_kw:5},
      metric_coverage_pct:{hashrate_ths:83,online:83,power_kw:83},legacy_power_kwh:8888}]});
  await h.ctx.loadDaily("a");
  const body=h.el("tblDaily").querySelector().innerHTML;
  assert.match(visibleText(body),/CURRENT_DAY/);
  assert.match(visibleText(body),/12,345 kWh/);
  assert.doesNotMatch(body,/OBSOLETE_DAY|9999|8888|旧口径|<details|rate-note/);
  assert.doesNotMatch(visibleText(body),/有效数据|统计截止|按有效/);
});

test("a long scan shows the same completed hashrate on the cloud card and real trend chart",async()=>{
  const h=setup();
  h.ctx.renderOverview({ts:1800000000,totals:{sites:1,sites_ok:1,hashrate_ths:null},
    display_totals:{hashrate_ths:161400},sites:[]});
  assert.equal(visibleText(h.el("kHashrate").innerHTML),"161.4 PH/s");
  h.respond({sites:[{id:"a",name:"A",points:[[1000,null],[1300,null],[1600,null]],
    display_points:[[1000,161400],[1300,161400],[1600,161400]],
    partial_points:[{ts:1300,value:999999999,coverage:coverage()}]}]});
  await h.ctx.loadTrend();
  const c=h.configs.trendChart;
  assert.equal(c.data.datasets.length,1,"display snapshot replaces strict partial overlays");
  assert.deepEqual(Array.from(c.data.datasets[0].data,p=>p.y),[161.4,161.4,161.4]);
  assert.equal(c.options.scales.y.title.text,"PH/s");
  const chart=realTrendChart(c);
  try {
    const meta=chart.getDatasetMeta(0);
    assert.ok(meta.data.every(p=>!p.skip));
    assert.deepEqual(meta.dataset.segments.map(s=>[s.start,s.end]),[[0,2]]);
  } finally {chart.destroy();}
});

test("display trend gaps and genuine zero take precedence over older raw and partial values",async()=>{
  const h=setup();
  h.respond({sites:[{id:"a",name:"A",
    points:[[1000,999999999],[1300,999999999],[1600,999999999],[1900,999999999],[2200,999999999]],
    display_points:[[1000,161400],[1300,null],[1600,0],[1900,null],[2200,161400]],
    partial_points:[{ts:1300,value:888888888,coverage:coverage()},{ts:1900,value:888888888,coverage:coverage()}]}]});
  await h.ctx.loadTrend();
  const c=h.configs.trendChart;
  assert.equal(c.data.datasets.length,1);
  assert.deepEqual(Array.from(c.data.datasets[0].data,p=>p.y),[161.4,null,0,null,161.4]);
  assert.equal(c.options.scales.y.title.text,"PH/s");
  const chart=realTrendChart(c);
  try {
    const meta=chart.getDatasetMeta(0);
    assert.equal(meta.data[1].skip,true);
    assert.equal(meta.data[3].skip,true);
    assert.equal(meta.data[2].skip,false);
    assert.equal(meta.data[2].options.radius,2);
    assert.ok(meta.dataset.segments.every(s=>s.start===s.end));
  } finally {chart.destroy();}
  h.respond({sites:[{id:"a",name:"A",points:[[1000,999999999]],display_points:[]}]});
  await h.ctx.loadTrend();
  assert.equal(h.configs.trendChart.data.datasets[0].data.length,0);
});

test("site detail prefers each completed display reading and preserves explicit gaps and strict fallback",async()=>{
  const h=setup();
  vm.runInContext("curSite='a';",h.ctx);
  h.respond({site:{id:"a",name:"A",type:"hydro",state:"ok",mode:"push"},
    trend:[
      {ts:1000,hashrate_ths:null,display_hashrate_ths:161400,display_scan_ts:900,online:538,power_kw:2800},
      {ts:1300,hashrate_ths:999999999,partial_hashrate_ths:999999999,display_hashrate_ths:null,display_scan_ts:null,online:null,power_kw:null},
      {ts:1600,hashrate_ths:999999999,display_hashrate_ths:0,display_scan_ts:1550,online:538,power_kw:0},
      {ts:1900,hashrate_ths:200000,online:538,power_kw:3000}],customers:[],days:[]});
  await h.ctx.loadSite();
  const c=h.configs.chHr;
  assert.equal(c.data.datasets.length,1);
  assert.deepEqual(Array.from(c.data.datasets[0].data,p=>p.y),[161.4,null,0,200]);
  assert.equal(c.options.scales.y.title.text,"PH/s");
  const chart=realTrendChart(c);
  try {
    const meta=chart.getDatasetMeta(0);
    assert.equal(meta.data[1].skip,true);
    assert.equal(meta.data[2].skip,false);
    assert.ok(meta.dataset.segments.every(s=>s.end<1||s.start>1));
  } finally {chart.destroy();}
  assert.deepEqual(Array.from(h.configs.chPower.data.datasets[0].data,p=>p.y),[2800,null,0,3000]);
});


test("a nonzero offline count never rounds to full availability",()=>{
  const h=setup();
  assert.equal(vm.runInContext("pct(1999,2000)",h.ctx),"99.95%");
  assert.equal(vm.runInContext("pct(2000,2000)",h.ctx),"100.0%");
  assert.equal(vm.runInContext("pct(99999,100000)",h.ctx),"99.999%");
});
