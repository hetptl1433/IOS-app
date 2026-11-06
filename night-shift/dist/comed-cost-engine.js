/* Calendar-month ComEd EV cost estimates. Prices in cents/kWh; results in USD.
 * Interval-end prices are normalized by the Python builder. This module never
 * uses future actual prices to choose charging hours.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SparcComedCosts = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const EPS = 1e-7, HOUR = 3600;
  const STATE_TAX_SOURCE = "https://www.ilga.gov/commission/jcar/admincode/086/086005110A01100R.html";
  const TAX_COMPARISON_SOURCE = "https://hourlypricing.comed.com/faqs/";
  const RIDER_TAX_SOURCE = "https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/bltb8485cefee950e7c/67b4f3aef14e110d1e948678/18_Rider_TAX.pdf?branch=prod_alias";
  const GLENVIEW_2024_SOURCE = "https://www.icc.illinois.gov/downloads/public/filing/4/350172.pdf";
  const GLENVIEW_2026_SOURCE = "https://www.comed.com/cdn/assets/v3/assets/blt3ebb3fed6084be2a/blt86ebee5fe6ed02f8/6ad911e1630c34d7f475f5cd/2026_Ratebook.pdf#page=404";
  const GLENVIEW_CENTS = [.556, .366, .346, .345, .340, .338, .294, .206, .144, .082];
  const GLENVIEW_EFFECTIVE_FROM = "2024-01-01", GLENVIEW_VERIFIED_THROUGH = "2026-11-05";
  // Statutory marginal blocks, in cents/kWh; null denotes the remaining kWh.
  const STATE_TAX_TIERS = [[2000, .330], [48000, .319], [50000, .303], [400000, .297], [500000, .286],
    [2000000, .270], [2000000, .254], [5000000, .233], [10000000, .207], [null, .202]];
  const SPECS = [
    {id: "fixed-immediate", label: "Fixed rate · immediate", supplyPlan: "fixed"},
    {id: "hourly-immediate", label: "Hourly rate · immediate", supplyPlan: "hourly"},
    {id: "hourly-sparc", label: "Hourly rate · SPARC", supplyPlan: "hourly"},
  ];
  const FIELDS = ["supply", "transmission", "delivery", "riders", "stateTax", "municipalTax", "serviceFees", "capacity"];
  const RIDERS = ["renewable_portfolio_standard", "carbon_free_resource_adj", "zero_emission_standard", "energy_efficiency",
    "environmental_cost_recovery", "energy_transition_assistance", "coal_to_solar"];
  const cache = new WeakMap();
  const finite = Number.isFinite;
  function nextDate(day, delta = 1) {
    const date = new Date(day + "T12:00:00Z"); date.setUTCDate(date.getUTCDate() + delta);
    return date.toISOString().slice(0, 10);
  }
  function calendarDays(month) {
    const [year, m] = month.split("-").map(Number);
    return new Date(Date.UTC(year, m, 0)).getUTCDate();
  }
  function defaults(data, input = {}) {
    const result = Object.assign({from: data.rows[0][0], to: data.rows.at(-1)[0], powerKw: 11.5,
      arrivalHour: 20, departureHour: 8, householdKwh: 600, capacityKw: 2.5, municipalCents: null}, input);
    // Preserve the original shared-rate option for prior callers, while explicit
    // plan-specific assumptions always win over that shared fallback.
    const nullableRate = value => value === null || value === undefined || value === "" ? null : Number(value);
    result.municipalCents = nullableRate(result.municipalCents);
    result.fixedMunicipalCents = nullableRate(input.fixedMunicipalCents !== undefined ? input.fixedMunicipalCents : result.municipalCents);
    result.hourlyMunicipalCents = nullableRate(input.hourlyMunicipalCents !== undefined ? input.hourlyMunicipalCents : result.municipalCents);
    for (const name of ["powerKw", "arrivalHour", "departureHour", "householdKwh", "capacityKw"]) {
      result[name] = Number(result[name]);
      if (!finite(result[name])) throw new RangeError(`Choose a finite ${name}.`);
    }
    for (const name of ["municipalCents", "fixedMunicipalCents", "hourlyMunicipalCents"]) {
      if (result[name] !== null && (!finite(result[name]) || result[name] < 0)) throw new RangeError("Municipal tax overrides must be blank or a finite nonnegative cents/kWh rate.");
    }
    if (result.powerKw <= 0 || result.householdKwh < 0 || result.capacityKw < 0 ||
        result.municipalCents < 0 || result.fixedMunicipalCents < 0 || result.hourlyMunicipalCents < 0) {
      throw new RangeError("Power must be positive; household usage, capacity and municipal tax must be nonnegative.");
    }
    for (const name of ["arrivalHour", "departureHour"]) {
      if (!Number.isInteger(result[name]) || result[name] < 0 || result[name] > 23) throw new RangeError("Choose whole clock hours from 0 through 23.");
    }
    if (result.arrivalHour === result.departureHour) throw new RangeError("Arrival and departure hours must differ.");
    for (const name of ["from", "to"]) {
      const parsed = Date.parse(result[name] + "T12:00:00Z");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(result[name]) || !finite(parsed) || new Date(parsed).toISOString().slice(0, 10) !== result[name]) throw new RangeError("Choose valid calendar dates.");
    }
    if (result.from > result.to) throw new RangeError("From must be on or before Through.");
    return result;
  }
  function market(data) {
    if (cache.has(data)) return cache.get(data);
    const result = {hours: new Map(), local: new Map()};
    for (const row of data.hours) {
      const h = {time: row[0], date: row[1], localHour: row[2], forecast: row[3], billed: row[4], samples: row[5], release: row[6]};
      result.hours.set(h.time, h);
      const key = h.date + "|" + h.localHour;
      if (!result.local.has(key)) result.local.set(key, []);
      result.local.get(key).push(h);
    }
    cache.set(data, result); return result;
  }
  function ratesValid(rate) {
    return rate && ["fixed_price", "pea", "transmission", "distribution_facilities", "il_electricity_distribution",
      "capacity_rate_usd_kw_month", "customer_charge_usd_month", "metering_charge_usd_month", "low_income_discount_recovery_usd_month",
      ...RIDERS].every(key => finite(rate[key]));
  }
  function adder(rate) {
    return rate.transmission + rate.distribution_facilities + rate.il_electricity_distribution + RIDERS.reduce((sum, key) => sum + rate[key], 0);
  }
  function expectedPrice(book, hour, now) {
    if (hour.release <= now) return hour.forecast;
    const previous = book.local.get(nextDate(hour.date, -1) + "|" + hour.localHour) || [];
    const known = previous.find(h => h.release <= now && finite(h.forecast));
    return known ? known.forecast : null;
  }
  function planDay(data, row, input = {}) {
    const options = defaults(data, input), book = market(data), date = row[0], kwh = row[1];
    const endDate = options.departureHour <= options.arrivalHour ? nextDate(date) : date;
    const starts = book.local.get(date + "|" + options.arrivalHour) || [];
    const ends = book.local.get(endDate + "|" + options.departureHour) || [];
    const fail = reason => ({valid: false, date, kwh, reason});
    if (starts.length !== 1 || ends.length !== 1) return fail("Missing or ambiguous Chicago arrival/departure hour");
    const start = starts[0].time, end = ends[0].time, hours = [];
    if (end <= start) return fail("Invalid charging window");
    if (kwh > (end - start) / HOUR * options.powerKw + EPS) return fail("Insufficient charger power or available hours");
    for (let t = start; t < end; t += HOUR) {
      const h = book.hours.get(t);
      if (!h || h.samples !== 12 || !finite(h.billed)) return fail("Incomplete settled hour (requires all 12 five-minute prices)");
      if (!finite(h.forecast)) return fail("Missing or inconsistent day-ahead forecast");
      if (!ratesValid(data.rates[h.date.slice(0, 7)])) return fail("Missing monthly tariff components");
      hours.push(h);
    }
    function immediate() {
      let left = kwh;
      return hours.map(h => { const energyKwh = Math.max(0, Math.min(options.powerKw, left)); left -= energyKwh; return {...h, energyKwh}; });
    }
    const immediateHours = immediate(), sparcHours = [];
    let remaining = kwh;
    // Replan at each hour using only published forecasts. Tomorrow's hours use
    // prior-local-day same-hour forecasts until tomorrow's release is reached.
    for (let i = 0; i < hours.length; i++) {
      const now = hours[i].time;
      const candidates = hours.slice(i).map(h => ({h, price: expectedPrice(book, h, now)}));
      if (candidates.some(c => !finite(c.price))) return fail("No published forecast or prior-day fallback for the chosen window");
      candidates.sort((a, b) => (a.price + adder(data.rates[a.h.date.slice(0, 7)])) -
        (b.price + adder(data.rates[b.h.date.slice(0, 7)])) || a.h.time - b.h.time);
      let needed = remaining, energyKwh = 0;
      for (const candidate of candidates) {
        const allocated = Math.min(options.powerKw, Math.max(0, needed));
        if (candidate.h.time === now) energyKwh = allocated;
        needed -= allocated;
      }
      remaining -= energyKwh;
      sparcHours.push({...hours[i], energyKwh});
    }
    if (remaining > EPS) return fail("SPARC could not deliver equal energy");
    const tracks = SPECS.map(spec => ({...spec, hours: spec.id === "hourly-sparc" ? sparcHours : immediateHours,
      energyKwh: kwh}));
    return {valid: true, date, kwh, start, end, endDate, tracks};
  }
  function tierTax(kwh, tiers) {
    if (!finite(kwh) || kwh < 0) throw new RangeError("Tax energy must be finite and nonnegative.");
    let left = kwh, dollars = 0;
    for (const [limit, cents] of tiers) {
      const energy = Math.min(limit === null ? Infinity : limit, left);
      dollars += energy * cents / 100; left -= energy; if (left <= 0) break;
    }
    return dollars;
  }
  function stateTax(kwh) { return tierTax(kwh, STATE_TAX_TIERS); }
  function glenviewTax(kwh) { return tierTax(kwh, STATE_TAX_TIERS.map(([limit], i) => [limit, GLENVIEW_CENTS[i]])); }
  function sourcedMunicipalStatus(month) {
    return month >= GLENVIEW_EFFECTIVE_FROM.slice(0, 7) && month <= GLENVIEW_VERIFIED_THROUGH.slice(0, 7) ? "sourced" : "historical-assumption";
  }
  function updateMunicipalStatus(treatment, months) {
    for (const plan of ["fixed", "hourly"]) {
      const municipal = treatment[plan].municipal;
      if (municipal.centsPerKwh !== null) continue;
      municipal.historicalAssumptionMonths = [...new Set(months)].filter(month => sourcedMunicipalStatus(month) !== "sourced").sort();
      municipal.status = municipal.historicalAssumptionMonths.length ? "historical-assumption" : "sourced";
    }
  }
  function taxTreatment(options) {
    const result = {};
    for (const plan of ["fixed", "hourly"]) {
      const centsPerKwh = options[plan + "MunicipalCents"];
      result[plan] = {
        state: {id: "illinois-residential-excise", source: "Illinois Administrative Code §511.110",
          url: STATE_TAX_SOURCE, basis: "monthly household kWh",
          tiers: STATE_TAX_TIERS.map(([kwh, centsPerKwh]) => ({kwh, centsPerKwh})),
          allocation: "T(other household kWh + EV kWh) - T(other household kWh)", sameRule: true, status: "verified"},
        municipal: centsPerKwh === null ? {
          id: "glenview-electricity-use-tax", centsPerKwh: null, status: "sourced", municipality: "Glenview, Illinois",
          source: "ComEd Rider TAX, Glenview municipal tax additions, Sheet 261 (2024, 2025 and October 2026 filings); rates include the collection-cost adjustment.",
          url: GLENVIEW_2026_SOURCE, basis: "monthly household kWh", sameRule: true,
          tiers: STATE_TAX_TIERS.map(([kwh], i) => ({kwh, centsPerKwh: GLENVIEW_CENTS[i]})),
          allocation: "T(other household kWh + EV kWh) - T(other household kWh)",
          effectiveFrom: GLENVIEW_EFFECTIVE_FROM, verifiedThrough: GLENVIEW_VERIFIED_THROUGH,
          historicalAssumptionMonths: [],
          sources: [{url: GLENVIEW_2024_SOURCE, effectiveFrom: "2024-01-01"}, {url: RIDER_TAX_SOURCE, effectiveFrom: "2025-01-01"},
            {url: GLENVIEW_2026_SOURCE, effectiveFrom: "2026-10-01", checkedThrough: "2026-11-05"}],
          note: "The same sourced Glenview rates apply to both supply plans. Dates outside the verified source range carry the schedule as a flagged historical assumption. No extra collection percentage is added.",
        } : {id: "user-flat-municipal-tax", centsPerKwh, status: "user-assumption", basis: "EV kWh",
          source: "User-entered scenario rate for " + plan + " supply; not a verified municipal tariff.",
          url: null, tiers: [{kwh: null, centsPerKwh}], effectiveFrom: null, verifiedThrough: null, historicalAssumptionMonths: []},
        note: "ComEd states that residential taxes and fees use the same rates for fixed and hourly supply. Separate municipal scenario inputs are assumptions, not evidence that statutory rates differ.",
        comparisonSource: TAX_COMPARISON_SOURCE,
      };
    }
    return result;
  }
  function taxesForPlan(plan, householdKwh, evKwh, treatment, month) {
    const rule = treatment[plan];
    if (!rule || rule.state.id !== "illinois-residential-excise") throw new RangeError("Unknown supply-plan tax treatment.");
    return {stateTax: stateTax(householdKwh + evKwh) - stateTax(householdKwh),
      municipalTax: rule.municipal.centsPerKwh === null ? glenviewTax(householdKwh + evKwh) - glenviewTax(householdKwh) : evKwh * rule.municipal.centsPerKwh / 100,
      municipalRateStatus: rule.municipal.centsPerKwh === null ? sourcedMunicipalStatus(month) : "user-assumption"};
  }
  function emptyStrategy(spec, treatment) {
    const rule = treatment[spec.supplyPlan];
    const result = {...spec, energyKwh: 0, total: 0, variableTotal: 0, accountServiceFees: 0, accountCapacity: 0,
      distributionFacilities: 0, illinoisDistribution: 0, stateTaxRuleId: rule.state.id,
      stateTaxBasis: rule.state.basis, municipalRateCentsPerKwh: rule.municipal.centsPerKwh,
      municipalRateStatus: rule.municipal.status, municipalTaxRuleId: rule.municipal.id,
      municipalTaxBasis: rule.municipal.basis, municipalTaxSourceUrl: rule.municipal.url};
    FIELDS.forEach(key => { result[key] = 0; }); return result;
  }
  function compare(data, input = {}) {
    const options = defaults(data, input), selected = data.rows.filter(r => r[0] >= options.from && r[0] <= options.to);
    const treatment = taxTreatment(options);
    const report = {options, source: data.source, taxTreatment: treatment, limitations: data.limitations || [], selectedDays: selected.length,
      chargingDays: selected.filter(r => r[1] > 0).length, comparedDays: 0, excludedDays: 0, excludedKwh: 0,
      energyKwh: 0, exclusions: [], strategies: SPECS.map(spec => emptyStrategy(spec, treatment)), months: [], timingSavings: 0, planSavings: 0};
    const coverage = new Map(), months = new Map();
    function cover(date) {
      const month = date.slice(0, 7); if (!coverage.has(month)) coverage.set(month, new Set()); coverage.get(month).add(date);
    }
    selected.forEach(row => cover(row[0]));
    function monthly(month) {
      if (!months.has(month)) months.set(month, {month, strategies: SPECS.map(spec => emptyStrategy(spec, treatment))});
      return months.get(month);
    }
    for (const row of selected) {
      if (row[1] <= 0) continue;
      const result = planDay(data, row, options);
      if (!result.valid) {
        report.excludedDays++; report.excludedKwh += row[1]; report.exclusions.push({date: row[0], kwh: row[1], reason: result.reason}); continue;
      }
      report.comparedDays++; report.energyKwh += row[1];
      // This models account-day coverage, shared by all strategies even when
      // immediate charging finishes before midnight and SPARC does not.
      if (result.endDate > row[0]) cover(result.endDate);
      for (const track of result.tracks) {
        for (const h of track.hours) {
          if (h.energyKwh <= EPS) continue;
          const month = h.date.slice(0, 7), rate = data.rates[month], cost = monthly(month).strategies.find(s => s.id === track.id);
          cost.energyKwh += h.energyKwh;
          cost.supply += h.energyKwh * (track.supplyPlan === "fixed" ? rate.fixed_price + rate.pea : h.billed) / 100;
          cost.transmission += h.energyKwh * rate.transmission / 100;
          cost.distributionFacilities += h.energyKwh * rate.distribution_facilities / 100;
          cost.illinoisDistribution += h.energyKwh * rate.il_electricity_distribution / 100;
          cost.delivery += h.energyKwh * (rate.distribution_facilities + rate.il_electricity_distribution) / 100;
          cost.riders += h.energyKwh * RIDERS.reduce((sum, key) => sum + rate[key], 0) / 100;
        }
      }
    }
    for (const [month, dates] of coverage) {
      const item = monthly(month), rate = data.rates[month];
      item.coverageDays = dates.size; item.calendarDays = calendarDays(month);
      item.coverageFraction = item.coverageDays / item.calendarDays;
      item.householdKwh = options.householdKwh * item.coverageFraction;
      item.notes = rate ? (rate.notes || "") : "Monthly rates unavailable; no charged energy included.";
      for (const cost of item.strategies) {
        Object.assign(cost, taxesForPlan(cost.supplyPlan, item.householdKwh, cost.energyKwh, treatment, month));
        const share = cost.energyKwh > 0 ? cost.energyKwh / (item.householdKwh + cost.energyKwh) : 0;
        cost.evShare = share;
        cost.accountServiceFees = rate ? (rate.customer_charge_usd_month + rate.metering_charge_usd_month + rate.low_income_discount_recovery_usd_month) * item.coverageFraction : 0;
        cost.accountCapacity = rate && cost.supplyPlan === "hourly" ? rate.capacity_rate_usd_kw_month * options.capacityKw * item.coverageFraction : 0;
        cost.serviceFees = cost.accountServiceFees * share;
        cost.capacity = cost.accountCapacity * share;
        cost.variableTotal = FIELDS.slice(0, 6).reduce((sum, key) => sum + cost[key], 0);
        cost.total = cost.variableTotal + cost.serviceFees + cost.capacity;
        const total = report.strategies.find(s => s.id === cost.id);
        for (const key of ["energyKwh", ...FIELDS, "distributionFacilities", "illinoisDistribution", "total", "variableTotal", "accountServiceFees", "accountCapacity"]) total[key] += cost[key];
      }
    }
    report.months = [...months.values()].sort((a, b) => a.month.localeCompare(b.month));
    updateMunicipalStatus(treatment, [...coverage.keys()]);
    for (const total of report.strategies) total.municipalRateStatus = treatment[total.supplyPlan].municipal.status;
    report.timingSavings = report.strategies[1].variableTotal - report.strategies[2].variableTotal;
    report.allocatedTimingDifference = report.strategies[1].total - report.strategies[2].total;
    report.planSavings = report.strategies[0].total - report.strategies[2].total;
    return report;
  }
  function csv(report) {
    const headers = ["month", "strategy", "supply_plan", "state_tax_rule_id", "state_tax_basis", "municipal_rate_cents_per_kwh", "municipal_rate_status",
      "municipal_tax_rule_id", "municipal_tax_basis", "municipal_tax_source_url",
      "energy_kwh", ...FIELDS.map(k => k + "_usd"), "distribution_facilities_usd", "il_electricity_distribution_usd", "total_usd", "variable_total_usd",
      "account_service_fees_usd", "account_capacity_usd", "ev_allocation_share", "coverage_days", "calendar_days", "other_household_kwh", "rate_notes"];
    const quote = value => '"' + String(value ?? "").replaceAll('"', '""') + '"';
    const rows = [headers];
    for (const m of report.months) for (const s of m.strategies) rows.push([m.month, s.id, s.supplyPlan, s.stateTaxRuleId, s.stateTaxBasis,
      s.municipalRateCentsPerKwh, s.municipalRateStatus, s.municipalTaxRuleId, s.municipalTaxBasis, s.municipalTaxSourceUrl, s.energyKwh,
      ...FIELDS.map(k => s[k]), s.distributionFacilities, s.illinoisDistribution, s.total, s.variableTotal, s.accountServiceFees, s.accountCapacity, s.evShare,
      m.coverageDays, m.calendarDays, m.householdKwh, m.notes]);
    return rows.map(row => row.map(v => quote(typeof v === "number" ? v.toFixed(8) : v)).join(",")).join("\r\n") + "\r\n";
  }
  return {compare, planDay, defaults, stateTax, glenviewTax, csv, fields: FIELDS.slice(), strategies: SPECS.map(s => ({...s}))};
});
