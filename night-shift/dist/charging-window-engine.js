/* Equal-energy comparison of recorded TeslaMate events and fixed overnight availability. */
(function (root, factory) {
  "use strict";
  const common = typeof module === "object" && module.exports;
  const api = factory(common ? require("./simulation-engine.js") : root.SparcSimulation,
    common ? require("./charging-history-engine.js") : root.SparcChargingHistory);
  if (common) module.exports = api;
  else root.SparcChargingWindows = api;
})(typeof window !== "undefined" ? window : globalThis, function (Engine, History) {
  "use strict";
  const EPS = 1e-6;
  function nextDate(day) {
    const date = new Date(day + "T12:00:00Z");
    date.setUTCDate(date.getUTCDate() + 1);
    return date.toISOString().slice(0, 10);
  }
  function compareSession(market, row, options = {}) {
    const tariff = options.tariff || "time-of-day";
    if (!["standard", "time-of-day"].includes(tariff)) return {valid: false, reason: "Choose a supported delivery rate."};
    if (row.sourceKind === "residential") return {valid: false, reason: "This comparison uses TeslaMate charging history."};
    const prepared = History.configuration(market, row, {window: "recorded", tariff});
    if (!prepared.valid) return {valid: false, reason: prepared.reason};
    const original = prepared.config, day = row.startLocal.slice(0, 10);
    const fixed = {...original, startEpoch: market.at(day, 20), endEpoch: market.at(nextDate(day), 8)};
    // Only the availability boundaries change; energy, power and efficiency remain identical.
    const runs = [
      Engine.simulate(market, {...original, strategy: "charge-now", tariff: "standard"}),
      Engine.simulate(market, {...original, strategy: "day-ahead"}),
      Engine.simulate(market, {...fixed, strategy: "day-ahead"}),
    ];
    const invalid = runs.find(run => !run.valid);
    if (invalid) return {valid: false, reason: invalid.reason};
    if (runs.some(run => !run.ready || Math.abs(run.energyKwh - row.gridKwh) > EPS)) {
      return {valid: true, comparable: false, reason: "At least one window cannot deliver the same required energy."};
    }
    const hourly = run => {
      const hours = Array(24).fill(0);
      run.slots.forEach(slot => { hours[slot.wallHour] += slot.energyKwh; });
      return hours;
    };
    return {valid: true, comparable: true, id: row.id, energyKwh: row.gridKwh,
      powerKw: original.power, fixedPowerKw: fixed.power, powerAdjusted: prepared.powerAdjusted,
      startEpoch: fixed.startEpoch, endEpoch: fixed.endEpoch,
      baselineCost: runs[0].totalCost, originalCost: runs[1].totalCost, fixedCost: runs[2].totalCost,
      originalHourly: hourly(runs[1]), fixedHourly: hourly(runs[2])};
  }
  function emptySummary() {
    return {total: 0, compared: 0, skipped: 0, shortfall: 0, energyKwh: 0, powerAdjustments: 0,
      baselineCost: 0, originalCost: 0, fixedCost: 0,
      originalHourly: Array(24).fill(0), fixedHourly: Array(24).fill(0), exclusions: {}};
  }
  function add(summary, result) {
    summary.total++;
    if (!result.valid || !result.comparable) {
      summary[!result.valid ? "skipped" : "shortfall"]++;
      summary.exclusions[result.reason] = (summary.exclusions[result.reason] || 0) + 1;
      return;
    }
    summary.compared++;
    summary.energyKwh += result.energyKwh;
    summary.powerAdjustments += Number(result.powerAdjusted);
    for (const key of ["baselineCost", "originalCost", "fixedCost"]) summary[key] += result[key];
    for (let h = 0; h < 24; h++) {
      summary.originalHourly[h] += result.originalHourly[h];
      summary.fixedHourly[h] += result.fixedHourly[h];
    }
  }
  function finish(summary) {
    summary.originalSaving = summary.baselineCost - summary.originalCost;
    summary.fixedSaving = summary.baselineCost - summary.fixedCost;
    summary.additionalSaving = summary.originalCost - summary.fixedCost;
    // A zero or negative baseline does not have a meaningful savings percentage.
    summary.originalPct = summary.compared && summary.baselineCost > 0 ? summary.originalSaving / summary.baselineCost * 100 : null;
    summary.fixedPct = summary.compared && summary.baselineCost > 0 ? summary.fixedSaving / summary.baselineCost * 100 : null;
    return summary;
  }
  async function compareHistory(market, rows, options = {}) {
    const summary = emptySummary();
    const yieldFrame = options.yieldFrame || (() => new Promise(resolve => setTimeout(resolve, 0)));
    const chunkSize = options.chunkSize || 24;
    for (let i = 0; i < rows.length; i++) {
      if (options.cancelled?.()) return null;
      add(summary, compareSession(market, rows[i], options));
      if ((i + 1) % chunkSize === 0 || i === rows.length - 1) {
        options.onProgress?.(i + 1, rows.length);
        await yieldFrame();
      }
    }
    return options.cancelled?.() ? null : finish(summary);
  }
  return {compareSession, compareHistory, emptySummary, add, finish};
});
