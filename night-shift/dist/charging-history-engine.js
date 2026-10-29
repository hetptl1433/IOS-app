/* Recorded charging sessions adapted to the shared SPARC planner and billing engine. */
(function (root, factory) {
  "use strict";
  var engine = typeof module === "object" && module.exports ? require("./simulation-engine.js") : root.SparcSimulation;
  var api = factory(engine);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SparcChargingHistory = api;
})(typeof window !== "undefined" ? window : globalThis, function (Engine) {
  "use strict";
  const SLOT = 300, HOUR = 3600, EPS = 1e-6;
  const BASELINE_TARIFF = "standard";
  const strategies = [
    {id: "recorded", label: "Recorded pattern · estimated", color: "#8b7868"},
    {id: "charge-now", label: "Immediate · standard delivery", color: "#87968d"},
    {id: "timer-0100", label: "1 AM timer", color: "#c9954f"},
    {id: "day-ahead", label: "SPARC day-ahead plan", color: "#678b32"},
    {id: "hindsight", label: "Perfect hindsight · reference", color: "#299a91"},
  ];

  function createMarket(data, history) {
    const market = Engine.createMarket(Object.assign({}, data, {hours: data.hours.concat(history.hours || [])}));
    market.vehicles.history = {id: "history", name: "Recorded vehicle"};
    return market;
  }
  function nextDate(day) {
    const date = new Date(day + "T12:00:00Z");
    date.setUTCDate(date.getUTCDate() + 1);
    return date.toISOString().slice(0, 10);
  }
  function configuration(market, row, options, nextRow) {
    options = options || {};
    if (row.issue) return {valid: false, reason: row.issue};
    if (row.dcFast || row.location !== "Home") return {valid: false, reason: "Only home AC charging is modeled."};
    if (!Number.isFinite(row.start) || !Number.isFinite(row.end) || row.end <= row.start || !Number.isFinite(row.gridKwh) || row.gridKwh <= 0) {
      return {valid: false, reason: "This record needs positive grid energy and a valid time window."};
    }
    const start = Math.floor(row.start / SLOT) * SLOT;
    const recordedEnd = Math.ceil(row.end / SLOT) * SLOT;
    let end = recordedEnd;
    let capped = false;
    if (options.window === "morning") {
      const hour = options.departureHour === undefined ? 7 : Number(options.departureHour);
      if (!Number.isInteger(hour) || hour < 0 || hour > 23) return {valid: false, reason: "Choose a departure hour between 0 and 23."};
      let day = row.startLocal.slice(0, 10);
      let deadline = market.at(day, hour);
      if (deadline !== null && deadline <= start) deadline = market.at(nextDate(day), hour);
      if (deadline === null) return {valid: false, reason: "No historical prices for the assumed departure."};
      // Do not count hypothetical availability already assigned to a later record.
      if (nextRow && Number.isFinite(nextRow.start)) {
        const nextStart = Math.floor(nextRow.start / SLOT) * SLOT;
        if (nextStart > start && nextStart < deadline) { deadline = nextStart; capped = true; }
      }
      end = Math.max(recordedEnd, deadline);
    }
    if (!options.window || options.window === "recorded" || options.window === "morning") {
      const minimumPower = row.gridKwh / ((recordedEnd - start) / HOUR);
      const observedPower = Number.isFinite(row.powerKw) && row.powerKw > 0 ? row.powerKw : 0;
      // Inconsistent peak/energy measurements are explicitly flagged in the UI.
      const power = options.powerKw === undefined || options.powerKw === "recorded" ? Math.max(observedPower, minimumPower) : Number(options.powerKw);
      if (!Number.isFinite(power) || power <= 0) return {valid: false, reason: "Choose positive charging power."};
      const config = {date: row.startLocal.slice(0, 10), vehicle: "history", startEpoch: start, endEpoch: end,
        gridEnergyKwh: row.gridKwh, power: power, efficiency: 1, reserveMinutes: 0,
        tariff: options.tariff || "time-of-day", strategy: "day-ahead"};
      return {valid: true, config, recordedEnd, capped, powerAdjusted: observedPower < minimumPower - EPS,
        extended: end > recordedEnd, recordedDurationHours: (recordedEnd - start) / HOUR};
    }
    return {valid: false, reason: "Unknown charging window."};
  }
  function simulate(market, row, options, nextRow) {
    const prepared = configuration(market, row, options, nextRow);
    if (!prepared.valid) return {valid: false, row, reason: prepared.reason, tracks: [], eligible: false};
    const config = prepared.config;
    const session = Engine.buildSession(market, config);
    if (!session.valid) return {valid: false, row, reason: session.reason, tracks: [], eligible: false};
    const flatSlotKwh = row.gridKwh / ((prepared.recordedEnd - config.startEpoch) / SLOT);
    const tracks = strategies.map(function (strategy) {
      const recorded = strategy.id === "recorded";
      const tariff = strategy.id === "charge-now" ? BASELINE_TARIFF : config.tariff;
      const result = Engine.simulate(market, Object.assign({}, config, {strategy: recorded ? "manual" : strategy.id, tariff}),
        recorded ? {manual: function (now) { return now < prepared.recordedEnd ? flatSlotKwh : 0; }} : undefined);
      const rateLabel = tariff === "standard" ? "standard delivery" : "time-of-day delivery";
      return Object.assign({}, strategy, {tariff, label: strategy.id === "charge-now" ? strategy.label : `${strategy.label} · ${rateLabel}`, result});
    });
    const immediate = tracks.find(t => t.id === "charge-now").result;
    const sparc = tracks.find(t => t.id === "day-ahead").result;
    const eligible = immediate.valid && sparc.valid && immediate.ready && sparc.ready && Math.abs(immediate.energyKwh - sparc.energyKwh) < EPS;
    return {valid: true, row, config, session, prepared, tracks, eligible,
      saving: eligible ? immediate.totalCost - sparc.totalCost : null};
  }
  function summarize(results) {
    const summary = {total: results.length, comparable: 0, skipped: 0, shortfall: 0,
      energyKwh: 0, baselineCost: 0, sparcCost: 0, saving: 0, loggedCost: 0};
    results.forEach(function (run) {
      if (!run.valid) { summary.skipped++; return; }
      if (!run.eligible) { summary.shortfall++; return; }
      summary.comparable++;
      summary.energyKwh += run.row.gridKwh;
      summary.loggedCost += run.row.loggedCost;
      summary.baselineCost += run.tracks.find(t => t.id === "charge-now").result.totalCost;
      summary.sparcCost += run.tracks.find(t => t.id === "day-ahead").result.totalCost;
      summary.saving += run.saving;
    });
    return summary;
  }
  function exportCsv(results, options) {
    const fields = ["session_id","start_chicago","end_chicago","window","departure_hour","baseline_tariff","planned_tariff",
      "modeled_start_utc","modeled_end_utc","modeled_power_kw","power_adjusted","morning_extension_capped",
      "grid_kwh","battery_kwh","logged_csv_cost_usd","recorded_pattern_estimate_usd","immediate_usd",
      "timer_1am_usd","sparc_usd","hindsight_usd","immediate_delivered_kwh","sparc_delivered_kwh",
      "saving_usd","status","reason"];
    const lines = [fields];
    results.forEach(function (result) {
      const row = result.row;
      const track = id => result.tracks.find(item => item.id === id)?.result;
      const fixed = value => Number.isFinite(value) ? value.toFixed(6) : "";
      const cost = id => fixed(track(id)?.totalCost);
      const iso = epoch => Number.isFinite(epoch) ? new Date(epoch * 1000).toISOString() : "";
      lines.push([row.id,row.startLocal,row.endLocal,options.window || "recorded",
        options.window === "morning" ? options.departureHour ?? 7 : "",BASELINE_TARIFF,result.config?.tariff || options.tariff || "time-of-day",
        iso(result.config?.startEpoch),iso(result.config?.endEpoch),result.config?.power ?? "",
        result.prepared?.powerAdjusted ?? "",result.prepared?.capped ?? "",row.gridKwh,row.batteryKwh,row.loggedCost,
        cost("recorded"),cost("charge-now"),cost("timer-0100"),cost("day-ahead"),cost("hindsight"),
        fixed(track("charge-now")?.energyKwh),fixed(track("day-ahead")?.energyKwh),
        result.eligible ? fixed(result.saving) : "",!result.valid ? "skipped" : result.eligible ? "compared" : "shortfall",
        result.reason || (!result.eligible ? "Not all requested energy could be delivered." : "")]);
    });
    const quote = value => `"${String(value).replace(/"/g, '""')}"`;
    return lines.map(line => line.map(quote).join(",")).join("\r\n");
  }
  function dailyRows(results, options) {
    options = options || {};
    const groups = new Map();
    results.forEach(function (result) {
      const day = result.row.startLocal.slice(0, 10);
      if (!groups.has(day)) groups.set(day, []);
      groups.get(day).push(result);
    });
    const dates = Array.from(groups.keys()).sort();
    const start = options.startDate || dates[0], end = options.endDate || dates[dates.length - 1];
    if (!start || !end) return [];
    if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || start > end) throw new RangeError("Choose a valid daily export date range.");
    const days = [];
    let baseline = 0, sparc = 0, saving = 0;
    for (let day = start; day <= end; day = nextDate(day)) {
      const summary = summarize(groups.get(day) || []);
      const unavailable = summary.total > 0 && summary.comparable === 0;
      baseline += summary.baselineCost;
      sparc += summary.sparcCost;
      saving += summary.saving;
      days.push({date: day, total: summary.total, comparable: summary.comparable,
        skipped: summary.skipped, shortfall: summary.shortfall, energyKwh: summary.energyKwh,
        baselineCost: unavailable ? null : summary.baselineCost,
        sparcCost: unavailable ? null : summary.sparcCost, saving: unavailable ? null : summary.saving,
        savingPct: summary.baselineCost > 0 ? summary.saving / summary.baselineCost * 100 : null,
        cumulativeBaselineCost: baseline, cumulativeSparcCost: sparc, cumulativeSaving: saving,
        cumulativeSavingPct: baseline > 0 ? saving / baseline * 100 : null,
        status: !summary.total ? "no_sessions" : unavailable ? "no_comparable_sessions" :
          summary.skipped || summary.shortfall ? "partial" : "compared"});
    }
    return days;
  }
  function exportDailyCsv(results, options) {
    options = options || {};
    const fields = ["session_start_date_chicago","standard_immediate_usd","sparc_planned_usd","saving_usd","saving_pct",
      "grid_kwh","sessions_compared","sessions_skipped","sessions_shortfall","sessions_total",
      "cumulative_standard_usd","cumulative_sparc_usd","cumulative_saving_usd","cumulative_saving_pct",
      "baseline_tariff","planned_tariff","window","status"];
    const fixed = value => Number.isFinite(value) ? value.toFixed(6) : "";
    const tariff = results.find(result => result.config)?.config.tariff || options.tariff || "time-of-day";
    const lines = [fields];
    dailyRows(results, options).forEach(function (day) {
      lines.push([day.date,fixed(day.baselineCost),fixed(day.sparcCost),fixed(day.saving),fixed(day.savingPct),
        fixed(day.energyKwh),day.comparable,day.skipped,day.shortfall,day.total,
        fixed(day.cumulativeBaselineCost),fixed(day.cumulativeSparcCost),fixed(day.cumulativeSaving),fixed(day.cumulativeSavingPct),
        BASELINE_TARIFF,tariff,options.window || "recorded",day.status]);
    });
    const quote = value => `"${String(value).replace(/"/g, '""')}"`;
    return lines.map(line => line.map(quote).join(",")).join("\r\n");
  }
  return {createMarket, configuration, simulate, summarize, exportCsv, dailyRows, exportDailyCsv, strategies};
});
