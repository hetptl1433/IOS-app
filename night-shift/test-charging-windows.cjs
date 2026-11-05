"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const crypto = require("node:crypto");
const Engine = require("./dist/simulation-engine.js");
const History = require("./dist/charging-history-engine.js");
const Windows = require("./dist/charging-window-engine.js");
const context = {window: {}};
vm.createContext(context);
for (const name of ["simulation-data.js", "charging-history-data.js"]) {
  vm.runInContext(fs.readFileSync(path.join(__dirname, "dist", name), "utf8"), context);
}
const data = context.window.SPARC_SIM_DATA, source = context.window.SPARC_CHARGING_HISTORY;
const market = History.createMarket(data, source);
const sample = source.rows.find(row => row.startLocal.startsWith("2025-05") && row.gridKwh > 15);
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-7, `${actual} != ${expected}`);

test("the original and fixed windows retain identical energy and power with one original-time baseline", () => {
  const result = Windows.compareSession(market, sample);
  assert.equal(result.comparable, true);
  const prepared = History.configuration(market, sample, {}).config;
  close(result.energyKwh, sample.gridKwh);
  close(result.powerKw, prepared.power);
  close(result.fixedPowerKw, prepared.power);
  close(result.originalHourly.reduce((a,b) => a+b), sample.gridKwh);
  close(result.fixedHourly.reduce((a,b) => a+b), sample.gridKwh);
  close(result.baselineCost, Engine.simulate(market, {...prepared, strategy: "charge-now", tariff: "standard"}).totalCost);
  for (let h = 8; h < 20; h++) close(result.fixedHourly[h], 0);
});

test("8 p.m. to 8 a.m. follows local daylight-saving transitions", () => {
  // Isolate clock behavior from real missing-price exclusions on transition dates.
  const fixturePrices = hours => hours.map(hour => {
    const copy = [...hour]; copy[3] = 1; copy[4] = 1; return copy;
  });
  const transitionMarket = History.createMarket({...data, hours:fixturePrices(data.hours)},
    {...source, hours:fixturePrices(source.hours)});
  for (const [day, duration] of [["2025-03-08", 11], ["2025-11-01", 13], ["2025-05-12", 12]]) {
    const row = {...sample, startLocal: `${day} 01:00:00`, start: transitionMarket.at(day,1), end: transitionMarket.at(day,2), gridKwh: 5};
    const result = Windows.compareSession(transitionMarket, row);
    assert.equal(result.comparable, true, result.reason);
    close((result.endEpoch-result.startEpoch)/3600, duration);
    close(result.energyKwh, 5);
  }
});

test("missing fixed-window prices, ambiguous source times and shortfalls never count as savings", async () => {
  const missing = History.createMarket(data, source);
  missing.hourly.delete(missing.at(sample.startLocal.slice(0,10), 21));
  const priceGap = Windows.compareSession(missing, sample);
  assert.equal(priceGap.valid, false);
  assert.match(priceGap.reason, /Incomplete historical prices/);
  const uncertain = source.rows.find(row => row.issue);
  const summary = await Windows.compareHistory(market, [uncertain], {yieldFrame: async () => {}});
  assert.equal(summary.compared, 0);
  assert.equal(summary.skipped, 1);
  assert.equal(summary.originalPct, null);
  assert.equal(summary.fixedPct, null);
  const long = {...sample, gridKwh: 200, powerKw: 11, start:market.at("2025-05-12",0), end:market.at("2025-05-13",23), startLocal:"2025-05-12 00:00:00"};
  const shortfall = await Windows.compareHistory(market, [long], {yieldFrame:async () => {}});
  assert.equal(shortfall.shortfall, 1);
  assert.equal(shortfall.energyKwh, 0);
  assert.equal(shortfall.compared, 0);
});

test("filter changes cancel an in-progress comparison without returning partial totals", async () => {
  let cancelled = false;
  const result = await Windows.compareHistory(market, [sample, sample], {chunkSize:1,
    cancelled:() => cancelled, yieldFrame:async () => { cancelled = true; }});
  assert.equal(result, null);
});

test("empty selections and nonpositive baseline prices have no invented percentage", async () => {
  const empty = await Windows.compareHistory(market, []);
  assert.equal(empty.compared, 0);
  assert.equal(empty.fixedPct, null);
  const negative = History.createMarket(data, source);
  for (const time of negative.hourly.keys()) negative.hourly.set(time, -100);
  const result = await Windows.compareHistory(negative, [sample], {yieldFrame:async () => {}});
  assert.equal(result.compared, 1);
  assert.ok(result.baselineCost < 0);
  assert.equal(result.originalPct, null);
  assert.equal(result.fixedPct, null);
});

test("full-history visuals reproduce the verified comparison and the rate-dependent change in cost", async () => {
  const bytes = fs.readFileSync(path.join(__dirname, "..", source.source));
  assert.equal(crypto.createHash("sha256").update(bytes).digest("hex"), source.sourceSha256);
  const before = JSON.stringify(source.rows);
  const tod = await Windows.compareHistory(market, source.rows, {yieldFrame:async () => {}});
  assert.equal(tod.total, 2155);
  assert.equal(tod.compared, 2121);
  assert.equal(tod.skipped, 34);
  close(tod.energyKwh, 9084.4);
  close(tod.baselineCost, 737.8192949173429);
  close(tod.originalCost, 509.17567557176375);
  close(tod.fixedCost, 454.504050157895);
  close(tod.additionalSaving, 54.67162541386875);
  close(tod.originalPct, 30.989108162479518);
  close(tod.fixedPct, 38.39900185738397);
  close(tod.originalHourly.reduce((a,b) => a+b), tod.energyKwh);
  close(tod.fixedHourly.reduce((a,b) => a+b), tod.energyKwh);
  const standard = await Windows.compareHistory(market, source.rows, {tariff:"standard", yieldFrame:async () => {}});
  assert.equal(standard.compared, tod.compared);
  close(standard.energyKwh, tod.energyKwh);
  close(standard.baselineCost, tod.baselineCost);
  close(standard.additionalSaving, -8.353675515617851);
  assert.equal(JSON.stringify(source.rows), before, "Source records remain unchanged");
});
