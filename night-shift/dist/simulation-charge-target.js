/* Battery targets shared by the controls and the historical charging model. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SparcChargeTarget = api;
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this), function () {
  "use strict";
  var DEFAULT_TARGET_SOC = 80, MIN_TARGET_SOC = 55, MAX_TARGET_SOC = 100, TARGET_STEP = 5;

  function profile(options) {
    options = options || {};
    var capacity = options.capacityKwh;
    var usage = options.usageId === undefined ? "heavy" : options.usageId;
    var target = options.targetSoc === undefined ? DEFAULT_TARGET_SOC : options.targetSoc;
    var efficiency = options.efficiency === undefined ? .9 : options.efficiency;
    if (typeof capacity !== "number" || !Number.isFinite(capacity) || capacity <= 0) {
      throw new RangeError("Battery capacity must be a positive finite number.");
    }
    if (typeof efficiency !== "number" || !Number.isFinite(efficiency) || efficiency <= 0 || efficiency > 1) {
      throw new RangeError("Charging efficiency must be greater than zero and at most one.");
    }
    if (usage !== "average" && usage !== "heavy") throw new RangeError("Choose Average or Heavy usage.");
    if (typeof target !== "number" || !Number.isFinite(target) || target < MIN_TARGET_SOC ||
        target > MAX_TARGET_SOC || (target - MIN_TARGET_SOC) % TARGET_STEP !== 0) {
      throw new RangeError("Choose a charging target from 55% to 100% in 5% steps.");
    }
    var start = usage === "heavy" ? 20 : 50;
    var battery = capacity * (target - start) / 100;
    return {startSoc: start, targetSoc: target, batteryKwh: battery,
      gridEnergyKwh: battery / efficiency, maxBatteryKwh: capacity * (100 - start) / 100};
  }

  return {profile: profile, DEFAULT_TARGET_SOC: DEFAULT_TARGET_SOC, MIN_TARGET_SOC: MIN_TARGET_SOC,
    MAX_TARGET_SOC: MAX_TARGET_SOC, TARGET_STEP: TARGET_STEP};
});
