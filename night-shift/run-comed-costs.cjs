"use strict";
// Reproduce the default offline bill estimate from the checked-in source bundle.
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const Engine = require("./dist/comed-cost-engine.js");
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "dist/comed-cost-data.js"), "utf8"), context);
const data = context.window.SPARC_COMED_COST_DATA;
const report = Engine.compare(data);
const out = path.resolve(__dirname, "../results/charging-history");
fs.mkdirSync(out, {recursive: true});
fs.writeFileSync(path.join(out, "comed-costs.json"), JSON.stringify(report, null, 2) + "\n");
fs.writeFileSync(path.join(out, "comed-costs.csv"), Engine.csv(report));
const {months, exclusions, ...summary} = report;
summary.exclusionReasons = Object.entries(exclusions.reduce((all, row) => {
  all[row.reason] = (all[row.reason] || 0) + 1; return all;
}, {})).map(([reason, days]) => ({reason, days}));
fs.writeFileSync(path.join(__dirname, "dist/comed-cost-summary.js"), "window.SPARC_COMED_COST_SUMMARY=" + JSON.stringify(summary) + ";\n");
console.log(`${report.comparedDays} / ${report.chargingDays} charging days; ${report.energyKwh.toFixed(1)} kWh; ${report.excludedDays} excluded.`);
for (const row of report.strategies) console.log(`${row.label}: $${row.total.toFixed(2)} ($${row.variableTotal.toFixed(2)} volume + $${(row.serviceFees + row.capacity).toFixed(2)} allocated account charges)`);
console.log(`SPARC timing savings $${report.timingSavings.toFixed(2)}; fixed-to-SPARC difference $${report.planSavings.toFixed(2)}`);
console.log(summary.exclusionReasons);
