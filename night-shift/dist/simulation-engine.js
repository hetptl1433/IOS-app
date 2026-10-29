/* Historical replay of sparc/{planner,strategies,backtest}.py.
 * Epoch seconds throughout; Chicago wall times come from the exported hour grid.
 * Live decisions see posted intervals after 7 minutes. Billing uses settled hours.
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SparcSimulation = api;
})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : this), function () {
  "use strict";
  var SLOT = 300, HOUR = 3600, DAY = 86400, EPS = 1e-9;
  var FALLBACK_VEHICLES = {
    "cybertruck": {id: "cybertruck", name: "Cybertruck (study configuration)", capacityKwh: 123,
      capacityNote: "123 kWh is a modeled capacity; Tesla's published rating does not distinguish usable from gross capacity.",
      batteryKwhPerMile: 0.4},
    "model-x": {id: "model-x", name: "2025 Model X Plaid (22 inch)", capacityKwh: 95,
      capacityNote: "95 kWh is an assumed usable capacity, not a verified manufacturer specification.",
      batteryKwhPerMile: 0.342}
  };
  function hourStart(t) { return Math.floor(t / HOUR) * HOUR; }
  function latestVisible(t) { return Math.floor((t - 420) / SLOT) * SLOT; }
  function addDate(day, n) {
    var d = new Date(day + "T12:00:00Z");
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }
  function finite(x) { return typeof x === "number" && Number.isFinite(x); }
  function upperBound(xs, x) {
    var lo = 0, hi = xs.length;
    while (lo < hi) { var mid = (lo + hi) >>> 1; if (xs[mid] <= x) lo = mid + 1; else hi = mid; }
    return lo;
  }
  function createMarket(data) {
    data = data || {};
    var market = {data: data, hours: new Map(), dayAhead: new Map(), hourly: new Map(),
      fiveMinute: new Map(), localHours: new Map(), days: data.days || [], vehicles: {},
      decay: (data.decay90d || data.decay || data.liveDecay || []).slice()};
    Object.keys(FALLBACK_VEHICLES).forEach(function (k) { market.vehicles[k] = FALLBACK_VEHICLES[k]; });
    var vehicles = data.vehicles || {};
    if (Array.isArray(vehicles)) vehicles.forEach(function (v) { market.vehicles[v.id] = v; });
    else Object.keys(vehicles).forEach(function (k) { market.vehicles[k] = vehicles[k]; });
    (data.hours || []).forEach(function (r) {
      var h = {time: r[0], date: r[1], localHour: r[2], dayAhead: r[3], billed: r[4],
        capacityRisk: !!r[5], release: r[6]};
      market.hours.set(h.time, h);
      if (finite(h.dayAhead)) market.dayAhead.set(h.time, h.dayAhead);
      if (finite(h.billed)) market.hourly.set(h.time, h.billed);
      var key = h.date + "|" + h.localHour;
      if (!market.localHours.has(key) || h.time < market.localHours.get(key)) market.localHours.set(key, h.time);
    });
    (data.slots || []).forEach(function (r) { if (finite(r[1])) market.fiveMinute.set(r[0], r[1]); });
    market.at = function (date, wallHour) {
      var whole = Math.floor(wallHour), fractional = (wallHour - whole) * HOUR;
      var found = market.localHours.get(date + "|" + whole);
      // ZoneInfo's spring-gap convention: nonexistent02:00 maps to actual03:00.
      if (found === undefined && whole === 2) found = market.localHours.get(date + "|3");
      return found === undefined ? null : found + fractional;
    };
    market.knownDayAhead = function (hour, now) { return knownDayAhead(market, hour, now); };
    return market;
  }
  function knownDayAhead(market, hour, now) {
    var candidates = [hour, hour - DAY];
    for (var i = 0; i < candidates.length; i++) {
      var h = market.hours.get(candidates[i]);
      if (h && finite(h.dayAhead) && h.release <= now) return h.dayAhead;
    }
    return null;
  }
  function tariffCents(tariff, localHour) {
    if (tariff === "energy-only") return 0;
    if (tariff === "standard" || tariff === "Standard delivery") return 6.228;
    if (localHour < 6) return 2.984;
    if (localHour < 13) return 4.009;
    if (localHour < 19) return 10.712;
    if (localHour < 21) return 3.747;
    return 2.984;
  }
  function normalizeConfig(config) {
    config = config || {};
    var c = Object.assign({}, config);
    c.vehicle = c.vehicle || c.vehicleId || "cybertruck";
    c.arrive = Number(c.arrive !== undefined ? c.arrive : (c.arrivalHour !== undefined ? c.arrivalHour : 20));
    c.depart = Number(c.depart !== undefined ? c.depart : (c.departureHour !== undefined ? c.departureHour : 7));
    c.power = Number(c.power !== undefined ? c.power : (c.powerKw !== undefined ? c.powerKw : 11.5));
    c.miles = Number(c.miles !== undefined ? c.miles : 75);
    c.efficiency = Number(c.efficiency !== undefined ? c.efficiency : .9);
    if (c.gridEnergyKwh !== undefined) c.gridEnergyKwh = Number(c.gridEnergyKwh);
    if (c.maxBatteryKwh !== undefined) c.maxBatteryKwh = Number(c.maxBatteryKwh);
    c.reserveMinutes = Number(c.reserveMinutes !== undefined ? c.reserveMinutes : (c.reserveMin !== undefined ? c.reserveMin : 30));
    c.tariff = c.tariff || "time-of-day";
    c.strategy = c.strategy || "day-ahead";
    var timer = /^timer-(\d{2})(\d{2})$/.exec(c.strategy);
    if (timer) { c.timerHour = Number(timer[1]) + Number(timer[2]) / 60; c.strategy = "timer"; }
    var cap = /^cap-(-?\d+(?:\.\d+)?)$/.exec(c.strategy);
    if (cap) { c.capCents = Number(cap[1]); c.strategy = "cap"; }
    c.timerHour = Number(c.timerHour !== undefined ? c.timerHour : 0);
    c.capCents = Number(c.capCents !== undefined ? c.capCents : 3);
    c.chargeStartHour = Number(c.chargeStartHour !== undefined ? c.chargeStartHour : 22);
    c.chargeStopHour = Number(c.chargeStopHour !== undefined ? c.chargeStopHour : 6);
    c.chargeWindowEnabled = c.chargeWindowEnabled === true;
    return c;
  }
  function buildSession(market, config) {
    var c = normalizeConfig(config), reason = null;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(c.date || "")) reason = "Choose a historical date.";
    else if (!market.vehicles[c.vehicle]) reason = "Unknown vehicle.";
    else if (c.gridEnergyKwh !== undefined && (!finite(c.gridEnergyKwh) || c.gridEnergyKwh <= 0)) reason = "Grid energy must be greater than zero.";
    else if (c.gridEnergyKwh === undefined && (!finite(c.miles) || c.miles <= 0)) reason = "Daily miles must be greater than zero.";
    else if (!finite(c.power) || c.power <= 0) reason = "Charging power must be greater than zero.";
    else if (!finite(c.efficiency) || c.efficiency <= 0 || c.efficiency > 1) reason = "Charging efficiency must be between zero and one.";
    else if (c.maxBatteryKwh !== undefined && (!finite(c.maxBatteryKwh) || c.maxBatteryKwh <= 0)) reason = "Available battery headroom must be greater than zero.";
    else if (!finite(c.arrive) || !finite(c.depart) || c.arrive < 0 || c.arrive >= 24 || c.depart < 0 || c.depart >= 24) reason = "Arrival and departure must be hours between0 and24.";
    else if (!finite(c.reserveMinutes) || c.reserveMinutes < 0) reason = "Departure reserve must be nonnegative.";
    else if ((c.chargeWindowEnabled || c.strategy === "schedule") && (!finite(c.chargeStartHour) || !finite(c.chargeStopHour) || c.chargeStartHour < 0 || c.chargeStartHour >= 24 || c.chargeStopHour < 0 || c.chargeStopHour >= 24)) reason = "Choose valid charging start and stop times.";
    else if ((c.chargeWindowEnabled || c.strategy === "schedule") && c.chargeStartHour === c.chargeStopHour) reason = "Charging start and stop times must be different.";
    else if ((c.chargeWindowEnabled || c.strategy === "schedule") && [c.chargeStartHour, c.chargeStopHour].some(function (hour) { return Math.abs(hour * 12 - Math.round(hour * 12)) > EPS; })) reason = "Choose charging start and stop times in five-minute steps.";
    if (reason) return {valid: false, reason: reason, config: c};
    var vehicle = market.vehicles[c.vehicle], rate = Number(vehicle.batteryKwhPerMile);
    if (c.gridEnergyKwh === undefined && (!finite(rate) || rate <= 0)) return {valid: false, reason: "Vehicle energy consumption is unavailable.", config: c};
    var leaveDate = c.depart <= c.arrive ? addDate(c.date, 1) : c.date;
    // Explicit boundaries preserve dated history windows, including repeated DST hours.
    var explicit = c.startEpoch !== undefined || c.endEpoch !== undefined;
    var arrival = explicit ? c.startEpoch : market.at(c.date, c.arrive);
    var departure = explicit ? c.endEpoch : market.at(leaveDate, c.depart);
    if (explicit && (!finite(arrival) || !finite(departure))) return {valid: false, reason: "Session boundaries must be finite epoch seconds.", config: c};
    if (arrival === null || departure === null || departure <= arrival) return {valid: false, reason: "This session falls outside the historical data.", config: c};
    if (arrival % SLOT || departure % SLOT) return {valid: false, reason: "Choose times on a five-minute boundary.", config: c};
    var hours = [];
    for (var h = hourStart(arrival); h < departure; h += HOUR) hours.push(h);
    var gridKwh = c.gridEnergyKwh !== undefined ? c.gridEnergyKwh : c.miles * rate / c.efficiency;
    var batteryKwh = c.gridEnergyKwh !== undefined ? gridKwh * c.efficiency : c.miles * rate;
    if (c.maxBatteryKwh !== undefined && batteryKwh > c.maxBatteryKwh + EPS) return {valid: false, reason: "The requested battery energy exceeds the available battery headroom.", config: c};
    var session = {valid: true, reason: null, config: c, vehicle: vehicle, start: arrival, end: departure,
      arrival: arrival, departure: departure, hours: hours, batteryKwh: batteryKwh,
      requiredGridKwh: gridKwh, gridKwh: gridKwh, energyKwh: gridKwh,
      powerKw: c.power, slotKwh: c.power * SLOT / HOUR, reserveSeconds: c.reserveMinutes * 60};
    session.chargingStart = arrival;
    session.chargingEnd = departure;
    session.allowedSlots = null;
    session.availableSeconds = departure - arrival;
    for (var i = 0; i < hours.length; i++) {
      h = hours[i];
      if (!market.hourly.has(h) || !market.dayAhead.has(h) || !market.dayAhead.has(h - DAY)) {
        session.valid = false; session.reason = "Incomplete historical prices for this window; choose another date or time."; break;
      }
    }
    if (session.valid && (c.chargeWindowEnabled || c.strategy === "schedule")) {
      var allowed = [];
      for (var t = arrival; t < departure; t += SLOT) {
        var wall = market.hours.get(hourStart(t)).localHour + (t - hourStart(t)) / HOUR;
        var afterStart = wall + EPS >= c.chargeStartHour, beforeStop = wall < c.chargeStopHour - EPS;
        if (c.chargeStartHour < c.chargeStopHour ? afterStart && beforeStop : afterStart || beforeStop) allowed.push(t);
      }
      if (!allowed.length) {
        session.valid = false;
        session.reason = "The charging window does not overlap the time the vehicle is plugged in.";
      } else {
        session.allowedSlots = new Set(allowed);
        session.chargingSlotTimes = allowed;
        session.chargingStart = allowed[0];
        session.chargingEnd = allowed[allowed.length - 1] + SLOT;
        session.availableSeconds = allowed.length * SLOT;
      }
    }
    return session;
  }
  function plan(need, costs, blocked, charging, stickiness) {
    var schedule = new Map();
    if (need.energyKwh <= EPS || need.now >= need.departure) return schedule;
    var full = need.powerKw * SLOT / HOUR, soft = need.departure - need.reserveSeconds;
    var current = hourStart(need.now), blocks = [];
    for (var hour = current; hour < need.departure; hour += HOUR) {
      var price = costs.get(hour);
      if (hour === current) price += charging ? -(stickiness || 0) : (stickiness || 0);
      var first = Math.max(hour, need.now), end = Math.min(hour + HOUR, need.departure);
      var split = soft <= first ? first : Math.min(end, first + Math.ceil((soft - first) / SLOT) * SLOT);
      [[false, first, split], [true, split, end]].forEach(function (part) {
        var slots = [];
        for (var t = part[1]; t < part[2]; t += SLOT) if (!need.allowedSlots || need.allowedSlots.has(t)) slots.push(t);
        if (slots.length) blocks.push({key: [blocked.has(hour) ? 1 : 0, part[0] ? 1 : 0, price, hour], slots: slots});
      });
    }
    blocks.sort(function (a, b) { for (var k = 0; k < 4; k++) if (a.key[k] !== b.key[k]) return a.key[k] - b.key[k]; return 0; });
    var chosen = [], left = need.energyKwh;
    for (var i = 0; i < blocks.length; i++) {
      var energy = Math.min(left, blocks[i].slots.length * full);
      chosen.push({slots: blocks[i].slots, energy: energy}); left -= energy;
      if (left <= EPS) break;
    }
    if (!chosen.length) return schedule;
    for (i = 0; i < chosen.length - 1; i++) chosen[i].slots.forEach(function (t) { schedule.set(t, full); });
    var last = chosen[chosen.length - 1], count = Math.max(1, Math.min(last.slots.length, Math.ceil(last.energy / full - EPS)));
    var continued = schedule.has(last.slots[0] - SLOT) || (last.slots[0] === need.now && charging);
    var slots = continued ? last.slots.slice(0, count) : last.slots.slice(-count);
    var partial = continued ? slots[slots.length - 1] : slots[0];
    slots.forEach(function (t) { schedule.set(t, full); });
    schedule.set(partial, last.energy - (count - 1) * full);
    return schedule;
  }
  function manualEnergy(manual, now, remaining, session) {
    var value;
    if (typeof manual === "function") value = manual(now, remaining, session);
    else if (manual instanceof Set) value = manual.has(now);
    else if (manual && typeof manual.get === "function") value = manual.get(now);
    else value = manual && manual[now];
    return value === true ? session.slotKwh : (finite(value) ? value : 0);
  }
  function invalidResult(session, reason) {
    return {valid: false, reason: reason || session.reason, start: session.start || null, end: session.end || null,
      session: session, slots: [], totalCost: null, costUsd: null, supplyUsd: null, deliveryUsd: null,
      energyKwh: 0, ready: false, requiredGridKwh: session.requiredGridKwh || 0, starts: 0, pauses: 0, riskKwh: 0};
  }
  function simulate(market, config, options) {
    options = options || {};
    var session = buildSession(market, config);
    if (!session.valid) return invalidResult(session);
    var c = session.config, strategy = c.strategy;
    if (["charge-now", "timer", "schedule", "cap", "day-ahead", "live", "hindsight", "manual"].indexOf(strategy) < 0) return invalidResult(session, "Unknown charging strategy.");
    if (strategy === "live" && !market.decay.length) return invalidResult(session, "Live-model calibration is unavailable.");
    var full = session.slotKwh, remaining = session.requiredGridKwh, charging = false;
    var blocked = new Set(), cents = new Map(), releases = [], changes = new Set();
    session.hours.forEach(function (h) {
      var meta = market.hours.get(h), prior = market.hours.get(h - DAY);
      if (meta.capacityRisk) blocked.add(h);
      cents.set(h, tariffCents(c.tariff, meta.localHour));
      releases.push(meta.release); changes.add(meta.release); if (prior) changes.add(prior.release);
    });
    releases.sort(function (a, b) { return a - b; });
    changes = Array.from(changes).sort(function (a, b) { return a - b; });
    var known = new Map(), knownVersion = null, schedule = new Map(), version = null;
    var begins = session.chargingStart;
    if (strategy === "timer") {
      if (!finite(c.timerHour) || c.timerHour < 0 || c.timerHour >= 24) return invalidResult(session, "Timer hour must be between0 and24.");
      var day = market.hours.get(hourStart(session.start)).date;
      var target = market.at(day, c.timerHour);
      if (target > session.start) day = addDate(day, -1);
      var priorTarget = market.at(day, c.timerHour);
      if (priorTarget === null) return invalidResult(session, "Timer history is unavailable.");
      target = session.start - priorTarget <= 6 * HOUR ? session.start : market.at(addDate(day, 1), c.timerHour);
      var duration = Math.ceil(remaining / full - EPS) * SLOT;
      var latest = session.chargingEnd - session.reserveSeconds - duration;
      if (latest < session.chargingStart) latest = session.chargingEnd - duration;
      if (session.allowedSlots) {
        var count = Math.ceil(remaining / full - EPS), times = session.chargingSlotTimes;
        var beforeReserve = upperBound(times, session.chargingEnd - session.reserveSeconds - SLOT);
        var finishAt = beforeReserve >= count ? beforeReserve : times.length;
        latest = times[Math.max(0, finishAt - count)];
      }
      begins = Math.max(session.chargingStart, Math.min(target, latest));
    }
    function costMap(now) {
      var expected = new Map(), changed = upperBound(changes, now);
      if (knownVersion !== changed) {
        knownVersion = changed; known = new Map();
        session.hours.forEach(function (h) { known.set(h, knownDayAhead(market, h, now)); });
      }
      session.hours.forEach(function (h) { expected.set(h, strategy === "hindsight" ? market.hourly.get(h) : known.get(h)); });
      if (strategy === "live") {
        var latest = latestVisible(now), posted = market.fiveMinute.get(latest);
        var reference = knownDayAhead(market, hourStart(latest), now);
        if (posted !== undefined && reference !== null) {
          var deviation = posted - reference, reach = latest + market.decay.length * SLOT;
          session.hours.forEach(function (h) {
            if (h < hourStart(now) || h > reach) return;
            var total = 0;
            for (var t = h; t < h + HOUR; t += SLOT) {
              if (t <= latest && market.fiveMinute.has(t)) total += market.fiveMinute.get(t);
              else {
                var k = (t - latest) / SLOT, persists = k >= 1 && k <= market.decay.length ? market.decay[k - 1] : 0;
                total += expected.get(h) + persists * deviation;
              }
            }
            expected.set(h, total / 12);
          });
        }
      }
      var costs = new Map();
      expected.forEach(function (p, h) { costs.set(h, p + cents.get(h)); });
      return {costs: costs, expected: expected};
    }
    var result = {valid: true, reason: null, start: session.start, end: session.end, session: session,
      slots: [], totalCost: 0, costUsd: 0, supplyUsd: 0, deliveryUsd: 0, energyKwh: 0,
      ready: false, requiredGridKwh: session.requiredGridKwh, starts: 0, pauses: 0, riskKwh: 0};
    var forecast = null;
    for (var now = session.start; now < session.end; now += SLOT) {
      var hour = hourStart(now), kwh = 0, prices = null;
      if (remaining > EPS && (!session.allowedSlots || session.allowedSlots.has(now))) {
        if (strategy === "charge-now") kwh = full;
        else if (strategy === "timer") kwh = now >= begins ? full : 0;
        else if (strategy === "schedule") kwh = full;
        else if (strategy === "manual") kwh = manualEnergy(options.manual || c.manual || c.schedule, now, remaining, session);
        else if (strategy === "cap") {
          var urgent = session.allowedSlots ? Math.ceil(remaining / full - EPS) >=
            upperBound(session.chargingSlotTimes, session.chargingEnd - session.reserveSeconds - SLOT) - upperBound(session.chargingSlotTimes, now - 1) :
            now + Math.ceil(remaining / full - EPS) * SLOT >= session.end - session.reserveSeconds;
          if (urgent) kwh = full;
          else {
            var posted = market.fiveMinute.get(latestVisible(now));
            kwh = posted === undefined ? (charging ? full : 0) : (posted <= c.capCents ? full : 0);
          }
        } else {
          var nextVersion = strategy === "live" ? now : (strategy === "hindsight" ? 0 : upperBound(releases, now));
          prices = costMap(now);
          if (version !== nextVersion) {
            version = nextVersion;
            schedule = plan({now: now, departure: session.chargingEnd, energyKwh: remaining, powerKw: c.power, allowedSlots: session.allowedSlots,
              reserveSeconds: strategy === "hindsight" ? 0 : session.reserveSeconds}, prices.costs, blocked, charging, strategy === "live" ? .5 : 0);
          }
          kwh = schedule.get(now) || 0;
        }
        kwh = Math.max(0, Math.min(kwh, full, remaining));
      }
      forecast = prices ? prices.expected.get(hour) : knownDayAhead(market, hour, now);
      var on = kwh > EPS;
      if (on && !charging) result.starts++;
      else if (charging && !on) result.pauses++;
      var supply = kwh * market.hourly.get(hour) / 100, delivery = kwh * cents.get(hour) / 100;
      result.supplyUsd += supply; result.deliveryUsd += delivery;
      result.energyKwh += kwh; if (blocked.has(hour)) result.riskKwh += kwh;
      remaining -= kwh;
      charging = on && kwh >= full - EPS && remaining > EPS;
      if (on && !charging && remaining > EPS) result.pauses++;
      result.totalCost = result.supplyUsd + result.deliveryUsd;
      var meta = market.hours.get(hour), visible = market.fiveMinute.get(latestVisible(now));
      result.slots.push({time: now, hour: hour, date: meta.date, wallHour: meta.localHour,
        energy: kwh, energyKwh: kwh, supply: supply, delivery: delivery, cost: supply + delivery,
        cumulativeCost: result.totalCost, cumulativeKwh: result.energyKwh, remaining: Math.max(0, remaining),
        charging: on, forecast: forecast, visiblePrice: visible === undefined ? null : visible,
        billedPrice: market.hourly.get(hour), deliveryCents: cents.get(hour),
        dayAheadPrice: knownDayAhead(market, hour, now), capacityRisk: blocked.has(hour)});
    }
    result.ready = remaining <= 1e-6;
    result.costUsd = result.totalCost;
    return result;
  }
  function compare(market, config, strategies) {
    return (strategies || ["charge-now", "timer-0000", "day-ahead", "live"]).map(function (strategy) {
      return {strategy: strategy, result: simulate(market, Object.assign({}, config, {strategy: strategy}))};
    });
  }
  return {createMarket: createMarket, buildSession: buildSession, simulate: simulate, compare: compare,
    plan: plan, knownDayAhead: knownDayAhead, tariffCents: tariffCents,
    latestVisibleInterval: latestVisible, normalizeConfig: normalizeConfig, SLOT: SLOT, HOUR: HOUR};
});
