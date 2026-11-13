/* Reprice the verified Norway-to-ComEd schedules without changing their cohort. */
"use strict";
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const crypto = require("node:crypto"), assert = require("node:assert/strict");
const Study = require("./compare-residential.cjs");
const Engine = require("./dist/simulation-engine.js");
const Lab = require("./dist/charging-history-engine.js");
const ROOT = path.resolve(__dirname, "..");
const OUT = path.join(ROOT, "results/charging-history");
const EPS = 1e-6;
const METHODS = ["charge-now", "timer-0100", "day-ahead", "live", "hindsight"];
const RIDERS = ["renewable_portfolio_standard", "carbon_free_resource_adj", "zero_emission_standard",
  "energy_efficiency", "environmental_cost_recovery", "energy_transition_assistance", "coal_to_solar"];
const digest = value => crypto.createHash("sha256").update(value).digest("hex");
const NORMALIZATION = {householdKwhPerMonth: 1000, accountCapacityKw: 2.5, glenviewCentsPerKwh: .556};

function empty() {
  return {sessions: 0, energyKwh: 0, supply: 0, studyDelivery: 0, monthlyDistribution: 0,
    transmission: 0, illinoisDistribution: 0, riders: 0, stateTax: 0, glenviewTax: 0,
    allocatedService: 0, allocatedCapacity: 0, fixedSupplyWithPea: 0};
}
function amounts(t) {
  const study = t.supply + t.studyDelivery;
  const sourceVolume = study + t.transmission + t.illinoisDistribution + t.riders + t.stateTax;
  const account = t.allocatedService + t.allocatedCapacity;
  return {supplyOnly: t.supply, currentStudy: study, stateAndGlenviewTaxes: study + t.stateTax + t.glenviewTax,
    monthlyVolume: sourceVolume, monthlyWithAccounts: sourceVolume + account,
    glenviewVolume: sourceVolume + t.glenviewTax, glenviewWithAccounts: sourceVolume + t.glenviewTax + account};
}
function add(t, run, rates, plugMonth) {
  t.sessions++; t.energyKwh += run.energyKwh;
  t.supply += run.supplyUsd; t.studyDelivery += run.deliveryUsd;
  for (const slot of run.slots) {
    if (!slot.energyKwh) continue;
    const rate = rates[slot.date.slice(0, 7)];
    assert.ok(rate, `Missing monthly components for ${slot.date}`);
    const kwh = slot.energyKwh;
    t.monthlyDistribution += kwh * rate.distribution_facilities / 100;
    t.transmission += kwh * rate.transmission / 100;
    t.illinoisDistribution += kwh * rate.il_electricity_distribution / 100;
    t.riders += kwh * RIDERS.reduce((n, name) => n + rate[name], 0) / 100;
    // The explicitly hypothetical 1,000 kWh household stays in the first tax tier.
    t.stateTax += kwh * .330 / 100;
    t.glenviewTax += kwh * NORMALIZATION.glenviewCentsPerKwh / 100;
    t.fixedSupplyWithPea += kwh * (rate.fixed_price + rate.pea) / 100;
  }
  // Identical allocation for every method of a session: timing cannot reduce
  // an unchanged account service fee or an assumed capacity obligation.
  const rate = rates[plugMonth], share = run.energyKwh / NORMALIZATION.householdKwhPerMonth;
  assert.ok(rate, `Missing account rates for ${plugMonth}`);
  t.allocatedService += share * (rate.customer_charge_usd_month + rate.metering_charge_usd_month + rate.low_income_discount_recovery_usd_month);
  t.allocatedCapacity += share * rate.capacity_rate_usd_kw_month * NORMALIZATION.accountCapacityKw;
}
function compare() {
  const prior = JSON.parse(fs.readFileSync(path.join(OUT, "norway-strategies-2025.json"), "utf8"));
  const context = {window: {}};
  const bundleText = fs.readFileSync(path.join(__dirname, "dist/comed-cost-data.js"), "utf8");
  vm.runInNewContext(bundleText, context);
  const data = context.window.SPARC_COMED_COST_DATA, rates = data.rates;
  assert.equal(data.source.taxSources.state.tiers[0].centsPerKwh, .330);
  assert.equal(data.source.taxSources.municipal.tiers[0].centsPerKwh, NORMALIZATION.glenviewCentsPerKwh);
  const monthlyPath = path.resolve(ROOT, "../../sparc-lite/comed-electricity-cost-monthly.csv");
  assert.equal(digest(fs.readFileSync(monthlyPath)), data.source.sha256[data.source.monthlyFile], "Rebuild stale ComEd rate bundle");
  const {market, rows, bundle, hashes, priceFiles, calibration} = Study.loadMarket(2025);
  assert.deepEqual(JSON.parse(JSON.stringify(bundle.sources)), prior.sourceHashes);
  assert.deepEqual(hashes, prior.priceBundleHashes);
  const totals = new Map(), ids = [], exclusions = {};
  for (const tariff of ["standard", "time-of-day"]) for (const method of METHODS) totals.set(`${tariff}/${method}`, empty());
  for (let index = 0; index < rows.length; index++) {
    const row = rows[index], prepared = Lab.configuration(market, row, {tariff: "standard"});
    let reason = prepared.reason;
    if (prepared.valid) { const session = Engine.buildSession(market, prepared.config); if (!session.valid) reason = session.reason; }
    if (reason) { exclusions[reason] = (exclusions[reason] || 0) + 1; continue; }
    const baseline = Engine.simulate(market, {...prepared.config, strategy: "charge-now", tariff: "standard"});
    if (!baseline.ready) {
      const message = "Reported energy exceeds the available estimated-power capacity.";
      exclusions[message] = (exclusions[message] || 0) + 1; continue;
    }
    ids.push(row.id);
    for (const tariff of ["standard", "time-of-day"]) for (const method of METHODS) {
      const run = tariff === "standard" && method === "charge-now" ? baseline :
        Engine.simulate(market, {...prepared.config, strategy: method, tariff});
      assert.ok(run.valid && run.ready, `Changed completed cohort: ${row.id} ${tariff}/${method}`);
      assert.ok(Math.abs(run.energyKwh - row.gridKwh) <= EPS);
      add(totals.get(`${tariff}/${method}`), run, rates, row.modeledDate.slice(0, 7));
    }
    if ((index + 1) % 4000 === 0) console.error(`Repriced ${index + 1}/${rows.length} source sessions…`);
  }
  assert.equal(ids.length, prior.commonSessions);
  assert.equal(digest(ids.join("\n")), prior.cohortSha256, "The common session cohort changed");
  const baseline = totals.get("standard/charge-now"), baselineAmounts = amounts(baseline), results = [];
  for (const [key, detail] of totals) {
    const [tariff, strategy] = key.split("/"), original = prior.results.find(r => r.tariff === tariff && r.strategy === strategy);
    const costs = amounts(detail);
    assert.ok(Math.abs(detail.energyKwh - prior.commonEnergyKwh) < EPS * ids.length);
    assert.ok(Math.abs(costs.currentStudy - original.totalCostUsd) < 1e-6, `${key}: old costs did not reproduce`);
    assert.ok(Math.abs(detail.allocatedService - baseline.allocatedService) < 1e-6);
    assert.ok(Math.abs(detail.allocatedCapacity - baseline.allocatedCapacity) < 1e-6);
    assert.ok(Math.abs(detail.stateTax - baseline.stateTax) < 1e-6);
    assert.ok(Math.abs(detail.glenviewTax - baseline.glenviewTax) < 1e-6);
    results.push({strategy, label: original.label, tariff, detail,
      costs: Object.fromEntries(Object.entries(costs).map(([model, cost]) => [model,
        {cost, baseline: baselineAmounts[model], saving: baselineAmounts[model] - cost,
          savingPct: (baselineAmounts[model] - cost) / baselineAmounts[model] * 100}])),
      historicalStandardDeliveryCost: tariff === "standard" ? costs.glenviewWithAccounts - detail.studyDelivery + detail.monthlyDistribution : null});
  }
  const b = baseline;
  const fixedReference = b.fixedSupplyWithPea + b.monthlyDistribution + b.transmission + b.illinoisDistribution + b.riders + b.stateTax + b.glenviewTax + b.allocatedService;
  return {dataSource: "Norway Dataset 1 replays with 2025 ComEd prices", commonSessions: ids.length,
    commonEnergyKwh: prior.commonEnergyKwh, sourceReports: rows.length, cohortSha256: prior.cohortSha256,
    exclusions, normalization: NORMALIZATION, monthlySource: {file: data.source.monthlyFile,
      sha256: data.source.sha256[data.source.monthlyFile], bundleSha256: digest(bundleText)},
    sourceHashes: bundle.sources, priceBundleHashes: hashes, fiveMinuteFiles: priceFiles, liveCalibration: calibration,
    taxSources: data.source.taxSources,
    monthlyRateNotes: Object.fromEntries(Object.entries(rates).filter(([m]) => m.startsWith("2025-") || m === "2026-01").map(([m, r]) => [m, r.notes])),
    fixedSupplyReference: {cost: fixedReference, note: "Separate fixed-supply immediate baseline, with monthly standard distribution and Glenview tax; no extra hourly capacity charge."},
    assumptions: [
      "Exactly the original 33,784 completed sessions and charging schedules; only the bill calculation changes. No replanning against the added monthly charges.",
      "The original settled hourly supply feeds and prior-period live calibration are retained; monthly-average market prices do not replace hourly variation.",
      "Primary comparison preserves the original study distribution-facilities rates (standard 6.228 cents/kWh and the existing time-of-day bands). Monthly distribution facilities are not added again.",
      "Added volume components follow the mapped charging calendar month: transmission, Illinois distribution charge, supplied riders and state electricity excise tax.",
      "Glenview tax is a separate sensitivity using the user-confirmed Illinois location, not a fact about the original Norwegian addresses.",
      "Tax normalization assumes a hypothetical 1,000 kWh household month, within the first state/Glenview tiers. The separate household scenarios are never aggregated into one tax account.",
      "Account-charge sensitivity allocates each session's kWh / 1,000 times one monthly service fee and the capacity rate times an assumed 2.5 kW obligation. The mapped plug-in month fixes this allocation identically across strategies; no monthly bill is charged once per session.",
      "Existing 2026 study delivery bands remain hypothetical in the 2025 scenario. historicalStandardDeliveryCost separately replaces standard 6.228 with monthly historical distribution; it has no invented historical time-of-day counterpart.",
      "Hourly transmission uses the supplied fixed-plan proxy; hourly-specific PEA and procurement charges and percentage franchise adjustments are unavailable. Monthly rate notes flag source assumptions.",
      "All costs aggregate independent sessions from multiple original Norwegian years, mapped to 2025. They are not annual household bills, an actual Norwegian tax calculation, or a simultaneous fleet bill.",
    ], results};
}
function write(report) {
  const stem = path.join(OUT, "norway-comed-bill-costs-2025");
  fs.writeFileSync(stem + ".json", JSON.stringify(report, null, 2) + "\n");
  const quote = value => '"' + String(value).replaceAll('"', '""') + '"';
  const csv = [["strategy", "tariff", "cost_model", "cost_usd", "baseline_usd", "saving_usd", "saving_pct", "sessions", "kwh"]];
  for (const row of report.results) for (const [model, value] of Object.entries(row.costs)) csv.push([
    row.strategy, row.tariff, model, value.cost, value.baseline, value.saving, value.savingPct, report.commonSessions, report.commonEnergyKwh]);
  fs.writeFileSync(stem + ".csv", csv.map(row => row.map(quote).join(",")).join("\r\n") + "\r\n");
  const money = value => "$" + value.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const baseline = report.results.find(row => row.tariff === "standard" && row.strategy === "charge-now").costs;
  const lines = ["# Norwegian sessions: effect of monthly ComEd bill components", "",
    `${report.commonSessions.toLocaleString()} identical completed sessions; ${report.commonEnergyKwh.toFixed(3)} kWh; 2025 ComEd scenario.`, "",
    "Savings use immediate hourly-supply charging on standard study delivery as the baseline, recalculated on each cost basis.", "",
    `Baseline costs: existing study ${money(baseline.currentStudy.cost)}; with monthly volume components ${money(baseline.monthlyVolume.cost)}; with allocated account charges ${money(baseline.monthlyWithAccounts.cost)}; also including Glenview ${money(baseline.glenviewWithAccounts.cost)}.`, "",
    "| Method | Delivery | Existing study saving | Monthly volume components | With normalized account costs | With account costs + Glenview |",
    "|---|---|---:|---:|---:|---:|"];
  for (const row of report.results) {
    const cell = name => `${money(row.costs[name].saving)} (${row.costs[name].savingPct.toFixed(2)}%)`;
    lines.push(`| ${row.label} | ${row.tariff} | ${cell("currentStudy")} | ${cell("monthlyVolume")} | ${cell("monthlyWithAccounts")} | ${cell("glenviewWithAccounts")} |`);
  }
  lines.push("", "## Component totals", "", "See JSON for each method's itemized dollar components and CSV for all cost bases.", "", ...report.assumptions.map(s => "- " + s), "");
  fs.writeFileSync(stem + ".md", lines.join("\n"));
  console.log(stem + ".{json,csv,md}");
  for (const row of report.results) console.log(JSON.stringify({strategy: row.strategy, tariff: row.tariff, costs: row.costs}));
}
if (require.main === module) write(compare());
module.exports = {compare, amounts, add, write, NORMALIZATION};
