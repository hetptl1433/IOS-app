/* Norwegian connection reports replayed as explicit ComEd calendar scenarios. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SparcResidential = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";
  const HOUR = 3600;
  // Retain season and weekday. The nearest matching weekday stays in the source
  // month, so boundary dates never silently change the chosen replay year.
  function scenarioDate(source, year) {
    const original = new Date(source.slice(0, 10) + "T12:00:00Z");
    const month = original.getUTCMonth(), day = original.getUTCDate();
    const last = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const candidates = [];
    for (let d = 1; d <= last; d++) {
      const candidate = new Date(Date.UTC(year, month, d, 12));
      if (candidate.getUTCDay() === original.getUTCDay()) candidates.push(candidate);
    }
    candidates.sort((a, b) => Math.abs(a.getUTCDate() - day) - Math.abs(b.getUTCDate() - day) || a - b);
    return candidates[0].toISOString().slice(0, 10);
  }
  function decode(bundle) {
    return bundle.rows.map(values => Object.fromEntries(bundle.columns.map((key, i) => [key, values[i]])));
  }
  function adapt(market, source, year) {
    if (!Number.isInteger(year) || ![2025, 2026].includes(year)) throw new RangeError("Choose a supported ComEd replay year.");
    const day = scenarioDate(source.startLocal, year);
    const parts = source.startLocal.slice(11).split(":").map(Number);
    const wallHour = parts[0] + parts[1] / 60 + parts[2] / HOUR;
    const start = market.at(day, wallHour);
    const duration = source.sourceEnd === null ? null : source.sourceEnd - source.sourceStart;
    let issue = source.issue;
    if (start === null) issue = issue || "No ComEd price calendar for the mapped date.";
    if (start !== null) {
      const hour = Math.floor(start / HOUR) * HOUR, current = market.hours.get(hour);
      const matches = h => h && h.date === day && h.localHour === parts[0];
      if (!matches(current) || matches(market.hours.get(hour - HOUR)) || matches(market.hours.get(hour + HOUR))) {
        issue = issue || "Ambiguous or nonexistent Chicago plug-in time in the mapped scenario.";
      }
    }
    return Object.assign({}, source, {sourceKind: "residential", sourceTimezone: "Europe/Oslo",
      priceYear: year, modeledDate: day, start, end: start !== null && duration !== null ? start + duration : null,
      batteryKwh: null, startSoc: null, endSoc: null, loggedCost: null,
      dcFast: false, location: "Home", issue});
  }
  return {scenarioDate, decode, adapt};
});
