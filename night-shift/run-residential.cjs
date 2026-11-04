/* Reproduce the complete independent-session ComEd scenario without a browser. */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const crypto = require("node:crypto");
const Lab = require("./dist/charging-history-engine.js");
const Residential = require("./dist/residential-engine.js");
const year = Number(process.argv[2] || 2025);
const directory = path.resolve(process.argv[3] || path.join(__dirname,"../results/charging-history"));
const context = {window:{}};
const priceDataHashes = {};
vm.createContext(context);
for (const name of ["simulation-data.js","charging-history-data.js","residential-data.js"]) {
  const text = fs.readFileSync(path.join(__dirname,"dist",name),"utf8");
  vm.runInContext(text,context);
  if (name !== "residential-data.js") priceDataHashes[name] = crypto.createHash("sha256").update(text).digest("hex");
}
const market = Lab.createMarket(context.window.SPARC_SIM_DATA,context.window.SPARC_CHARGING_HISTORY);
const rows = Residential.decode(context.window.SPARC_RESIDENTIAL_DATA).map(row => Residential.adapt(market,row,year));
const results = [], reasons = {}, byLocation = new Map(), shortfalls = [];
for (const row of rows) {
  const run = Lab.simulate(market,row,{tariff:"standard"});
  if (run.eligible) {
    for (const id of ["charge-now","day-ahead"]) {
      const outcome = run.tracks.find(track => track.id === id).result;
      if (Math.abs(outcome.energyKwh-row.gridKwh)>1e-6 || !Number.isFinite(outcome.totalCost)) {
        throw new Error(`Energy or cost reconciliation failed for ${row.id}`);
      }
    }
  }
  if (!run.valid) reasons[run.reason] = (reasons[run.reason] || 0) + 1;
  if (run.valid && !run.eligible) {
    const immediate = run.tracks.find(track => track.id === "charge-now").result;
    const sparc = run.tracks.find(track => track.id === "day-ahead").result;
    shortfalls.push({sessionId:row.id,requiredKwh:row.gridKwh,powerKw:run.config.power,
      connectedCapacityKwh:run.config.power*(row.end-row.start)/3600,
      immediateKwh:immediate.energyKwh,sparcKwh:sparc.energyKwh});
  }
  for (const track of run.tracks) { track.result.slots = []; delete track.result.session; }
  delete run.session;
  results.push(run);
  if (!byLocation.has(row.site)) byLocation.set(row.site,[]);
  byLocation.get(row.site).push(run);
}
const summary = Lab.summarize(results);
const report = {dataSource:"Dataset1",priceYear:year,tariff:"standard",power:"Dataset2 estimated user power",
  scenario:"Independent sessions; nearest same weekday within source month; Chicago plug-in clock; original elapsed duration",
  interpretation:"Retrospective scheduling with known energy and unplug deadline. Not Norwegian bills, fleet load, or an annual forecast.",
  sourceHashes:context.window.SPARC_RESIDENTIAL_DATA.sources,priceDataHashes,summary,
  savingPct:summary.baselineCost > 0 ? summary.saving/summary.baselineCost*100 : null,exclusionReasons:reasons,shortfalls,
  byLocation:Object.fromEntries(Array.from(byLocation,([site,runs])=>[site,Lab.summarize(runs)]))};
fs.mkdirSync(directory,{recursive:true});
fs.writeFileSync(path.join(directory,`residential-scenario-${year}.json`),JSON.stringify(report,null,2)+"\n");
fs.writeFileSync(path.join(directory,`residential-daily-${year}.csv`),Lab.exportDailyCsv(results,{tariff:"standard"})+"\r\n");
console.log(JSON.stringify({summary,savingPct:report.savingPct,exclusionReasons:reasons},null,2));
