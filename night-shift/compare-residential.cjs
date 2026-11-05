/* Compare all automated browser strategies on one common Norway session cohort. */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const assert = require("node:assert/strict");
const Engine = require("./dist/simulation-engine.js");
const Lab = require("./dist/charging-history-engine.js");
const Residential = require("./dist/residential-engine.js");
const ROOT = path.resolve(__dirname,"..");
const SLOT = 300, HOUR = 3600, DAY = 86400, EPS = 1e-6;
const STRATEGIES = [
  {id:"charge-now",label:"Immediate charging"},
  ...Array.from({length:24},(_,hour) => ({id:`timer-${String(hour).padStart(2,"0")}00`,
    label:`Timer ${String(hour).padStart(2,"0")}:00`})),
  ...[2,3,5].map(cap => ({id:`cap-${cap}`,label:`Price cap ${cap}¢/kWh`})),
  {id:"day-ahead",label:"SPARC day-ahead"},
  {id:"live",label:"SPARC + live prices"},
  {id:"hindsight",label:"Perfect hindsight (reference)"},
];
const TARIFFS = ["standard","time-of-day"];
const digest = text => crypto.createHash("sha256").update(text).digest("hex");

function loadMarket(year) {
  const context = {window:{}};
  const hashes = {};
  vm.createContext(context);
  for (const name of ["simulation-data.js","charging-history-data.js","residential-data.js"]) {
    const text = fs.readFileSync(path.join(__dirname,"dist",name),"utf8");
    vm.runInContext(text,context); hashes[name] = digest(text);
  }
  const bundle = context.window.SPARC_RESIDENTIAL_DATA;
  for (const source of bundle.sources) {
    assert.equal(digest(fs.readFileSync(path.join(ROOT,source.file))),source.sha256,`Rebuild stale residential bundle: ${source.file}`);
  }
  const market = Lab.createMarket(context.window.SPARC_SIM_DATA,context.window.SPARC_CHARGING_HISTORY);
  const rows = Residential.decode(bundle).map(row=>Residential.adapt(market,row,year));
  const yearStart = market.at(`${year}-01-01`,0);
  assert.ok(yearStart,"Missing price calendar for calibration cutoff");
  const calibrateFrom = yearStart-90*DAY;
  const end = Math.max(...rows.map(row=>row.end || 0))+HOUR;
  const priceFiles = [];
  for (let y=year-1;y<=year+1;y++) {
    const name = `comed-data/five_minute/${y}.csv`, filename = path.join(ROOT,name);
    if (!fs.existsSync(filename)) continue;
    const text = fs.readFileSync(filename,"utf8"), lines = text.trimEnd().split(/\r?\n/);
    assert.equal(lines.shift(),"start_utc,start_chicago,price_cents");
    let used = 0;
    for (const line of lines) {
      // This repository's fixed three-column price feed contains no quoted or
      // embedded-comma fields. Reject any schema change rather than guessing.
      const parts = line.split(",");
      assert.equal(parts.length,3,"Unexpected five-minute price CSV schema");
      const epoch = Date.parse(parts[0])/1000;
      if (epoch < calibrateFrom || epoch >= end || !parts[2]) continue;
      const value = Number(parts[2]);
      assert.ok(Number.isFinite(epoch) && Number.isFinite(value));
      market.fiveMinute.set(epoch,value); used++;
    }
    priceFiles.push({file:name,sha256:digest(text),usedReadings:used});
  }
  // Refit the supplied live-model algorithm on the 90 days preceding this
  // scenario year. None of its calibration observations comes from the test.
  const deviations = new Map();
  for (const [t,value] of market.fiveMinute) {
    const ahead = market.dayAhead.get(Math.floor(t/HOUR)*HOUR);
    if (t>=calibrateFrom && t<yearStart && Number.isFinite(ahead)) deviations.set(t,value-ahead);
  }
  assert.ok(deviations.size>90*24*12*.9,"Insufficient prior-period live calibration data");
  market.decay = Array.from({length:24},(_,i)=>{
    let cross=0,square=0;
    for (const [t,value] of deviations) {
      const later = deviations.get(t+(i+1)*SLOT);
      if (later !== undefined) { cross+=value*later; square+=value*value; }
    }
    return square ? cross/square : 0;
  });
  return {market,rows,bundle,hashes,priceFiles,calibration:{startUtc:new Date(calibrateFrom*1000).toISOString(),
    endExclusiveUtc:new Date(yearStart*1000).toISOString(),observations:deviations.size,decay:market.decay}};
}

function emptyTotals() { return {cost:0,supply:0,delivery:0,energyKwh:0,riskKwh:0,starts:0,pauses:0}; }
function add(totals,run) {
  totals.cost+=run.totalCost; totals.supply+=run.supplyUsd; totals.delivery+=run.deliveryUsd;
  totals.energyKwh+=run.energyKwh; totals.riskKwh+=run.riskKwh; totals.starts+=run.starts; totals.pauses+=run.pauses;
}
function compare(year=2025) {
  const loaded = loadMarket(year), {market,rows} = loaded;
  const totals = new Map(), completion = new Map(), exclusions = {}, missingFeed = {sessions:0,slots:0};
  for (const tariff of TARIFFS) for (const strategy of STRATEGIES) {
    const key=`${tariff}/${strategy.id}`; totals.set(key,emptyTotals()); completion.set(key,{completed:0,shortfall:0,invalid:0});
  }
  let feasible=0,common=0,energyKwh=0,baselineCost=0,commonExcluded=0;
  const cohortIds=[];
  for (let index=0;index<rows.length;index++) {
    const row=rows[index], prepared=Lab.configuration(market,row,{tariff:"standard"});
    let reason=prepared.reason;
    if (prepared.valid) {
      const session=Engine.buildSession(market,prepared.config);
      if (!session.valid) reason=session.reason;
    }
    if (reason) { exclusions[reason]=(exclusions[reason] || 0)+1; continue; }
    const baseline=Engine.simulate(market,{...prepared.config,strategy:"charge-now",tariff:"standard"});
    if (!baseline.ready) { exclusions["Reported energy exceeds the available estimated-power capacity."]=
      (exclusions["Reported energy exceeds the available estimated-power capacity."] || 0)+1; continue; }
    feasible++;
    let missing=0;
    for(let t=prepared.config.startEpoch;t<prepared.config.endEpoch;t+=SLOT) {
      if (!market.fiveMinute.has(Engine.latestVisibleInterval(t))) missing++;
    }
    if (missing) { missingFeed.sessions++; missingFeed.slots+=missing; }
    const runs=new Map();
    for (const tariff of TARIFFS) for (const strategy of STRATEGIES) {
      const key=`${tariff}/${strategy.id}`;
      const run=tariff==="standard" && strategy.id==="charge-now" ? baseline :
        Engine.simulate(market,{...prepared.config,strategy:strategy.id,tariff});
      const status=completion.get(key);
      status[!run.valid ? "invalid" : run.ready ? "completed" : "shortfall"]++;
      if (run.ready) assert.ok(Math.abs(run.energyKwh-row.gridKwh)<=EPS,`Energy mismatch: ${row.id}, ${key}`);
      assert.ok(!run.valid || Number.isFinite(run.totalCost),`Nonfinite cost: ${row.id}, ${key}`);
      // Retain only scalar results across methods for this one session.
      run.slots=[]; delete run.session; runs.set(key,run);
    }
    if (Array.from(runs.values()).every(run=>run.valid && run.ready && Math.abs(run.energyKwh-row.gridKwh)<=EPS)) {
      common++; energyKwh+=row.gridKwh; baselineCost+=baseline.totalCost; cohortIds.push(row.id);
      for (const [key,run] of runs) add(totals.get(key),run);
    } else commonExcluded++;
    if ((index+1)%2000===0) console.error(`Compared ${index+1}/${rows.length} source sessions…`);
  }
  assert.equal(common+commonExcluded,feasible);
  assert.equal(feasible+Object.values(exclusions).reduce((a,b)=>a+b,0),rows.length);
  assert.ok(common>0,"No common completed cohort");
  const results=[];
  for (const strategy of STRATEGIES) for (const tariff of TARIFFS) {
    const key=`${tariff}/${strategy.id}`, value=totals.get(key), status=completion.get(key);
    assert.ok(Math.abs(value.energyKwh-energyKwh)<=EPS*common);
    results.push({strategy:strategy.id,label:strategy.label,tariff,commonSessions:common,totalCostUsd:value.cost,
      baselineCostUsd:baselineCost,savingUsd:baselineCost-value.cost,savingPct:(baselineCost-value.cost)/baselineCost*100,
      costPerSessionUsd:value.cost/common,deliveredKwh:value.energyKwh,riskKwh:value.riskKwh,
      startsPerSession:value.starts/common,pausesPerSession:value.pauses/common,
      completedFeasibleSessions:status.completed,shortfallFeasibleSessions:status.shortfall,
      invalidFeasibleSessions:status.invalid,completionPct:status.completed/feasible*100});
  }
  return {dataSource:"Norway Dataset1",priceYear:year,sourceReports:rows.length,feasibleSessions:feasible,
    commonSessions:common,excludedFromCommon:commonExcluded,commonEnergyKwh:energyKwh,baselineCostUsd:baselineCost,
    exclusions,missingFiveMinuteReadings:missingFeed,sourceHashes:loaded.bundle.sources,priceBundleHashes:loaded.hashes,
    fiveMinuteFiles:loaded.priceFiles,liveCalibration:loaded.calibration,
    cohortSha256:digest(cohortIds.join("\n")),
    assumptions:["Norway source month/weekday and local plug-in clock mapped to 2025 ComEd dates; original elapsed connection duration.",
      "All cost rows use the same intersection of completed sessions across all 31 strategies and both tariffs.",
      "All savings compare with immediate charging on standard delivery. Standard rows isolate timing; time-of-day rows include rate changes.",
      "Charger power is a Dataset2 estimate; actual energy and unplug deadline are known retrospectively.",
      "Missing posted intervals use the existing hold-state fallback; the live model falls back to day-ahead without a visible signal.",
      "All timers can start early to meet the deadline and have a six-hour grace after their scheduled start.",
      "Thresholds use posted supply prices, with a deadline override. Planners prefer avoiding capacity-risk hours; capacity-charge dollars are excluded.",
      "Hindsight knows settled prices in advance and retains the planners' capacity-risk preference; it is a reference, not deployable.",
      "Manual actions and custom charging-window inputs do not define a fixed autonomous strategy and are not assigned invented results.",
      "Independent scenarios, not measured Norwegian bills, simultaneous fleet demand, or annual savings."],results};
}

function write(report,directory) {
  fs.mkdirSync(directory,{recursive:true});
  const stem=path.join(directory,`norway-strategies-${report.priceYear}`);
  fs.writeFileSync(stem+".json",JSON.stringify(report,null,2)+"\n");
  const keys=Object.keys(report.results[0]);
  const quote=value=>`"${String(value).replace(/"/g,'""')}"`;
  fs.writeFileSync(stem+".csv",[keys,...report.results.map(row=>keys.map(key=>row[key]))].map(row=>row.map(quote).join(",")).join("\r\n")+"\r\n");
  const money=value=>`$${value.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2})}`;
  const lines=[`# Norway Dataset 1: every automated strategy (${report.priceYear} ComEd scenario)`,"",
    `${report.commonSessions.toLocaleString()} common completed sessions; ${report.commonEnergyKwh.toFixed(3)} kWh. Baseline: ${money(report.baselineCostUsd)}.`,"",
    "| Strategy | Standard cost | Timing saved | Time-of-day cost | Total saved |",
    "|---|---:|---:|---:|---:|"];
  for(const strategy of STRATEGIES) {
    const standard=report.results.find(row=>row.strategy===strategy.id && row.tariff==="standard");
    const tod=report.results.find(row=>row.strategy===strategy.id && row.tariff==="time-of-day");
    lines.push(`| ${strategy.label} | ${money(standard.totalCostUsd)} | ${standard.savingPct.toFixed(2)}% | ${money(tod.totalCostUsd)} | ${tod.savingPct.toFixed(2)}% |`);
  }
  lines.push("",`Of ${report.sourceReports.toLocaleString()} source reports, ${report.feasibleSessions.toLocaleString()} have valid inputs/prices and enough charging capacity. ${report.excludedFromCommon.toLocaleString()} of those are excluded from all cost rows because at least one strategy did not complete.`,"",
    "Completion rates, switching frequency, energy in capacity-risk hours, source hashes, and live calibration are in the CSV and JSON.","",
    ...report.assumptions.map(note=>`- ${note}`),"");
  fs.writeFileSync(stem+".md",lines.join("\n"));
  fs.writeFileSync(path.join(__dirname,"dist/norway-comparison-data.js"),
    "window.SPARC_NORWAY_COMPARISON="+JSON.stringify(report)+";\n");
  return stem;
}

if (require.main===module) {
  const year=Number(process.argv[2] || 2025);
  assert.equal(year,2025,"This comparison is calibrated and validated for the full 2025 scenario.");
  const report=compare(year), stem=write(report,path.resolve(process.argv[3] || path.join(ROOT,"results/charging-history")));
  console.log(JSON.stringify({files:stem+".{md,csv,json}",sourceReports:report.sourceReports,
    feasibleSessions:report.feasibleSessions,commonSessions:report.commonSessions,exclusions:report.exclusions,
    completion:report.results.filter(row=>row.completionPct<100).map(row=>({strategy:row.strategy,tariff:row.tariff,pct:row.completionPct})),
    results:report.results.map(({label,tariff,totalCostUsd,savingPct})=>({label,tariff,totalCostUsd,savingPct}))},null,2));
}
module.exports={STRATEGIES,loadMarket,compare,write};
