/* Pure replay presentation helpers. A paused replay keeps its supplied fraction. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SparcSimulationView = api;
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this), function () {
  "use strict";
  var EPS = 1e-7;
  function finite(value) { return typeof value === "number" && Number.isFinite(value); }
  function number(value, fallback) { return finite(value) ? value : fallback; }
  function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }

  function savingsPercent(saved, baseline) {
    return finite(saved) && finite(baseline) && baseline > EPS ? saved / baseline * 100 : null;
  }

  function motionProgress(cursor, totalSlots, fraction) {
    var count = Math.max(0, number(totalSlots, 0));
    if (!count) return 0;
    var position = clamp(number(cursor, 0), 0, count);
    var partial = position < count ? clamp(number(fraction, 0), 0, 1) : 0;
    return clamp((position + partial) / count, 0, 1);
  }

  function laneState(options) {
    options = options || {};
    var required = Math.max(0, number(options.requiredKwh, 0));
    var completed = clamp(number(options.completedKwh, 0), 0, required);
    var remaining = Math.max(0, required - completed);
    var next = Math.min(Math.max(0, number(options.nextEnergyKwh, 0)), remaining);
    var count = Math.max(0, number(options.totalSlots, 0));
    var cursor = clamp(number(options.cursor, 0), 0, count);
    var ended = cursor >= count;
    var fraction = ended ? 0 : clamp(number(options.fraction, 0), 0, 1);
    var gridKwh = Math.min(required, completed + next * fraction);
    var startSoc = clamp(number(options.startSoc, 20), 0, 100);
    var capacity = number(options.capacityKwh, 0);
    var efficiency = clamp(number(options.efficiency, .9), 0, 1);
    var soc = capacity > 0 ? clamp(startSoc + gridKwh * efficiency / capacity * 100, 0, 100) : startSoc;
    var targetSoc = capacity > 0 ? clamp(startSoc + required * efficiency / capacity * 100, 0, 100) : startSoc;
    var targetReached = gridKwh >= required - EPS;
    var charging = !!options.playing && !ended && !targetReached && next > EPS;
    var state = targetReached ? "ready" : ended ? "stopped" : charging ? "charging" :
      options.playing ? "waiting" : cursor > 0 || fraction > 0 ? "paused" : "plugged";
    return {gridKwh: gridKwh, soc: soc, targetSoc: targetSoc, targetReached: targetReached,
      charging: charging, state: state, phaseFraction: fraction,
      progress: motionProgress(cursor, count, fraction)};
  }

  return {savingsPercent: savingsPercent, laneState: laneState, motionProgress: motionProgress};
});
