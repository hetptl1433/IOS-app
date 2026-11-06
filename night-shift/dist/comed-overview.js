"use strict";
(() => {
  const root = document.getElementById("comed-cost-overview");
  if (!root) return;
  const summary = window.SPARC_COMED_COST_SUMMARY;
  if (!summary || !summary.comparedDays) return;
  const money = value => new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(value);
  const number = value => value.toLocaleString("en-US", {maximumFractionDigits: 1});
  const totals = document.createElement("div");
  totals.className = "co-totals";
  totals.setAttribute("aria-label", "Modeled EV bill costs including allocated monthly charges");
  for (const row of summary.strategies) {
    const item = document.createElement("div"), label = document.createElement("span"), amount = document.createElement("strong");
    item.className = "co-total";
    label.textContent = {"fixed-immediate": "Fixed rate · immediate", "hourly-immediate": "Hourly rate · immediate", "hourly-sparc": "Hourly rate · SPARC"}[row.id] || row.label;
    amount.textContent = money(row.total);
    item.append(label, amount); totals.append(item);
  }
  const context = document.createElement("p");
  context.className = "co-context";
  const saving = summary.timingSavings;
  context.textContent = `${number(summary.energyKwh)} matched home kWh across ${number(summary.comparedDays)} charging days. SPARC timing ${saving >= 0 ? "saves" : "adds"} ${money(Math.abs(saving))} in volume charges versus immediate hourly charging. ${number(summary.excludedDays)} charging days excluded for incomplete energy or prices.`;
  const basis = document.createElement("p");
  basis.className = "co-context";
  basis.textContent = `${summary.options.from}–${summary.options.to} · assumed 8 p.m.–8 a.m. availability at ${summary.options.powerKw} kW · ${summary.options.householdKwh} other home kWh/month · ${summary.options.capacityKw} kW account capacity obligation. Service and capacity charges are allocated by the EV share of modeled household energy.`;
  root.querySelector(".co-limit").before(totals, context, basis);
})();
