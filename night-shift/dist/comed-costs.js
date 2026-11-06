/* Itemized, equal-energy ComEd estimates from the sparc-lite daily trace. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SparcComedCostView = api;
  if (typeof window !== "undefined" && root.document) api.mount(root.document, root.SPARC_COMED_COST_DATA, root.SparcComedCosts);
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const ids = ["fixed-immediate", "hourly-immediate", "hourly-sparc"];
  const labels = ["Fixed · immediate", "Hourly · immediate", "Hourly · SPARC"];
  const money = value => new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(Math.abs(value) < .005 ? 0 : value);
  const number = (value, digits = 0) => new Intl.NumberFormat("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits}).format(value);
  const percent = (difference, base) => base > 0 ? `${number(Math.abs(difference) / base * 100, 1)}%` : null;
  const components = [
    ["supply", "Electricity supply", "Includes the fixed-plan Purchased Electricity Adjustment; hourly-specific adjustments are unavailable."],
    ["transmission", "Transmission", "Fixed-plan transmission is used as the hourly-plan estimate."],
    ["distributionFacilities", "Distribution facilities", "The ordinary delivery charge, separate from Illinois excise tax."],
    ["illinoisDistribution", "Illinois electricity distribution charge", "A separately itemized delivery charge; it is not the household electricity excise tax."],
    ["riders", "Riders and credits", "Renewable, carbon-free, zero-emission, efficiency, environmental, transition, and coal-to-solar adjustments."],
    ["stateTax", "Illinois electricity use tax", "Incremental monthly excise tax above the entered other household usage."],
    ["municipalTax", "Glenview electricity tax / override", "Filed monthly kWh tiers by default; any entered per-plan override is a user scenario."],
    ["variableTotal", "EV volume charges", "", "cc-subtotal"],
    ["serviceFees", "EV share of service fees", "Customer, metering, and low-income recovery charges, allocated by household energy share."],
    ["capacity", "EV share of capacity", "Hourly-plan account capacity allocated by household energy share. Fixed supply already includes capacity."],
    ["total", "Allocated EV cost", "Volume charges plus allocated monthly fees and capacity.", "cc-total"],
  ];
  function validate(report) {
    if (!report || !Array.isArray(report.strategies) || !Array.isArray(report.months)) throw new Error("The ComEd simulation returned incomplete results.");
    const strategies = ids.map(id => report.strategies.find(row => row.id === id));
    if (strategies.some(row => !row || !Number.isFinite(row.total) || !Number.isFinite(row.energyKwh))) throw new Error("All three charging options need valid results.");
    if (strategies.some(row => Math.abs(row.energyKwh - report.energyKwh) > Math.max(1e-5, report.comparedDays * 1e-6))) throw new Error("The charging options must deliver equal energy.");
    return strategies;
  }
  function mount(doc, data, engine) {
    const $ = id => doc.getElementById(id);
    const put = (id, value) => { $(id).textContent = value; };
    const el = (tag, className, value) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (value !== undefined) node.textContent = value;
      return node;
    };
    let report = null, generation = 0;
    const fail = message => { put("cc-error", message); $("cc-error").hidden = false; };
    if (!data?.rows?.length || typeof engine?.compare !== "function") {
      fail("The ComEd data or billing model could not load. Reload the complete SPARC page.");
      put("cc-status", "The bill simulation is unavailable.");
      $("cc-run").disabled = true;
      $("cc-download").disabled = true;
      return;
    }
    const first = data.rows[0][0], last = data.rows.at(-1)[0];
    for (const [id, value] of [["cc-from", first], ["cc-to", last]]) {
      $(id).min = first; $(id).max = last; $(id).value = value;
    }
    function render(result) {
      const strategies = validate(result), fixed = strategies[0], immediate = strategies[1], sparc = strategies[2];
      report = result;
      const hasData = result.comparedDays > 0;
      $("cc-results").hidden = !hasData;
      $("cc-download").disabled = !hasData;
      put("cc-status", `${number(result.comparedDays)} of ${number(result.chargingDays)} home-charging days compared. ${number(result.excludedDays)} excluded; ${number(result.excludedKwh, 1)} kWh excluded from every option.`);
      if (!hasData) {
        fail(result.chargingDays ? "No days can deliver the same energy with complete prices in this selection. Try a higher charger power or a wider date range." : "No home-charging energy falls in this date range.");
        return;
      }
      const timing = immediate.variableTotal - sparc.variableTotal, plan = fixed.total - sparc.total;
      put("cc-sparc-cost", money(sparc.total));
      put("cc-sparc-detail", `${number(sparc.total / sparc.energyKwh * 100, 2)}¢ per home kWh, with allocated account charges`);
      function savingCard(id, noteId, difference, base, relationship) {
        put(id, money(Math.abs(difference)));
        const ratio = percent(difference, base);
        put(noteId, `${ratio ? `${ratio} ${difference >= 0 ? "lower" : "higher"} cost. ` : ""}${difference >= 0 ? "Saved" : "Additional cost"} ${relationship}.`);
        $(id).parentElement.classList.toggle("is-negative", difference < 0);
      }
      savingCard("cc-timing-saving", "cc-timing-note", timing, immediate.variableTotal, "versus hourly immediate charging, volume charges only");
      savingCard("cc-plan-saving", "cc-plan-note", plan, fixed.total, "versus immediate charging on fixed pricing");
      put("cc-cohort", `${number(result.energyKwh, 1)} home kWh on ${number(result.comparedDays)} matched days · ${result.options.from} to ${result.options.to} · ${number(result.options.powerKw, 1)} kW · 8 p.m.–8 a.m. Chicago time. Totals cover this selection and are not annual savings.`);
      drawBars(strategies);
      renderTaxTreatment(result, strategies);
      $("cc-breakdown").replaceChildren(...components.map(([key, label, note, style]) => {
        const row = el("tr", style || ""), name = el("td", "", label);
        if (note) name.append(el("span", "cc-row-note", note));
        row.append(name, ...strategies.map(strategy => el("td", strategy[key] < 0 ? "cc-credit" : "", money(strategy[key]))));
        return row;
      }));
      put("cc-allocation", `Monthly service fees and capacity are allocated using EV kWh ÷ (EV kWh + other home kWh), by method and charging month. Other home usage is ${number(result.options.householdKwh)} kWh/month; hourly account capacity is ${number(result.options.capacityKw, 1)} kW. This allocated EV cost is not a whole-house bill.`);
      $("cc-monthly").replaceChildren(...result.months.map(month => {
        const methods = ids.map(id => month.strategies.find(row => row.id === id));
        const row = el("tr"), monthName = el("td", "", month.month);
        if (month.coverageFraction < 1 - 1e-8) monthName.append(el("span", "cc-row-note", `${number(month.coverageDays)} of ${number(month.calendarDays)} days`));
        const energy = el("td", "", number(methods[2].energyKwh, 1));
        energy.title = `Monthly kWh — fixed: ${number(methods[0].energyKwh, 2)}; hourly immediate: ${number(methods[1].energyKwh, 2)}; SPARC: ${number(methods[2].energyKwh, 2)}. Overnight month boundaries can shift energy between months.`;
        const saving = methods[1].variableTotal - methods[2].variableTotal;
        row.append(monthName, energy, ...methods.map(method => el("td", "", money(method.total))), el("td", saving < 0 ? "is-negative" : "", money(saving)));
        return row;
      }));
      const fixedService = fixed.accountServiceFees, hourlyService = sparc.accountServiceFees, hourlyCapacity = sparc.accountCapacity;
      put("cc-account-summary", `Before allocation, the modeled account service fees total ${money(fixedService)} for fixed pricing and ${money(hourlyService)} for SPARC’s hourly pricing. The entered hourly capacity obligation adds ${money(hourlyCapacity)} for the account. Only the EV’s calculated share appears in the charging totals above.`);
      put("cc-partial-month-note", "For partial months, the selected source-calendar days prorate both the account charges and other household usage; month-boundary charging is assigned to its actual month.");
      put("cc-coverage", `${number(result.selectedDays)} source-calendar days selected, including ${number(result.chargingDays)} with home charging. ${number(result.comparedDays)} charging days are included and ${number(result.excludedDays)} are excluded from all three methods. Daily energy is taken from the source trace; the charging window is assumed.`);
      const exclusionCounts = new Map();
      for (const item of result.exclusions) exclusionCounts.set(item.reason, (exclusionCounts.get(item.reason) || 0) + 1);
      $("cc-exclusions").replaceChildren(...(exclusionCounts.size ? [...exclusionCounts].map(([reason, count]) => el("li", "", `${number(count)} days: ${reason}`)) : [el("li", "", "No charging days excluded.")]));
      put("cc-source", "Inputs: car0001_zip60025_tesla-X-LR-2022.csv, comed-electricity-cost-5min.csv, comed-electricity-cost-monthly.csv, and comed-rate-components.csv from sparc-lite. Original input files are preserved.");
      renderHashes(result.source || data.source || {});
      const rateNotes = result.months.flatMap(month => {
        const notes = Array.isArray(month.notes) ? month.notes.join("; ") : month.notes;
        return notes ? [el("li", "", `${month.month}: ${notes}`)] : [];
      });
      $("cc-rate-notes").replaceChildren(...(rateNotes.length ? rateNotes : [el("li", "", "No additional monthly rate notes.")]));
    }
    function renderTaxTreatment(result, strategies) {
      if (!result.taxTreatment?.fixed?.state || !result.taxTreatment?.hourly?.state) throw new Error("The plan-specific tax sources could not load.");
      const treatment = result.taxTreatment, fixed = strategies[0], immediate = strategies[1], sparc = strategies[2];
      const sourceLink = rule => {
        const link = el("a", "cc-tax-source", rule.source || "Filed tax schedule");
        link.setAttribute("href", rule.url); link.setAttribute("target", "_blank"); link.setAttribute("rel", "noopener noreferrer");
        return link;
      };
      const ruleCell = (rule, state = false) => {
        const cell = el("td"), status = rule.status;
        const badge = state ? "Verified state rule" : status === "user-assumption" ? "User scenario override" : status === "historical-assumption" ? "Filed tiers · historical carryforward" : "Sourced Glenview tiers";
        cell.append(el("span", `cc-tax-status${status === "user-assumption" || status === "historical-assumption" ? " cc-tax-assumed" : ""}`, badge));
        if (!state && Number.isFinite(rule.centsPerKwh)) {
          cell.append(el("p", "cc-tax-rule", `${number(rule.centsPerKwh, 3)}¢/kWh · flat scenario override`));
          cell.append(el("span", "cc-row-note", "User-entered assumption; not a verified town rate. Clear the override to restore filed Glenview tiers."));
        } else {
          const tiers = rule.tiers || [];
          if (tiers.length < 2) throw new Error("The sourced tax tiers are incomplete.");
          cell.append(el("p", "cc-tax-rule", `${number(tiers[0].centsPerKwh, 3)}¢/kWh · first ${number(tiers[0].kwh)} kWh/month`));
          cell.append(el("p", "cc-tax-rule", `${number(tiers[1].centsPerKwh, 3)}¢/kWh · next ${number(tiers[1].kwh)} kWh/month`));
          cell.append(el("span", "cc-row-note", `Basis: ${rule.basis}. Higher usage follows the linked tier schedule.`));
          cell.append(sourceLink(rule));
          if (rule.historicalAssumptionMonths?.length) {
            const months = rule.historicalAssumptionMonths;
            const note = el("span", "cc-row-note", `Historical carryforward: ${number(months.length)} selected months outside independently verified filings.`);
            note.title = months.join(", "); cell.append(note);
          }
        }
        return cell;
      };
      const dollars = (primary, secondary) => {
        const cell = el("td", "", money(primary));
        if (secondary !== undefined) {
          cell.textContent = "";
          cell.append(el("span", "cc-tax-amount", `SPARC: ${money(primary)}`), el("span", "cc-row-note", `Immediate: ${money(secondary)}`));
        }
        return cell;
      };
      const row = (label, key, cells) => {
        const tr = el("tr"), title = el("th", "", label); title.setAttribute("scope", "row"); tr.setAttribute("data-tax-row", key); tr.append(title, ...cells); return tr;
      };
      $("cc-tax-treatment").replaceChildren(
        row("Illinois residential excise tax", "state-rule", [ruleCell(treatment.fixed.state, true), ruleCell(treatment.hourly.state, true)]),
        row("Modeled added state tax", "state-amount", [dollars(fixed.stateTax), dollars(sparc.stateTax, immediate.stateTax)]),
        row("Glenview municipal electricity tax", "municipal-rule", [ruleCell(treatment.fixed.municipal), ruleCell(treatment.hourly.municipal)]),
        row("Modeled added local tax", "municipal-amount", [dollars(fixed.municipalTax), dollars(sparc.municipalTax, immediate.municipalTax)])
      );
    }
    function renderHashes(source) {
      const hashes = [];
      if (source.sha256 && typeof source.sha256 === "object") {
        Object.entries(source.sha256).forEach(([file, hash]) => {
          if (typeof hash === "string") hashes.push(`${file}: SHA-256 ${hash}`);
        });
      }
      function visit(value, key = "") {
        if (!value || typeof value !== "object") return;
        const hash = value.sha256 || value.sourceSha256;
        if (typeof hash === "string") hashes.push(`${value.file || value.filename || value.name || key}: SHA-256 ${hash}`);
        Object.entries(value).forEach(([name, child]) => {
          if (/sha256/i.test(name) && typeof child === "string" && name !== "sha256" && name !== "sourceSha256") hashes.push(`${name}: ${child}`);
          else if (child && typeof child === "object") visit(child, name);
        });
      }
      visit(source);
      $("cc-source-hashes").replaceChildren(...[...new Set(hashes)].map(hash => el("p", "", hash)));
    }
    function drawBars(strategies) {
      const values = strategies.map(strategy => strategy.total), low = Math.min(0, ...values), high = Math.max(0, ...values);
      const span = high - low || 1, zero = -low / span * 100;
      $("cc-cost-bars").replaceChildren(...strategies.map((strategy, i) => {
        const wrapper = el("div", "cc-cost-bar"), top = el("div", "cc-cost-bar-top");
        top.append(el("span", "", labels[i]), el("strong", "", money(strategy.total)));
        const track = el("div", "cc-bar-track"), bar = el("span", "cc-bar"), axis = el("span", "cc-zero");
        bar.style.left = `${Math.min(zero, (strategy.total - low) / span * 100)}%`;
        bar.style.width = `${Math.abs(strategy.total) / span * 100}%`; axis.style.left = `${zero}%`;
        track.setAttribute("aria-hidden", "true"); track.append(bar, axis); wrapper.append(top, track); return wrapper;
      }));
    }
    async function run(event) {
      event?.preventDefault();
      const current = ++generation;
      $("cc-error").hidden = true;
      if (!$("cc-form").checkValidity() || $("cc-from").value > $("cc-to").value) {
        fail("Choose valid dates, with From on or before Through, and valid nonnegative household assumptions.");
        $("cc-results").hidden = true;
        $("cc-download").disabled = true;
        put("cc-status", "Update the assumptions and run the simulation again.");
        return;
      }
      const override = id => $(id).value.trim() === "" ? null : Number($(id).value);
      const options = {from: $("cc-from").value, to: $("cc-to").value, powerKw: Number($("cc-power").value), householdKwh: Number($("cc-home").value), capacityKw: Number($("cc-capacity").value), fixedMunicipalCents: override("cc-fixed-local-tax"), hourlyMunicipalCents: override("cc-hourly-local-tax"), arrivalHour: 20, departureHour: 8};
      $("cc-run").disabled = true; $("cc-download").disabled = true;
      $("cc-results").hidden = true;
      $("cc-form").setAttribute("aria-busy", "true");
      put("cc-status", "Replaying the same daily home energy on all three options…");
      await new Promise(resolve => setTimeout(resolve, 0));
      try {
        const result = engine.compare(data, options);
        if (current === generation) render(result);
      } catch (error) {
        if (current === generation) { fail(error.message || "The ComEd simulation could not finish."); put("cc-status", "Update the assumptions and run the simulation again."); }
      } finally {
        if (current === generation) { $("cc-run").disabled = false; $("cc-form").setAttribute("aria-busy", "false"); }
      }
    }
    $("cc-form").addEventListener("submit", run);
    $("cc-form").addEventListener("input", () => {
      if (report) put("cc-status", "Assumptions changed. Run the bill simulation to refresh these results.");
      $("cc-download").disabled = true;
    });
    $("cc-download").addEventListener("click", () => {
      if (!report || typeof engine.csv !== "function") return;
      const url = URL.createObjectURL(new Blob([engine.csv(report)], {type: "text/csv;charset=utf-8"}));
      const link = el("a"); link.href = url; link.download = `comed-costs-${report.options.from}-${report.options.to}.csv`;
      doc.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    const ready = run();
    return {run, ready, getReport: () => report};
  }
  return {validate, mount};
});
