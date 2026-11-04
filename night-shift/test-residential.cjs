"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const Residential = require("./dist/residential-engine.js");
const Lab = require("./dist/charging-history-engine.js");
const context = {window: {}};
vm.createContext(context);
for (const file of ["simulation-data.js", "charging-history-data.js", "residential-data.js"]) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, "dist", file), "utf8"), context);
}
const bundle = context.window.SPARC_RESIDENTIAL_DATA;
const source = Residential.decode(bundle);
const market = Lab.createMarket(context.window.SPARC_SIM_DATA, context.window.SPARC_CHARGING_HISTORY);
const rows = source.map(row => Residential.adapt(market, row, 2025));
const close = (a, b) => assert.ok(Math.abs(a-b) < 1e-6, `${a} != ${b}`);
const sample = rows.find(row => !row.issue && row.startLocal.startsWith("2019-07") && row.gridKwh > 10 && row.idleHours > 5 && Lab.simulate(market, row, {tariff:"standard"}).eligible);

test("all source reports survive normalization, with joined estimates and honest missing values", () => {
  assert.equal(rows.length, 35377);
  assert.equal(new Set(rows.map(row => row.id)).size, rows.length);
  assert.equal(new Set(rows.map(row => row.userId)).size, 267);
  assert.equal(rows.filter(row => row.powerKw === null).length, 840);
  assert.equal(rows.filter(row => row.hourlyKwh !== null).length, 34537);
  for (const row of rows) {
    if (row.hourlyKwh !== null) close(row.hourlyKwh, row.gridKwh);
    assert.equal(row.loggedCost, null);
    assert.equal(row.batteryKwh, null);
    assert.equal(row.sourceTimezone, "Europe/Oslo");
  }
});

test("calendar mapping preserves month and weekday, including leap days and year edges", () => {
  for (const source of ["2020-02-29", "2018-01-01", "2019-12-31", "2021-08-05"]) {
    for (const year of [2025, 2026]) {
      const mapped = Residential.scenarioDate(source, year);
      assert.equal(mapped.slice(0,4),String(year));
      assert.equal(mapped.slice(5,7),source.slice(5,7));
      assert.equal(new Date(mapped+"T12:00:00Z").getUTCDay(),new Date(source+"T12:00:00Z").getUTCDay());
    }
  }
  assert.throws(() => Residential.adapt(market, source[0], 2020), /supported/);
});

test("scenario clocks and elapsed durations are explicit and boundary capacity is exact", () => {
  assert.ok(sample);
  close(sample.end-sample.start,sample.sourceEnd-sample.sourceStart);
  const prepared = Lab.configuration(market,sample,{});
  assert.ok(prepared.config.startEpoch <= sample.start);
  assert.ok(prepared.config.endEpoch >= sample.end);
  close(prepared.config.availabilityStartEpoch,sample.start);
  close(prepared.config.availabilityEndEpoch,sample.end);
  assert.equal(prepared.config.date,sample.modeledDate);
  assert.equal(prepared.config.startEpoch%300,0);
  assert.equal(prepared.config.endEpoch%300,0);
  close(prepared.config.power,sample.powerKw);
  close(prepared.config.gridEnergyKwh,sample.gridKwh);
  assert.equal(prepared.powerAdjusted,false);
  assert.equal(Lab.configuration(market,sample,{window:"morning"}).valid,false);
});

test("source and mapped daylight-saving uncertainties are excluded rather than guessed", () => {
  const ambiguous = rows.find(row => row.issue?.includes("Europe/Oslo"));
  assert.ok(ambiguous);
  assert.equal(Lab.simulate(market,ambiguous,{}).valid,false);
  for (const startLocal of ["2019-03-10 02:30:00", "2019-11-03 01:30:00"]) {
    const mapped = Residential.adapt(market,{...source[0],issue:null,startLocal},2025);
    assert.match(mapped.issue,/Chicago/);
    assert.equal(Lab.simulate(market,mapped,{}).valid,false);
  }
});

test("missing boundaries stay missing and missing power requires an explicit what-if", () => {
  const missing = rows.find(row => row.sourceEnd === null);
  assert.equal(missing.end,null);
  assert.equal(Lab.simulate(market,missing,{}).valid,false);
  const noPower = {...sample,powerKw:null};
  assert.equal(Lab.simulate(market,noPower,{}).valid,false);
  const explicit = Lab.simulate(market,noPower,{powerKw:11.5});
  assert.equal(explicit.eligible,true);
});

test("derived SoC, capacity, and idle-time estimates never alter the requested energy", () => {
  const original = Lab.simulate(market,sample,{tariff:"standard"});
  const edited = Lab.simulate(market,{...sample,predictedStartSoc:1,predictedSocDiff:99,capacityKwh:1000,idleHours:999,chargingHours:0},{tariff:"standard"});
  assert.equal(original.eligible,true);
  close(original.saving,edited.saving);
  for (const track of original.tracks) close(track.result.energyKwh,sample.gridKwh);
  assert.match(original.tracks[0].label,/modeled/);
  close(original.tracks[0].result.totalCost,original.tracks[1].result.totalCost);
});

test("insufficient predicted power reports a shortfall and is never silently increased", () => {
  const run = Lab.simulate(market,{...sample,powerKw:.1},{});
  assert.equal(run.valid,true);
  assert.equal(run.prepared.powerAdjusted,false);
  close(run.config.power,.1);
  assert.equal(run.eligible,false);
  assert.equal(run.saving,null);
  assert.equal(Lab.summarize([run]).shortfall,1);
});

test("short connections retain partial-slot capacity without inventing connected time", () => {
  const start = market.at("2025-05-12",20)+60;
  const row = {...sample,start,end:start+180,gridKwh:.18,powerKw:3.6,modeledDate:"2025-05-12"};
  const run = Lab.simulate(market,row,{tariff:"standard"});
  assert.equal(run.eligible,true);
  close(run.session.availableSeconds,180);
  for (const track of run.tracks) {
    assert.equal(track.result.ready,true);
    close(track.result.energyKwh,.18);
    for (const slot of track.result.slots) {
      assert.ok(slot.availableStart>=start);
      assert.ok(slot.availableEnd<=start+180);
      assert.ok(slot.energyKwh<=3.6*(slot.availableEnd-slot.availableStart)/3600+1e-9);
    }
  }
  const bad = {...run.config,availabilityStartEpoch:NaN};
  assert.equal(require("./dist/simulation-engine.js").buildSession(market,bad).valid,false);
});

test("the planner accounts for limited capacity in the cheapest boundary interval", () => {
  const Engine = require("./dist/simulation-engine.js");
  const start = market.at("2025-05-12",20)+120;
  const row = {...sample,start,end:market.at("2025-05-12",21)+60,gridKwh:3,powerKw:3.6,modeledDate:"2025-05-12"};
  const run = Lab.simulate(market,row,{tariff:"standard"});
  assert.equal(run.eligible,true);
  for (const id of ["charge-now","day-ahead","hindsight"]) {
    const result = Engine.simulate(market,{...run.config,strategy:id});
    assert.equal(result.ready,true);
    close(result.energyKwh,3);
    for (const slot of result.slots) assert.ok(slot.energyKwh<=3.6*(slot.availableEnd-slot.availableStart)/3600+1e-9);
  }
});

test("exports distinguish source dates, scenario dates, and supplied estimates", () => {
  const run = Lab.simulate(market,sample,{tariff:"standard"});
  const csv = Lab.exportCsv([run],{tariff:"standard"});
  assert.match(csv,/source_plugin_local/);
  assert.match(csv,/"Europe\/Oslo"/);
  assert.match(csv,/"Dataset2 estimate"/);
  assert.match(csv,/"2025"/);
  assert.match(csv,new RegExp(sample.startLocal));
  assert.match(csv,/predicted_start_soc_pct/);
  const daily = Lab.exportDailyCsv([run],{});
  assert.ok(daily.startsWith('"source_plugin_date_oslo"'));
  assert.match(daily,/mapped ComEd scenario costs/);
});

test("representative sessions across all locations conserve energy and aggregate correctly", () => {
  const chosen = new Map();
  for (const row of rows) {
    if (chosen.has(row.site) || row.issue || row.idleHours < 1) continue;
    const run = Lab.simulate(market,row,{tariff:"standard"});
    if (run.eligible) chosen.set(row.site,run);
  }
  assert.equal(chosen.size,12);
  const results = Array.from(chosen.values());
  const summary = Lab.summarize(results);
  close(summary.baselineCost-summary.sparcCost,summary.saving);
  const last = Lab.dailyRows(results).at(-1);
  close(last.cumulativeSaving,summary.saving);
  for (const run of results) {
    for (const track of run.tracks) {
      if (track.result.ready) close(track.result.energyKwh,run.row.gridKwh);
      assert.ok(track.result.slots.every(slot => slot.availableStart >= run.row.start && slot.availableEnd <= run.row.end));
      for (const slot of track.result.slots) assert.ok(slot.energyKwh <= run.config.power*(slot.availableEnd-slot.availableStart)/3600+1e-9);
    }
  }
});
