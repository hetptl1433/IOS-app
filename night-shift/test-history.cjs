"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const Engine = require("./dist/simulation-engine.js");
const Lab = require("./dist/charging-history-engine.js");
const context = {window: {}};
vm.createContext(context);
for (const name of ["simulation-data.js", "charging-history-data.js"]) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, "dist", name), "utf8"), context);
}
const data = context.window.SPARC_SIM_DATA;
const history = context.window.SPARC_CHARGING_HISTORY;
const market = Lab.createMarket(data, history);
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-6, `${actual} != ${expected}`);
const sample = history.rows.find(row => row.startLocal.startsWith("2025-05") && row.gridKwh > 15);

test("the bundle includes every source record, chronologically, with unique IDs", () => {
  const source = fs.readFileSync(path.join(__dirname, "../charging-history-data/charging_sessions_tm1.csv"), "utf8");
  assert.equal(history.rows.length, source.trim().split(/\r?\n/).length - 1);
  assert.equal(new Set(history.rows.map(row => row.id)).size, history.rows.length);
  for (let i = 1; i < history.rows.length; i++) assert.ok(history.rows[i].start >= history.rows[i-1].start);
});

test("2024 history uses real supplemental prices and grid energy without another loss factor", () => {
  const row = history.rows[0];
  const run = Lab.simulate(market, row, {});
  assert.equal(run.valid, true, run.reason);
  assert.equal(run.eligible, true);
  for (const track of run.tracks) {
    assert.equal(track.result.valid, true, track.result.reason);
    assert.equal(track.result.ready, true);
    close(track.result.energyKwh, row.gridKwh);
  }
});

test("the baseline uses standard delivery and the plans use the selected delivery rate", () => {
  const run = Lab.simulate(market, sample, {tariff:"time-of-day"});
  assert.equal(run.eligible, true);
  for (const track of run.tracks) {
    close(track.result.totalCost, track.result.slots.reduce((sum, slot) => sum + slot.energyKwh * (slot.billedPrice + slot.deliveryCents) / 100, 0));
    assert.equal(track.result.session.config.tariff, track.id === "charge-now" ? "standard" : "time-of-day");
    assert.equal(track.tariff, track.result.session.config.tariff);
    close(track.result.energyKwh, sample.gridKwh);
  }
  close(run.saving, run.tracks[1].result.totalCost - run.tracks[3].result.totalCost);
});

test("time-of-day is the default and changing the plans' rate preserves the standard baseline", () => {
  const defaultRun = Lab.simulate(market, sample, {});
  const standard = Lab.simulate(market, sample, {tariff:"standard"});
  const baseline = run => run.tracks.find(track => track.id === "charge-now").result;
  assert.equal(defaultRun.config.tariff,"time-of-day");
  assert.equal(standard.config.tariff,"standard");
  close(baseline(defaultRun).totalCost,baseline(standard).totalCost);
  assert.deepEqual(baseline(defaultRun).slots,baseline(standard).slots);
  const expectedBase = Engine.simulate(market,{...defaultRun.config,strategy:"charge-now",tariff:"standard"});
  const expectedPlan = Engine.simulate(market,{...defaultRun.config,strategy:"day-ahead",tariff:"time-of-day"});
  close(defaultRun.saving,expectedBase.totalCost-expectedPlan.totalCost);
});

test("the recorded CSV cost does not enter the savings comparison", () => {
  const original = Lab.simulate(market,sample,{});
  const edited = Lab.simulate(market,{...sample,loggedCost:999999},{});
  close(original.saving,edited.saving);
  close(Lab.summarize([original]).baselineCost,Lab.summarize([edited]).baselineCost);
  close(Lab.summarize([original]).sparcCost,Lab.summarize([edited]).sparcCost);
});

test("recorded pattern distributes the recorded grid energy only inside its recorded window", () => {
  const run = Lab.simulate(market, sample, {window:"morning"});
  const recorded = run.tracks.find(track => track.id === "recorded").result;
  close(recorded.energyKwh, sample.gridKwh);
  assert.ok(recorded.slots.filter(slot => slot.time >= run.prepared.recordedEnd).every(slot => slot.energyKwh === 0));
});

test("morning flexibility is capped by the next chronological record", () => {
  const row = {...sample, start:market.at("2025-05-12", 1), end:market.at("2025-05-12", 2), startLocal:"2025-05-12 01:00:00", gridKwh:5, powerKw:11};
  const next = {start:market.at("2025-05-12", 3)};
  const prepared = Lab.configuration(market, row, {window:"morning", departureHour:7}, next);
  assert.equal(prepared.valid, true);
  assert.equal(prepared.config.endEpoch, next.start);
  assert.equal(prepared.capped, true);
  const earlyDeparture = Lab.configuration(market, {...row, end:market.at("2025-05-12", 8)}, {window:"morning", departureHour:7});
  assert.equal(earlyDeparture.config.endEpoch, market.at("2025-05-12", 8));
});

test("a slower charger reports shortfall and contributes no savings", () => {
  const run = Lab.simulate(market, sample, {powerKw: .1});
  assert.equal(run.valid, true);
  assert.equal(run.eligible, false);
  assert.equal(run.saving, null);
  const summary = Lab.summarize([run]);
  assert.equal(summary.comparable, 0);
  assert.equal(summary.shortfall, 1);
  assert.equal(summary.saving, 0);
  assert.equal(summary.energyKwh, 0);
});

test("ambiguous daylight-saving records and missing prices are explicitly skipped", () => {
  const ambiguous = history.rows.find(row => row.issue);
  assert.ok(ambiguous);
  assert.equal(Lab.simulate(market, ambiguous, {}).valid, false);
  const incomplete = Lab.createMarket(data, history);
  incomplete.hourly.delete(Math.floor(sample.start / 3600) * 3600);
  const run = Lab.simulate(incomplete, sample, {});
  assert.equal(run.valid, false);
  assert.match(run.reason, /Incomplete historical prices/);
  assert.equal(Lab.summarize([run]).skipped, 1);
});

test("peak readings inconsistent with energy are raised and flagged", () => {
  const row = {...sample, powerKw: .1};
  const prepared = Lab.configuration(market, row, {});
  assert.equal(prepared.powerAdjusted, true);
  assert.ok(prepared.config.power > row.powerKw);
  assert.equal(Lab.simulate(market, row, {}).eligible, true);
});

test("explicit dates preserve repeated DST hours and reject invalid boundaries", () => {
  const row = history.rows.find(row => !row.issue && row.startLocal.startsWith("2025-10"));
  const config = Lab.configuration(market, row, {}).config;
  assert.equal(Engine.buildSession(market, {...config, startEpoch:NaN}).valid, false);
  assert.equal(Engine.buildSession(market, {...config, endEpoch:undefined}).valid, false);
  const first = market.at("2025-11-02", 1);
  const repeated = Engine.buildSession(market, {...config, date:"2025-11-02", startEpoch:first+3600, endEpoch:first+7200});
  assert.equal(repeated.start, first+3600);
  assert.equal(repeated.end, first+7200);
});

test("legacy simulator configurations still produce the same dated charging window", () => {
  const config = {date:"2025-05-12", vehicle:"cybertruck", arrive:20, depart:7, gridEnergyKwh:15, power:11.5, tariff:"standard"};
  const ordinary = Engine.simulate(market, config);
  const explicit = Engine.simulate(market, {...config, startEpoch:market.at("2025-05-12",20), endEpoch:market.at("2025-05-13",7)});
  assert.equal(ordinary.valid, true);
  assert.equal(explicit.valid, true);
  close(ordinary.totalCost, explicit.totalCost);
  assert.deepEqual(ordinary.slots, explicit.slots);
});

test("scheduled charging honors exact start and stop times across midnight", () => {
  const config = {date:"2025-05-12", vehicle:"cybertruck", arrive:20, depart:7,
    gridEnergyKwh:100, power:11.5, strategy:"schedule", chargeStartHour:23+5/60, chargeStopHour:2+10/60};
  const run = Engine.simulate(market, config);
  assert.equal(run.valid, true, run.reason);
  assert.equal(run.ready, false);
  const start = market.at("2025-05-12",23) + 300;
  const stop = market.at("2025-05-13",2) + 600;
  const active = run.slots.filter(slot => slot.energyKwh > 0);
  assert.equal(active[0].time, start);
  assert.equal(active.at(-1).time, stop-300);
  assert.ok(run.slots.filter(slot => slot.time < start || slot.time >= stop).every(slot => slot.energyKwh === 0));
  close(run.energyKwh, (stop-start)/3600*11.5);
});

test("scheduled charging clips to plug-in/departure and stops at the target", () => {
  const config = {date:"2025-05-12", vehicle:"cybertruck", arrive:20, depart:7,
    gridEnergyKwh:100, power:11.5, strategy:"schedule", chargeStartHour:18, chargeStopHour:22};
  const sameDay = Engine.simulate(market, config);
  assert.equal(sameDay.valid, true);
  close(sameDay.energyKwh, 2*11.5);
  const departure = Engine.simulate(market, {...config,chargeStartHour:5,chargeStopHour:9});
  close(departure.energyKwh, 2*11.5);
  const target = Engine.simulate(market, {...config,gridEnergyKwh:3});
  assert.equal(target.ready, true);
  close(target.energyKwh, 3);
  assert.ok(target.slots.filter(slot => slot.time >= market.at("2025-05-12",21)).every(slot => slot.energyKwh === 0));
});

test("scheduled charging rejects missing, equal, and off-grid times", () => {
  const config = {date:"2025-05-12",gridEnergyKwh:15,strategy:"schedule"};
  for (const values of [{chargeStartHour:NaN},{chargeStopHour:24},{chargeStartHour:2,chargeStopHour:2},{chargeStartHour:1+1/60}]) {
    assert.equal(Engine.simulate(market,{...config,...values}).valid,false);
  }
});

test("a separate charging window preserves the plug-in timeline and constrains every strategy", () => {
  const config = {date:"2025-05-12", vehicle:"cybertruck", arrive:20, depart:7,
    gridEnergyKwh:40, power:11.5, reserveMinutes:0, chargeWindowEnabled:true,
    chargeStartHour:0, chargeStopHour:6};
  const start = market.at("2025-05-13",0), stop = market.at("2025-05-13",6);
  for (const strategy of ["charge-now","timer-0100","cap-0","day-ahead","live","hindsight","manual"]) {
    const run = Engine.simulate(market,{...config,strategy},{manual:() => true});
    assert.equal(run.valid,true,`${strategy}: ${run.reason}`);
    assert.equal(run.start,market.at("2025-05-12",20));
    assert.equal(run.end,market.at("2025-05-13",7));
    assert.equal(run.session.availableSeconds,6*3600);
    assert.ok(run.slots.filter(slot => slot.time < start || slot.time >= stop).every(slot => slot.energyKwh === 0),strategy);
    assert.equal(run.ready,true,strategy);
    close(run.energyKwh,40);
  }
});

test("SPARC plans from the allowed hours instead of scheduling inaccessible cheap hours", () => {
  const constrainedMarket = Lab.createMarket(data,history);
  for (const [day,hour,price] of [["2025-05-12",22,-1000],["2025-05-13",3,-500]]) {
    const epoch = constrainedMarket.at(day,hour);
    constrainedMarket.hours.get(epoch).dayAhead = price;
    constrainedMarket.dayAhead.set(epoch,price);
    constrainedMarket.hourly.set(epoch,price);
  }
  const run = Engine.simulate(constrainedMarket,{date:"2025-05-12",arrive:20,depart:7,
    gridEnergyKwh:11.5,power:11.5,reserveMinutes:0,strategy:"day-ahead",chargeWindowEnabled:true,
    chargeStartHour:0,chargeStopHour:6});
  assert.equal(run.valid,true);
  assert.equal(run.ready,true);
  const active = run.slots.filter(slot => slot.energyKwh > 0);
  assert.equal(active[0].time,constrainedMarket.at("2025-05-13",3));
  assert.equal(active.at(-1).time,constrainedMarket.at("2025-05-13",4)-300);
});

test("short charging windows cause shortfalls without charging before start or after stop", () => {
  for (const strategy of ["charge-now","timer-0100","cap-0","day-ahead","live","manual"]) {
    const run = Engine.simulate(market,{date:"2025-05-12",arrive:20,depart:7,gridEnergyKwh:80,
      power:11.5,strategy,chargeWindowEnabled:true,chargeStartHour:0,chargeStopHour:6},{manual:() => true});
    assert.equal(run.valid,true);
    assert.equal(run.ready,false,strategy);
    close(run.energyKwh,69);
  }
});

test("charging-window validation and disabled controls behave independently of strategy", () => {
  const config = {date:"2025-05-12",gridEnergyKwh:15,strategy:"day-ahead",chargeWindowEnabled:true};
  for (const values of [{chargeStartHour:NaN},{chargeStartHour:3,chargeStopHour:3},{chargeStopHour:6+1/60}]) {
    assert.equal(Engine.buildSession(market,{...config,...values}).valid,false);
  }
  const outside = Engine.buildSession(market,{...config,chargeStartHour:10,chargeStopHour:12});
  assert.equal(outside.valid,false);
  assert.match(outside.reason,/does not overlap/);
  assert.equal(Engine.simulate(market,{...config,chargeWindowEnabled:false,chargeStartHour:NaN}).ready,true);
});

test("charging windows follow Chicago daylight-saving time", () => {
  const run = Engine.simulate(market,{date:"2025-03-08",arrive:20,depart:7,gridEnergyKwh:100,
    power:11.5,strategy:"charge-now",chargeWindowEnabled:true,chargeStartHour:0,chargeStopHour:4});
  assert.equal(run.valid,true,run.reason);
  assert.equal(run.session.availableSeconds,3*3600);
  close(run.energyKwh,3*11.5);
});

test("the full history preserves equal delivered energy and honest aggregate totals", () => {
  const results = history.rows.map((row, i) => Lab.simulate(market, row, {}, history.rows[i+1]));
  const summary = Lab.summarize(results);
  assert.equal(summary.total, 2155);
  assert.ok(summary.comparable > 2000);
  assert.equal(summary.comparable + summary.skipped + summary.shortfall, summary.total);
  close(summary.baselineCost-summary.sparcCost, summary.saving);
  // Fixed source-data regression: standard immediate versus time-of-day SPARC.
  close(summary.baselineCost,747.4704790506762);
  close(summary.sparcCost,515.9552147050971);
  for (const run of results.filter(result => result.eligible)) {
    close(run.tracks[1].result.energyKwh, run.tracks[3].result.energyKwh);
    assert.ok(Number.isFinite(run.saving));
  }
  console.log("Recorded-window totals:", JSON.stringify(summary));
});

test("CSV export preserves precision and distinguishes skipped and shortfall results", () => {
  const complete = Lab.simulate(market, sample, {});
  const shortfall = Lab.simulate(market, sample, {powerKw:.1});
  const skipped = Lab.simulate(market, {...sample, issue:'Missing data, with a "quote".'}, {});
  const csv = Lab.exportCsv([complete, shortfall, skipped], {});
  const lines = csv.split("\r\n");
  assert.equal(lines.length, 4);
  assert.match(lines[0], /modeled_start_utc/);
  assert.match(lines[0], /sparc_delivered_kwh/);
  assert.match(lines[0], /"baseline_tariff","planned_tariff"/);
  assert.match(lines[1], /"standard","time-of-day"/);
  assert.match(lines[1], /"compared"/);
  assert.match(lines[1], new RegExp(complete.saving.toFixed(6)));
  assert.match(lines[2], /"shortfall"/);
  assert.match(lines[3], /"skipped"/);
  assert.match(lines[3], /Missing data, with a ""quote""/);
});

test("full morning scenarios retain equal energy and cap extensions before the next record", () => {
  for (const tariff of ["standard","time-of-day"]) {
    const results = history.rows.map((row, i) => Lab.simulate(market, row, {window:"morning",tariff}, history.rows[i+1]));
    const summary = Lab.summarize(results);
    assert.ok(summary.comparable > 2000);
    close(summary.baselineCost-summary.sparcCost, summary.saving);
    results.forEach((run, i) => {
      if (!run.valid || !run.prepared.extended || !history.rows[i+1]) return;
      assert.ok(run.config.endEpoch <= Math.floor(history.rows[i+1].start / 300) * 300);
    });
    console.log(`Morning-window totals (${tariff}):`, JSON.stringify(summary));
  }
});

function dailyFixture(day, baselineCost, sparcCost) {
  return {valid:true,eligible:true,row:{startLocal:day+" 23:50:00",gridKwh:1,loggedCost:0},
    config:{tariff:"time-of-day"},saving:baselineCost-sparcCost,
    tracks:[{id:"charge-now",result:{totalCost:baselineCost}},{id:"day-ahead",result:{totalCost:sparcCost}}]};
}

test("daily savings group sessions by Chicago start date, fill gaps, and use cost-weighted percentages", () => {
  const results = [dailyFixture("2025-05-03",90,81),dailyFixture("2025-05-01",4,2),dailyFixture("2025-05-01",6,3)];
  const days = Lab.dailyRows(results);
  assert.deepEqual(days.map(day=>day.date),["2025-05-01","2025-05-02","2025-05-03"]);
  assert.equal(days[0].comparable,2);
  close(days[0].savingPct,50);
  close(days[2].savingPct,10);
  assert.equal(days[1].status,"no_sessions");
  assert.equal(days[1].baselineCost,0);
  assert.equal(days[1].savingPct,null);
  close(days[2].cumulativeSaving,14);
  close(days[2].cumulativeSavingPct,14);
  assert.notEqual(days[2].cumulativeSavingPct,(days[0].savingPct+days[2].savingPct)/2);
});

test("daily exports distinguish unpriced sessions, shortfalls, and days without charging", () => {
  const valid = dailyFixture("2025-05-01",10,12);
  const skipped = {valid:false,eligible:false,row:{startLocal:"2025-05-02 01:00:00"},tracks:[]};
  const shortfall = {...dailyFixture("2025-05-03",1,0),eligible:false,saving:null};
  const days = Lab.dailyRows([valid,skipped,shortfall],{startDate:"2025-05-01",endDate:"2025-05-04"});
  assert.equal(days[1].skipped,1);
  assert.equal(days[1].baselineCost,null);
  assert.equal(days[1].saving,null);
  assert.equal(days[2].shortfall,1);
  assert.equal(days[2].sparcCost,null);
  assert.equal(days[3].status,"no_sessions");
  assert.equal(days[3].baselineCost,0);
  close(days[3].cumulativeSavingPct,-20);
  const lines = Lab.exportDailyCsv([valid,skipped,shortfall],{endDate:"2025-05-04"}).split("\r\n");
  assert.match(lines[0],/"saving_pct"/);
  assert.match(lines[0],/"cumulative_saving_pct"/);
  assert.match(lines[1],/"-2.000000","-20.000000"/);
  assert.match(lines[2],/^"2025-05-02","","","",""/);
  assert.match(lines[4],/^"2025-05-04","0.000000","0.000000","0.000000",""/);
});

test("the daily CSV reconciles to the complete history and its 31 percent headline", () => {
  const results = history.rows.map((row,i)=>Lab.simulate(market,row,{},history.rows[i+1]));
  const summary = Lab.summarize(results);
  const days = Lab.dailyRows(results);
  const last = days.at(-1);
  assert.equal(days[0].date,"2024-09-17");
  assert.equal(last.date,"2026-10-14");
  assert.equal(days.reduce((sum,day)=>sum+day.total,0),summary.total);
  assert.equal(days.reduce((sum,day)=>sum+day.comparable,0),summary.comparable);
  assert.equal(days.reduce((sum,day)=>sum+day.skipped,0),summary.skipped);
  close(days.reduce((sum,day)=>sum+(day.baselineCost||0),0),summary.baselineCost);
  close(days.reduce((sum,day)=>sum+(day.sparcCost||0),0),summary.sparcCost);
  close(last.cumulativeSaving,summary.saving);
  close(last.cumulativeSavingPct,summary.saving/summary.baselineCost*100);
  close(last.cumulativeSavingPct,30.973164938850122);
  assert.equal(Lab.exportDailyCsv(results,{}).split("\r\n").length,days.length+1);
});
