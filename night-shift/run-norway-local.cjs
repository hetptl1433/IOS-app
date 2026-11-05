/* Reproduce the original-date Norway/Norway comparison and its offline page. */
"use strict";
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm"),assert=require("node:assert/strict");
const Replay=require("./dist/norway-local-engine.js");
const filename=path.join(__dirname,"dist/norway-local-data.js"),context={window:{}};
vm.createContext(context);vm.runInContext(fs.readFileSync(filename,"utf8"),context);
const data=context.window.SPARC_NORWAY_LOCAL_DATA,market=Replay.createMarket(data),runs=[];
for(let index=0;index<data.rows.length;index++){
  const run=Replay.compareSession(market,data.rows[index]);
  for(const track of run.tracks){
    if(!track.available || !track.result)continue;
    if(run.eligible){assert.ok(track.result.ready);assert.ok(Math.abs(track.result.energyKwh-run.row.gridKwh)<=1e-6);assert.ok(Number.isFinite(track.result.costNok));}
    track.result.slots=[];
  }
  runs.push(run);
  if((index+1)%5000===0)console.error(`Compared ${index+1}/${data.rows.length} original Norway sessions…`);
}
const summary=Replay.summarize(runs);
const shortfalls=runs.filter(run=>run.valid && !run.eligible).map(run=>({sessionId:run.row.id,area:run.row.area,
  requiredKwh:run.row.gridKwh,connectedCapacityKwh:run.row.powerKw*(run.row.end-run.row.start)/3600,
  completed:run.tracks.filter(track=>track.available && track.result.ready).map(track=>track.id),
  missed:run.tracks.filter(track=>track.available && !track.result.ready).map(track=>track.id)}));
assert.equal(summary.comparable+summary.skipped+summary.shortfall,data.rows.length);
for(const result of summary.results){if(result.available){assert.equal(result.completedSessions,summary.comparable);assert.ok(Math.abs(summary.baselineCostNok-result.costNok-result.savingNok)<1e-6);}}
data.historySummary=summary;
data.generatedAt=new Date().toISOString();
fs.writeFileSync(filename,"window.SPARC_NORWAY_LOCAL_DATA="+JSON.stringify(data)+";\n");
const out=path.join(__dirname,"../results/charging-history");fs.mkdirSync(out,{recursive:true});
const report={dataSource:data.dataSource,timezone:data.timezone,currency:data.currency,costScope:data.costScope,
  sourceDateRange:data.coverage,priceCoverage:data.priceCoverage,provenance:data.provenance,generatedAt:data.generatedAt,summary,shortfalls};
fs.writeFileSync(path.join(out,"norway-local-history.json"),JSON.stringify(report,null,2)+"\n");
const columns=["strategy","label","available","reason","sessions","cost_nok","saved_nok","saved_pct","energy_kwh"];
const quote=value=>`"${String(value ?? "").replace(/"/g,'""')}"`;
const csv=[columns,...summary.results.map(result=>[result.id,result.label,result.available,result.reason || "",result.completedSessions,result.costNok,result.savingNok,result.savingPct,result.available ? summary.energyKwh : ""])].map(row=>row.map(quote).join(",")).join("\r\n");
fs.writeFileSync(path.join(out,"norway-local-strategies.csv"),csv+"\r\n");
console.log(JSON.stringify({total:summary.total,comparable:summary.comparable,skipped:summary.skipped,shortfall:summary.shortfall,
  energyKwh:summary.energyKwh,baselineCostNok:summary.baselineCostNok,exclusions:summary.exclusions,
  main:summary.results.filter(result=>["charge-now","cap-25","cap-50","cap-100","day-ahead","live","hindsight"].includes(result.id))},null,2));
