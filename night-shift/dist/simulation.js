/* SPARC Night Shift: historical replay, a manual charging challenge, and a full-study lab. */
"use strict";
(() => {
  const DATA = window.SPARC_SIM_DATA;
  const Engine = window.SparcSimulation;
  const View = window.SparcSimulationView;
  const ChargeTarget = window.SparcChargeTarget;
  const $ = (id) => document.getElementById(id);
  const text = (id, value) => { const el = $(id), next = String(value); if (el && el.textContent !== next) el.textContent = next; };
  if (!DATA || !Engine || !View || !ChargeTarget) {
    text("configuration-note", "The replay files could not load. Keep simulation-data.js and simulation-engine.js beside this page, then reload.");
    return;
  }
  const market = Engine.createMarket(DATA);
  const FLEET = {
    bus: {label:"Electric bus", capacityKwh:440, batteryKwhPerMile:1, power:125},
    commercial: {label:"Commercial vehicle", capacityKwh:200, batteryKwhPerMile:1, power:50},
  };
  Object.assign(market.vehicles, FLEET);
  const isFleet = (c) => !!FLEET[c.vehicleId];
  const baselineTariff = (c) => isFleet(c) ? "energy-only" : "standard";
  const billingScope = (c) => isFleet(c) ? "supply only" : "supply + delivery";
  const comparison = (c) => isFleet(c) ? `Compared with ${c.chargeWindowEnabled ? "charging when the window allows" : "immediate charging"} on the same hourly supply prices. Commercial delivery and demand charges are excluded.` : `Compared with standard delivery and ${c.chargeWindowEnabled ? "charging when the window allows" : "immediate charging"}; includes any delivery-rate change.`;
  const currency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  const wholeMoney = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  const clockFormat = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", hour: "numeric", minute: "2-digit" });
  const clock = { format: (epochSeconds) => clockFormat.format(new Date(epochSeconds * 1000)) };
  const niceDate = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });
  const money = (v) => currency.format(Math.abs(v) < .00001 ? 0 : v);
  const percent = (a, b) => b > 0 ? Math.min(100, Math.max(0, a / b * 100)) : 100;
  const dateLabel = (date) => niceDate.format(new Date(date + "T12:00:00Z"));
  const usageLabel = (c) => isFleet(c) ? c.usageId === "heavy" ? "Heavy workload" : "Regular workload" : c.usageId === "heavy" ? "Heavy user" : "Average user";
  const savingsLabel = (saving, baseCost) => {
    const value = View.savingsPercent(saving, baseCost);
    return value === null ? "Percentage unavailable" : `${Math.abs(value).toFixed(1)}% ${value >= 0 ? "saved" : "extra cost"}`;
  };
  function showPercent(id, saving, baseCost, eligible = true) {
    text(id, eligible ? savingsLabel(saving, baseCost) : "");
    $(id)?.classList.toggle("is-negative", eligible && saving < 0);
  }
  const node = (tag, className, value) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined) el.textContent = value;
    return el;
  };
  let config, session, baseline, tracks = [], human, cursor = 0, playing = false, challenge = false;
  let humanOn = false, humanChoices = new Map(), frameTime = null, accumulated = 0, benchmarkToken = 0, lastMotionFrame = 0;
  let events = [], selectedDate = DATA.scenarios[0].date, selection = "day-ahead";
  let lastDrawnSlot = -1, benchmarkResult = null, annualToken = 0, lastFlow = {};
  const annualCache = new Map();
  const rules = () => [
    { id: "charge-now", label: $("charge-window").value === "custom" ? "Charge when allowed" : "Plug in & charge", color: "#87968d" },
    { id: "timer", label: `Timer · ${clock.format(market.at(selectedDate, Number($("timer-hour").value)))}`, color: "#ac8c5a" },
    { id: "cap", label: `Below ${Number($("cap-cents").value).toFixed(1)}¢`, color: "#de9754" },
    { id: "day-ahead", label: "SPARC plan", color: "#8cbb39" },
    { id: "live", label: "SPARC + live", color: "#1b9f94" },
  ];

  function timeHour(value) {
    if (!/^\d{2}:\d{2}$/.test(value)) return NaN;
    const [hour, minute] = value.split(":").map(Number);
    return hour + minute / 60;
  }
  function timeValue(hour) {
    const minutes = Math.round(hour * 60);
    return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  }

  function configFromControls() {
    const profile = ChargeTarget.profile({capacityKwh:market.vehicles[$("vehicle").value].capacityKwh,
      usageId:$("usage").value, targetSoc:Number($("charge-target").value), efficiency:.9});
    return { vehicleId: $("vehicle").value, date: selectedDate, usageId: $("usage").value,
      startSoc:profile.startSoc, targetSoc:profile.targetSoc,
      gridEnergyKwh:profile.gridEnergyKwh, maxBatteryKwh:profile.maxBatteryKwh,
      arrivalHour: Number($("arrival").value), departureHour: Number($("departure").value),
      powerKw: Number($("power").value), efficiency: .9, reserveMin: 30,
      tariff: $("tariff").value, strategy: $("strategy").value,
      timerHour: Number($("timer-hour").value), capCents: Number($("cap-cents").value),
      chargeWindowEnabled: $("charge-window").value === "custom",
      chargeStartHour: timeHour($("charge-start").value), chargeStopHour: timeHour($("charge-stop").value) };
  }
  function updateVehicleControls(useDefaults = false) {
    const fleet = FLEET[$("vehicle").value], previousPower = $("power").value, previousTariff = $("tariff").value;
    const powers = fleet ? $("vehicle").value === "bus" ? [50,125,450] : [11.5,50] : [3.6,7.7,11.5];
    $("power").replaceChildren(...powers.map((kw) => { const option = node("option", "", `${kw} kW`); option.value = kw; return option; }));
    $("power").value = !useDefaults && powers.includes(Number(previousPower)) ? previousPower : String(fleet?.power || 11.5);
    const rates = fleet ? [["energy-only","Hourly supply only"]] : [["time-of-day","Time-of-day"],["standard","Standard"]];
    $("tariff").replaceChildren(...rates.map(([value,label]) => { const option = node("option", "", label); option.value = value; return option; }));
    $("tariff").value = rates.some(([value]) => value === previousTariff) ? previousTariff : rates[0][0];
    text("usage-average-label", fleet ? "Regular" : "Average");
    text("usage-average-note", "50% on arrival");
    updateUsageSwitch();
    text("tariff-label", fleet ? "Pricing scenario" : "Delivery rate");
    text("billing-scope", fleet ? "Modeled hourly-priced supply only. Commercial delivery, demand charges and infrastructure are excluded." : "Supply + delivery charges. Final savings require the same delivered energy.");
    text("baseline-description", fleet ? "Same hourly supply · immediate charging" : "Standard delivery · immediate charging");
  }
  function updateUsageSwitch() {
    const heavy = $("usage").value === "heavy";
    $("usage-switch").classList.toggle("is-heavy", heavy);
    $("usage-average").setAttribute("aria-pressed", String(!heavy));
    $("usage-heavy").setAttribute("aria-pressed", String(heavy));
    text("usage-note", `Modeled arrival battery: ${heavy ? "20% after heavier use" : "50% after average use"}. Both strategies charge to your target.`);
  }
  function currentTrack() {
    return challenge ? human : (tracks.find((t) => t.id === selection)?.result || tracks[0]?.result);
  }
  function atCursor(result) {
    const i = Math.min(cursor, result?.slots?.length || 0);
    return i ? result.slots[i - 1] : { cumulativeCost: 0, cumulativeKwh: 0, energy: 0, charging: false };
  }
  function pushEvent(message, when = session?.start, kind = "") {
    events.unshift({ time: when, message, kind });
    events = events.slice(0, 7);
    const list = $("event-log");
    if (!list) return;
    list.replaceChildren(...events.map((event) => {
      const li = node("li", event.kind);
      const stamp = node("time", "", clock.format(event.time));
      stamp.dateTime = new Date(event.time * 1000).toISOString();
      li.append(stamp, node("span", "", event.message));
      return li;
    }));
  }
  function setPlaying(value) {
    playing = !!value && !!session?.valid && cursor < (baseline?.slots?.length || 0);
    text("play", playing ? "Pause replay" : cursor >= (baseline?.slots?.length || Infinity) ? "Replay night" : "Start replay");
    if ($("play")) $("play").setAttribute("aria-pressed", String(playing));
    frameTime = null;
    drawMotion();
  }
  function setMode(manual) {
    challenge = manual;
    $("race-mode").setAttribute("aria-pressed", String(!manual));
    $("challenge-mode").setAttribute("aria-pressed", String(manual));
    $("race-mode").classList.toggle("active", !manual);
    $("challenge-mode").classList.toggle("active", manual);
    $("challenge-panel").hidden = !manual;
    document.body.classList.toggle("challenge-mode", manual);
    $("timeline").disabled = manual;
    reset();
  }
  function reset() {
    setPlaying(false);
    annualToken++;
    if ($("yearly-savings-dialog").open) $("yearly-savings-dialog").close();
    benchmarkToken++;
    if ($("benchmark-button")) $("benchmark-button").disabled = false;
    config = configFromControls();
    text("target-value", `${config.targetSoc}%`);
    $("charge-target").style.setProperty("--target-fill", `${(config.targetSoc - 55) / 45 * 100}%`);
    text("charge-target-note", `${config.startSoc}% → ${config.targetSoc}% · ${(config.gridEnergyKwh * config.efficiency).toFixed(1)} battery kWh to add.`);
    session = Engine.buildSession(market, config);
    cursor = 0; accumulated = 0; humanOn = false; humanChoices = new Map();
    setPlaying(false);
    lastDrawnSlot = -1; events = []; lastFlow = {};
    $("run-summary").hidden = true;
    $("garage-scene").classList.toggle("vehicle-model-x", config.vehicleId === "model-x");
    $("garage-scene").classList.toggle("vehicle-bus", config.vehicleId === "bus");
    $("garage-scene").classList.toggle("vehicle-commercial", config.vehicleId === "commercial");
    document.body.classList.toggle("vehicle-model-x", config.vehicleId === "model-x");
    const vehicle = market.vehicles[config.vehicleId];
    $("garage-art").setAttribute("aria-label", `A ${vehicle.label || vehicle.name} plugged in beside a charger, under a night sky`);
    text("target-soc", `${config.targetSoc}% target`);
    selection = config.strategy;
    if ($("timer-settings")) $("timer-settings").hidden = selection !== "timer";
    $("schedule-settings").hidden = !config.chargeWindowEnabled;
    text("schedule-note", config.chargeWindowEnabled ? "Choose times in 5-minute steps. A stop earlier than the start crosses midnight. Every strategy stays within these hours while plugged in; SPARC can choose cheaper hours inside the window." : "Charging is allowed throughout the time between plug-in and ready-by.");
    text("baseline-description", config.chargeWindowEnabled ? isFleet(config) ? "Same hourly supply · starts when allowed" : "Standard delivery · starts when allowed" : isFleet(config) ? "Same hourly supply · immediate charging" : "Standard delivery · immediate charging");
    if ($("cap-settings")) $("cap-settings").hidden = selection !== "cap";
    text("cap-value", `${config.capCents.toFixed(1)}¢`);
    if (!session.valid) {
      text("configuration-note", session.reason || "This night is missing prices. Choose another historical night.");
      baseline = null; tracks = []; human = null;
      $("play").disabled = true;
      $("finish").disabled = true;
      text("car-status", "Check your settings");
      text("decision-reason", session.reason || "Every required hour needs a recorded price. Missing data is skipped, not invented.");
      ["cost-now", "saved-now", "baseline-cost", "plan-cost"].forEach((id) => text(id, "—"));
      text("sim-clock", "—");
      text("energy-now", "0.0 kWh");
      text("saved-percent", ""); text("race-clock", "—");
      $("race-card").style.setProperty("--replay-progress", "0%");
      ["baseline", "plan"].forEach((id) => {
        text(`${id}-soc`, `${config.startSoc}%`); text(`${id}-charge-state`, "Prices unavailable");
        text(`${id}-energy`, "—");
        $(`${id}-bar`).style.width = `${config.startSoc}%`;
        $(`${id}-target`).style.left = `${config.targetSoc}%`;
        $(`${id}-lane`).classList.remove("is-charging", "is-ready", "is-stopped");
      });
      $("garage-scene").classList.remove("is-charging", "is-ready");
      $("price-chart").replaceChildren();
      $("manual-charge").disabled = true;
      $("leaderboard").replaceChildren();
      showSavedBenchmark();
      return;
    }
    $("play").disabled = false; $("finish").disabled = false;
    baseline = Engine.simulate(market, { ...config, strategy: "charge-now", tariff: baselineTariff(config) });
    tracks = rules().map((rule) => ({ ...rule, result: Engine.simulate(market, { ...config, strategy: rule.id }) }));
    human = Engine.simulate(market, { ...config, strategy: "manual" }, { manual: humanChoices });
    const fit = session.requiredGridKwh <= session.availableSeconds / 3600 * config.powerKw + 1e-7;
    const scenario = DATA.scenarios.find((s) => s.date === selectedDate);
    text("configuration-note", fit ? `${dateLabel(selectedDate)} · ${isFleet(config) ? "One vehicle on assumed hourly-priced supply. Preset workloads are modeled, not a measured operator maximum." : scenario?.description || "Real ComEd prices, replayed in Chicago time."}` :
      "This charger cannot deliver the target before departure. Try more power, a lighter preset, or a longer window. Unfinished runs never count as savings.");
    if (config.chargeWindowEnabled) {
      text("configuration-note", fit ? `Plugged in ${clock.format(session.start)} → ${clock.format(session.end)}. Charging hours ${clock.format(market.at(selectedDate, config.chargeStartHour))} → ${clock.format(market.at(selectedDate, config.chargeStopHour))}, with ${(session.availableSeconds / 3600).toFixed(1)} hours available while plugged in.` : "Your charging window cannot deliver the target while plugged in. Extend the start/stop times, increase power, or lower the target. Unfinished runs never count as savings.");
    }
    pushEvent(`${vehicle.label || vehicle.name} plugged in at ${config.startSoc}%. Target ${config.targetSoc}%: ${session.batteryKwh.toFixed(1)} battery kWh to add.`);
    if (config.chargeWindowEnabled) pushEvent(`Charging is allowed from ${clock.format(session.chargingStart)} until ${clock.format(session.chargingEnd)}. No strategy charges outside that window.`);
    pushEvent(challenge ? "Your move: switch charging on and off. Future actual prices stay hidden." : "The strategies share a target and departure time. SPARC waits for its forecast's cheapest hours.");
    updateManualButton();
    draw(true);
    showSavedBenchmark();
    saveControls();
  }
  function saveControls() {
    try { localStorage.setItem("sparc.simulation.v3", JSON.stringify(config)); } catch { /* Optional local preferences. */ }
  }
  function updateManualButton() {
    text("manual-charge", humanOn ? "Pause my charging" : "Turn my charging on");
    $("manual-charge").setAttribute("aria-pressed", String(humanOn));
    const now = session?.start + cursor * 300;
    const allowed = !session?.allowedSlots || session.allowedSlots.has(now);
    text("manual-status", humanOn ? allowed ? "ON · charging at the selected power" : "ON requested · outside allowed charging hours" : "OFF · waiting for your command");
    $("manual-charge").classList.toggle("charging", humanOn);
  }
  function advance(steps) {
    if (!session?.valid) return;
    const end = Math.min(baseline.slots.length, cursor + steps);
    if (challenge) {
      for (let i = cursor; i < end; i++) humanChoices.set(baseline.slots[i].time, humanOn);
      human = Engine.simulate(market, { ...config, strategy: "manual" }, { manual: humanChoices });
    }
    cursor = end;
    draw();
    if (cursor >= baseline.slots.length) finishRound();
  }
  function finishRound() {
    setPlaying(false);
    const result = currentTrack();
    const canCompare = baseline.ready && result.ready;
    const saving = canCompare ? baseline.totalCost - result.totalCost : null;
    text("result-title", result.ready ? challenge ? "Ready for the road." : "Morning mission complete." : "Departure time. Battery short.");
    text("result-detail", result.ready ? `${usageLabel(config)} energy replenished. ${money(result.totalCost)} in replayed ${billingScope(config)} charges.` :
      `${Math.max(0, (session.requiredGridKwh - result.energyKwh) * .9).toFixed(1)} battery kWh still needed. A lower bill is not a saving when the target is missed.`);
    text("result-saving", canCompare ? `${money(Math.abs(saving))} ${saving >= 0 ? "saved" : "extra cost"} tonight` : "Savings unavailable · target missed");
    showPercent("result-percent", saving, baseline.totalCost, canCompare);
    text("result-ready", result.ready ? "100% of requested energy delivered" : `${Math.round(percent(result.energyKwh, session.requiredGridKwh))}% of requested energy delivered`);
    $("run-summary").hidden = false;
    if (challenge) {
      humanOn = false;
      updateManualButton();
      $("manual-charge").disabled = true;
      text("manual-status", "Night finished · charger stopped");
    }
    const plan = tracks.find((track) => track.id === "day-ahead").result;
    pushEvent(challenge ? (result.ready ? `Your bill: ${money(result.totalCost)}. SPARC's plan: ${money(plan.totalCost)}.` : "Challenge unfinished. Reset and try charging earlier.") :
      (result.ready ? `Target reached. Final settled bill: ${money(result.totalCost)}.` : "The charger or schedule could not finish in this window."), session.end, "complete");
    text("sr-status", result.ready ? `Night finished. ${canCompare ? money(saving) + " compared with immediate charging." : "Target ready."}` : "Night finished without reaching the energy target.");
    showAnnualSavings(result, canCompare, saving);
  }
  async function showAnnualSavings(result, canCompare, saving) {
    const token = ++annualToken, captured = { ...config }, strategy = selection;
    const dialog = $("yearly-savings-dialog");
    text("yearly-disclaimer", isFleet(captured) ? "One vehicle on assumed hourly-priced supply. Delivery, demand charges, infrastructure and battery wear are excluded. Actual results depend on the tariff and operation." : "Historical estimate, assuming daily charging. Supply + delivery only. Actual savings vary.");
    text("yearly-title", isFleet(captured) ? "Estimated yearly supply savings." : "Your estimated yearly savings.");
    text("yearly-amount", "Calculating…");
    text("yearly-unit", "");
    text("yearly-percent", "");
    text("yearly-night", canCompare ? `${money(Math.abs(saving))} ${saving >= 0 ? "saved" : "extra cost"} in this night’s replay.` : "This night did not deliver all the requested energy.");
    text("yearly-basis", `Checking your ${captured.startSoc}% → ${captured.targetSoc}% charge and strategy across the historical dataset…`);
    if (!dialog.open) dialog.showModal();
    function unavailable(message) {
      text("yearly-title", "Yearly savings unavailable.");
      text("yearly-amount", "—");
      text("yearly-unit", "");
      text("yearly-basis", message);
    }
    if (!canCompare) {
      unavailable("The energy target must be reached before a lower bill can count as a saving. Try more charging time, a stronger charger, or a lighter preset.");
      return;
    }
    if (challenge) {
      unavailable("Your manual choices apply to this night. Select an automatic strategy to estimate a year across all historical nights.");
      return;
    }
    const key = JSON.stringify({ ...captured, date: null, strategy });
    let annual = annualCache.get(key);
    if (!annual && !captured.chargeWindowEnabled && captured.arrivalHour === 20 && captured.departureHour === 7 && captured.powerKw === 11.5) {
      const row = savedBenchmark(strategy, captured);
      if (row) annual = { saving: row.savedDefaultAnnualUsd, baselineCost: row.defaultAnnualCostUsd, ready: row.readyFraction, nights: row.nights, skipped: row.skipped };
    }
    if (!annual) {
      let saved = 0, baseTotal = 0, ready = 0, nights = 0, skipped = 0;
      for (let i = 0; i < DATA.days.length; i++) {
        if (token !== annualToken) return;
        const c = { ...captured, date: DATA.days[i] };
        const base = Engine.simulate(market, { ...c, strategy: "charge-now", tariff: baselineTariff(c) });
        const chosen = Engine.simulate(market, { ...c, strategy });
        if (!base.valid || !chosen.valid) skipped++;
        else {
          nights++; saved += base.totalCost - chosen.totalCost; baseTotal += base.totalCost;
          if (base.ready && chosen.ready) ready++;
        }
        if (i % 10 === 0) {
          text("yearly-basis", `${i + 1} / ${DATA.days.length} historical dates checked…`);
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
      }
      annual = { saving: nights ? saved / nights * 365 : 0, baselineCost: nights ? baseTotal / nights * 365 : 0, ready: nights ? ready / nights : 0, nights, skipped };
    }
    if (token !== annualToken) return;
    annualCache.set(key, annual);
    if (!annual.nights || annual.ready < 1) {
      unavailable(annual.nights ? `The target was reached on ${(annual.ready * 100).toFixed(1)}% of ${annual.nights} usable historical nights. A full-year savings claim needs the requested energy delivered every night.` : "There are no complete historical nights for these settings.");
      return;
    }
    text("yearly-title", annual.saving >= 0 ? "Your estimated yearly savings." : "Your estimated yearly extra cost.");
    text("yearly-amount", money(Math.abs(annual.saving)));
    text("yearly-unit", "/ year");
    showPercent("yearly-percent", annual.saving, annual.baselineCost);
    text("yearly-title", annual.saving >= 0 ? isFleet(captured) ? "Estimated yearly supply savings." : "Your estimated yearly savings." : "Your estimated yearly extra cost.");
    text("yearly-basis", `Average difference across ${annual.nights} historical nights × 365, assuming a ${captured.startSoc}% → ${captured.targetSoc}% charge every day with your vehicle, charger and schedule. ${comparison(captured)} ${annual.skipped} dates skipped for missing prices.`);
  }
  function reason(result, item) {
    const now = session.start + cursor * 300;
    if (config.chargeWindowEnabled && atCursor(result).cumulativeKwh < session.requiredGridKwh - 1e-7 && !session.allowedSlots.has(now)) {
      const nextAllowed = session.chargingSlotTimes.find(time => time >= now);
      return nextAllowed !== undefined ? `Plugged in. Charging is allowed from ${clock.format(nextAllowed)}.` : `Your charging window has closed. Charging stopped by ${clock.format(session.chargingEnd)}.`;
    }
    if (!item) return challenge ? "You control the charger. Press C or use the charging button." : "Press Start replay to watch the plan work.";
    if (item.remaining <= 1e-7) return "Target reached. The vehicle is ready, so charging stops.";
    if (challenge) return humanOn ? "Your charger is on. You decide when to pause." : "Your charger is off. Keep an eye on the remaining time.";
    if (selection === "charge-now") return "This strategy starts immediately and keeps charging until the target is reached.";
    if (selection === "timer") return item.charging ? "The fixed timer has started, or the finish deadline requires charging now." : "Waiting for the fixed start time. The timer starts early if necessary to finish.";
    if (selection === "cap") return item.charging ? "The last published supply price is below your cap, or the finish deadline requires charging." : "The posted supply price is above the cap. Waiting while enough time remains to finish.";
    if (item.capacityRisk && !item.charging) return "Avoiding summer peak-capacity hours while the target can still fit later.";
    return item.charging ? selection === "live" ? "Live prices updated the plan. This slot is worth charging in." : "This is one of the forecast's cheapest available hours." :
      "Waiting for cheaper forecast hours. SPARC keeps enough time to finish before departure.";
  }
  // The in-progress bill uses posted prices for the unfinished hour. Final bills
  // settle on the recorded hourly prices, so the manual player cannot infer a future settlement.
  function displayedCost(result, now) {
    if (cursor >= baseline.slots.length) return result.totalCost;
    let cost = 0;
    const latest = Math.floor((now - 420) / 300) * 300;
    const visible = market.fiveMinute.get(latest);
    for (let i = 0; i < Math.min(cursor, result.slots.length); i++) {
      const slot = result.slots[i];
      if (slot.hour + 3600 <= now) cost += slot.cost;
      else cost += slot.energy * ((visible ?? market.knownDayAhead(slot.hour, now) ?? slot.forecast ?? 0) + slot.deliveryCents) / 100;
    }
    return cost;
  }
  function laneMotion(result, manual = false) {
    const completed = atCursor(result).cumulativeKwh;
    const now = session.start + cursor * 300;
    const allowed = !session.allowedSlots || session.allowedSlots.has(now);
    const nextEnergy = manual ? humanOn && allowed ? Math.min(config.powerKw / 12, session.requiredGridKwh - completed) : 0 : result.slots[cursor]?.energy || 0;
    return View.laneState({completedKwh:completed, nextEnergyKwh:nextEnergy,
      requiredKwh:session.requiredGridKwh, capacityKwh:market.vehicles[config.vehicleId].capacityKwh,
      efficiency:config.efficiency, startSoc:config.startSoc, cursor, totalSlots:baseline.slots.length, fraction:manual ? 0 : accumulated, playing});
  }
  function drawMotion() {
    if (!session?.valid || !baseline || !currentTrack()) return;
    const progress = View.motionProgress(cursor, baseline.slots.length, accumulated);
    const now = session.start + progress * (session.end - session.start);
    const chosen = laneMotion(currentTrack(), challenge), regular = laneMotion(baseline);
    text("race-clock", clock.format(now)); text("sim-clock", clock.format(now));
    text("race-target-label", `Both target ${chosen.targetSoc.toFixed(0)}% battery`);
    $("race-card").style.setProperty("--replay-progress", `${progress * 100}%`);
    $("race-card").classList.toggle("is-replay-paused", !playing);
    $("timeline").value = Math.round(progress * 1000);
    text("timeline-label", `${clock.format(now)} / ${clock.format(session.end)} · ${dateLabel(selectedDate)}`);
    [["baseline",regular,"Regular charging"],["plan",chosen,challenge ? "Your charging" : "SPARC"]].forEach(([id,lane,label]) => {
      const description = lane.state === "ready" ? "Target reached · stopped" : lane.state === "stopped" ? "Stopped · target missed" : lane.state === "charging" ? `Charging · ${config.powerKw} kW` : lane.state === "paused" ? "Replay paused" : lane.state === "plugged" ? "Plugged in · ready" : lane.gridKwh > .000001 ? "Paused · charger off" : "Waiting · charger off";
      text(`${id}-soc`, `${lane.soc.toFixed(0)}%`);
      text(`${id}-charge-state`, description);
      text(`${id}-progress-label`, `Target ${lane.targetSoc.toFixed(0)}%`);
      text(`${id}-energy`, `${(lane.gridKwh * config.efficiency).toFixed(1)} / ${session.batteryKwh.toFixed(1)} kWh added`);
      $(`${id}-bar`).style.width = `${lane.soc}%`;
      $(`${id}-target`).style.left = `${lane.targetSoc}%`;
      const row = $(`${id}-lane`);
      row.classList.toggle("is-charging", lane.charging);
      row.classList.toggle("is-ready", lane.state === "ready");
      row.classList.toggle("is-stopped", lane.state === "stopped");
      if ((playing || cursor >= baseline.slots.length) && lastFlow[id] !== lane.state) {
        if (lane.state === "charging") pushEvent(`${label}: charging started.`, now);
        else if (lane.state === "ready") pushEvent(`${label}: battery target reached, charger stopped.`, now);
        else if (lane.state === "waiting" && lastFlow[id] === "charging") pushEvent(`${label} paused charging.`, now);
        lastFlow[id] = lane.state;
      }
    });
    text("battery-soc", `${chosen.soc.toFixed(0)}%`);
    $("battery-fill").style.width = `${chosen.soc}%`;
    text("energy-now", `${(chosen.gridKwh * config.efficiency).toFixed(1)} kWh`);
    text("car-status", chosen.state === "ready" ? "Ready to go · charger stopped" : chosen.state === "stopped" ? "Target missed · stopped" : chosen.state === "charging" ? "Charging" : chosen.state === "paused" ? "Replay paused" : chosen.state === "plugged" ? "Plugged in" : "Waiting · charger off");
    $("garage-scene").classList.toggle("is-charging", chosen.charging);
    $("garage-scene").classList.toggle("is-ready", chosen.targetReached);
    $("garage-scene").style.setProperty("--night-progress", progress);
    const wallHour = market.hours.get(Math.floor(now / 3600) * 3600)?.localHour ?? 7;
    $("garage-scene").classList.toggle("is-day", wallHour >= 6 && wallHour < 19);
  }
  function draw(force = false) {
    if (!session?.valid || !baseline) return;
    const result = currentTrack(), item = cursor ? result.slots[cursor - 1] : null;
    const state = atCursor(result), base = atCursor(baseline);
    const now = Math.min(session.end, session.start + cursor * 300), complete = cursor >= baseline.slots.length;
    const vehicle = market.vehicles[config.vehicleId];
    const chosenCost = displayedCost(result, now), baseCost = displayedCost(baseline, now);
    const delta = baseCost - chosenCost;
    text("sim-clock", clock.format(now));
    text("energy-now", `${(state.cumulativeKwh * .9).toFixed(1)} kWh`);
    text("cost-now", money(chosenCost));
    text("saved-now", complete && (!result.ready || !baseline.ready) ? "—" : money(complete ? Math.abs(delta) : delta));
    if (complete) showPercent("saved-percent", delta, baseCost, result.ready && baseline.ready);
    else { text("saved-percent", "Savings % at the finish"); $("saved-percent").classList.remove("is-negative"); }
    text("savings-label", complete ? delta < 0 ? "Extra cost tonight" : "Saved tonight" : "Cost gap so far");
    text("cost-label", complete ? "Settled bill" : "Bill estimate");
    text("baseline-cost", money(baseCost));
    text("baseline-energy", `${(base.cumulativeKwh * .9).toFixed(1)} / ${(session.requiredGridKwh * .9).toFixed(1)} kWh`);
    text("plan-cost", money(chosenCost));
    text("plan-energy", `${(state.cumulativeKwh * .9).toFixed(1)} / ${(session.requiredGridKwh * .9).toFixed(1)} kWh`);
    text("plan-label", challenge ? "Your charging" : rules().find((rule) => rule.id === selection)?.label || "SPARC");
    const soc = Math.min(config.targetSoc, config.startSoc + state.cumulativeKwh * config.efficiency / vehicle.capacityKwh * 100);
    text("battery-soc", `${Math.round(soc)}%`);
    if ($("battery-fill")) $("battery-fill").style.width = `${soc}%`;
    text("timeline-label", `${clock.format(now)} / ${clock.format(session.end)} · ${dateLabel(selectedDate)}`);
    $("timeline").value = Math.round(cursor / baseline.slots.length * 1000);
    const nextSlot = result.slots[Math.min(cursor, result.slots.length - 1)];
    const charging = !!item?.charging && !complete;
    $("finish").disabled = complete;
    const status = complete ? result.ready ? "Ready to go" : "Target missed" : cursor === 0 ? "Plugged in" : charging ? "Charging" : state.cumulativeKwh >= session.requiredGridKwh - 1e-7 ? "Ready to go" : "Waiting";
    text("car-status", status);
    text("decision-reason", reason(result, item));
    const visible = market.fiveMinute.get(Math.floor((now - 420) / 300) * 300);
    text("current-price", visible === undefined ? "—" : `${visible.toFixed(1)}¢`);
    text("current-price-label", "Last posted supply price / kWh");
    $("garage-scene").classList.toggle("is-charging", charging);
    $("garage-scene").classList.toggle("is-ready", complete && result.ready);
    $("garage-scene").style.setProperty("--night-progress", cursor / baseline.slots.length);
    const wallHour = market.hours.get(Math.floor(now / 3600) * 3600)?.localHour ?? 7;
    $("garage-scene").classList.toggle("is-day", wallHour >= 6 && wallHour < 19);
    if (cursor !== lastDrawnSlot || force) {
      lastDrawnSlot = cursor;
      drawChart(result, now);
      drawLeaderboard(now, complete);
    }
    if (nextSlot && !complete) $("manual-charge").disabled = state.cumulativeKwh >= session.requiredGridKwh - 1e-7;
    else $("manual-charge").disabled = true;
    if (challenge) updateManualButton();
    drawMotion();
  }
  function drawLeaderboard(now, complete) {
    const rows = tracks.map((track) => ({ ...track, state: atCursor(track.result), cost: displayedCost(track.result, now) }));
    if (challenge) rows.push({ id: "manual", label: "You", color: "#566de7", result: human, state: atCursor(human), cost: displayedCost(human, now) });
    if (complete) rows.sort((a, b) => Number(b.result.ready) - Number(a.result.ready) || a.cost - b.cost);
    const parent = $("leaderboard"), table = parent.tagName === "TBODY";
    parent.replaceChildren(...rows.map((track, index) => {
      const row = node(table ? "tr" : "div", `strategy-row ${track.id === (challenge ? "manual" : selection) ? "selected" : ""}`);
      const label = node(table ? "td" : "div", "strategy-name");
      const dot = node("span", "strategy-dot"); dot.style.background = track.color;
      label.append(dot, node("span", "", `${complete ? index + 1 + ". " : ""}${track.label}`));
      const cost = node(table ? "td" : "div", "strategy-cost", money(track.cost));
      const energy = node(table ? "td" : "div", "strategy-energy", `${Math.round(percent(track.state.cumulativeKwh, session.requiredGridKwh))}%`);
      const status = node(table ? "td" : "div", `strategy-status ${complete && !track.result.ready ? "short" : ""}`, complete ? track.result.ready ? "Ready" : "Short" : track.state.charging ? "Charging" : "Waiting");
      row.append(label, cost, energy, status);
      return row;
    }));
  }
  function svg(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs || {}).forEach(([key, value]) => el.setAttribute(key, value));
    return el;
  }
  function drawChart(result, now) {
    const chart = $("price-chart"); if (!chart) return;
    const width = 820, height = 220, left = 40, right = 14, top = 18, bottom = 33;
    const hours = session.hours.map((time) => market.hours.get(time));
    const points = hours.map((hour) => ({ hour, forecast: market.knownDayAhead(hour.time, now), actual: hour.time + 3600 <= now ? hour.billed : null }));
    const values = points.flatMap((point) => [point.forecast, point.actual]).filter(Number.isFinite);
    const min = Math.min(-1, ...values), max = Math.max(8, ...values), span = max - min || 1;
    const x = (time) => left + (time - session.start) / (session.end - session.start) * (width - left - right);
    const y = (value) => top + (max - value) / span * (height - top - bottom);
    chart.replaceChildren();
    const title = svg("title"); title.textContent = "Published day-ahead forecast and completed hours' settled supply prices. Future actual prices are hidden."; chart.append(title);
    [min, min + span / 2, max].forEach((value) => {
      chart.append(svg("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: "#dbe1d5", "stroke-dasharray": "3 6" }));
      const label = svg("text", { x: left - 7, y: y(value) + 4, "text-anchor": "end", fill: "#7a867b", "font-size": 11 }); label.textContent = `${value.toFixed(0)}¢`; chart.append(label);
    });
    for (let i = 0; i < hours.length; i++) {
      if (i % 2 === 0 || i === hours.length - 1) {
        const label = svg("text", { x: x(hours[i].time), y: height - 8, "text-anchor": "middle", fill: "#7a867b", "font-size": 11 });
        label.textContent = clock.format(hours[i].time).replace(":00", ""); chart.append(label);
      }
      const hour = hours[i], a = Math.max(hour.time, session.start), b = Math.min(hour.time + 3600, session.end);
      const energy = result.slots.filter((slot) => slot.hour === hour.time && slot.time < now).reduce((sum, slot) => sum + slot.energy, 0);
      if (energy > 0) chart.append(svg("rect", { x: x(a), y: height - bottom - 13, width: Math.max(1, x(b) - x(a) - 2), height: 13, rx: 2, fill: "#a3cb66", opacity: .75 }));
    }
    function line(key, color, dash) {
      let path = "", previous = false;
      points.forEach((point) => {
        const value = point[key]; if (!Number.isFinite(value)) { previous = false; return; }
        const first = Math.max(session.start, point.hour.time), end = Math.min(session.end, point.hour.time + 3600);
        path += `${previous ? "L" : "M"}${x(first)},${y(value)}L${x(end)},${y(value)}`; previous = true;
      });
      chart.append(svg("path", { d: path, stroke: color, fill: "none", "stroke-width": 2.5, "stroke-dasharray": dash || "", "stroke-linejoin": "round" }));
    }
    line("forecast", "#92a17f", "5 5"); line("actual", "#168776");
    chart.append(svg("line", { x1: x(now), x2: x(now), y1: top, y2: height - bottom, stroke: "#25392b", "stroke-width": 1.5 }));
  }

  function savedId(id, c = config) {
    return id === "timer" ? `timer-${String(c.timerHour).padStart(2, "0")}00` : id === "cap" ? `cap-${c.capCents}` : id;
  }
  function savedBenchmark(id, c = config) {
    if (c.chargeWindowEnabled) return null;
    const vehicle = market.vehicles[c.vehicleId];
    return DATA.benchmarks.find((row) => row.vehicleId === c.vehicleId && row.tariff === c.tariff && row.strategy === savedId(id, c) &&
      Math.abs(row.milesPerDay * vehicle.batteryKwhPerMile - c.gridEnergyKwh * c.efficiency) < 1e-7);
  }
  function showSavedBenchmark() {
    benchmarkResult = null;
    const preset = !config.chargeWindowEnabled && config.arrivalHour === 20 && config.departureHour === 7 && config.powerKw === 11.5;
    const rows = preset ? rules().map((rule) => {
      const b = savedBenchmark(rule.id);
      return b ? { label: rule.label, cost: b.annualCostUsd, saving: b.savedDefaultAnnualUsd, baselineCost:b.defaultAnnualCostUsd, ready: b.readyFraction, nights: b.nights, skipped: b.skipped } : null;
    }) : [];
    if (rows.length && rows.every(Boolean)) {
      drawBenchmark(rows);
      text("benchmark-status", "Previously verified against the Python simulator · 554 usable nights");
    } else {
      $("benchmark-results").replaceChildren(node("p", "benchmark-empty", "Run the historical study to test these exact settings across all available nights."));
      text("benchmark-status", "Your settings · ready for a fresh historical test");
      text("benchmark-note", `One vehicle charging from ${config.startSoc}% to ${config.targetSoc}%, with your charger and window. ${comparison(config)} Run the full test to compare all strategies across the historical dataset.`);
    }
    if ($("benchmark-progress")) $("benchmark-progress").value = 0;
  }
  function drawBenchmark(rows) {
    const table = node("table", "benchmark-table"), header = node("thead"), tr = node("tr");
    ["Strategy", "Cost / year", "Saved / year", "Saved %", "Ready"].forEach((value) => tr.append(node("th", "", value)));
    header.append(tr); table.append(header);
    const body = node("tbody");
    [...rows].sort((a, b) => b.ready - a.ready || a.cost - b.cost).forEach((row) => {
      const r = node("tr");
      [row.label, money(row.cost), row.ready === 1 ? wholeMoney.format(row.saving) : "Target missed", row.ready === 1 ? savingsLabel(row.saving, row.baselineCost) : "—", `${(row.ready * 100).toFixed(row.ready === 1 ? 0 : 1)}%`].forEach((value) => r.append(node("td", "", value)));
      body.append(r);
    });
    table.append(body); $("benchmark-results").replaceChildren(table);
    const n = rows[0]?.nights || 0, skipped = rows[0]?.skipped || 0;
    text("benchmark-note", `${n} usable nights; ${skipped} dates skipped for missing prices. Annualized nightly averages × 365, assuming daily charging. ${comparison(config)} An unfinished target never counts as a saving. ${billingScope(config)} only; other bill charges, battery wear and installation are excluded.`);
    benchmarkResult = rows;
  }
  async function runBenchmark() {
    const token = ++benchmarkToken, captured = { ...config }, methods = rules();
    $("benchmark-button").disabled = true;
    if ($("benchmark-progress")) $("benchmark-progress").hidden = false;
    const totals = methods.map((rule) => ({ ...rule, cost: 0, saved: 0, baselineCost:0, ready: 0, nights: 0, skipped: 0 }));
    text("benchmark-status", "Testing your exact settings on historical prices…");
    for (let i = 0; i < DATA.days.length; i++) {
      if (token !== benchmarkToken) return;
      const day = DATA.days[i], c = { ...captured, date: day };
      const base = Engine.simulate(market, { ...c, strategy: "charge-now", tariff: baselineTariff(c) });
      if (!base.valid) totals.forEach((total) => total.skipped++);
      else totals.forEach((total) => {
        const result = Engine.simulate(market, { ...c, strategy: total.id });
        if (!result.valid) { total.skipped++; return; }
        total.nights++; total.cost += result.totalCost; total.saved += base.totalCost - result.totalCost; total.baselineCost += base.totalCost;
        if (result.ready && base.ready) total.ready++;
      });
      if (i % 5 === 0 || i === DATA.days.length - 1) {
        if ($("benchmark-progress")) { $("benchmark-progress").max = DATA.days.length; $("benchmark-progress").value = i + 1; }
        text("benchmark-status", `${i + 1} / ${DATA.days.length} historical dates tested`);
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    }
    if (token !== benchmarkToken) return;
    const rows = totals.filter((total) => total.nights).map((total) => ({ label: total.label, cost: total.cost / total.nights * 365,
      saving: total.saved / total.nights * 365, baselineCost:total.baselineCost / total.nights * 365, ready: total.ready / total.nights, nights: total.nights, skipped: total.skipped }));
    if (rows.length) drawBenchmark(rows);
    else $("benchmark-results").replaceChildren(node("p", "", "No nights have complete prices for this window. Try another arrival or departure."));
    text("benchmark-status", "Complete · real prices, your settings, every available night");
    $("benchmark-button").disabled = false;
  }

  function setupControls() {
    const scenario = $("scenario"); scenario.replaceChildren();
    const highlights = document.createElement("optgroup"); highlights.label = "Featured real nights";
    DATA.scenarios.forEach((s) => { const option = node("option", "", s.label); option.value = s.date; highlights.append(option); });
    scenario.append(highlights);
    const all = document.createElement("optgroup"); all.label = "Every historical night";
    DATA.days.forEach((date) => { const option = node("option", "", dateLabel(date)); option.value = date; all.append(option); });
    scenario.append(all); scenario.value = selectedDate;
    try {
      const targetPreferences = localStorage.getItem("sparc.simulation.v3");
      const currentPreferences = targetPreferences || localStorage.getItem("sparc.simulation.v2");
      const stored = JSON.parse(currentPreferences || localStorage.getItem("sparc.simulation") || "null");
      if (stored && market.vehicles[stored.vehicleId]) {
        // Move the former schedule strategy into the independent charging window.
        if (stored.strategy === "schedule") { stored.strategy = "day-ahead"; stored.chargeWindowEnabled = true; }
        $("charge-window").value = stored.chargeWindowEnabled ? "custom" : "connected";
        $("usage").value = targetPreferences && stored.usageId === "average" ? "average" : "heavy";
        if (targetPreferences && Number.isFinite(stored.targetSoc) && stored.targetSoc >= 55 && stored.targetSoc <= 100 && stored.targetSoc % 5 === 0) $("charge-target").value = stored.targetSoc;
        [["vehicle", "vehicleId"], ["arrival", "arrivalHour"], ["departure", "departureHour"], ["power", "powerKw"], ["tariff", "tariff"], ["strategy", "strategy"], ["timer-hour", "timerHour"], ["cap-cents", "capCents"]].forEach(([id, key]) => {
          if (stored[key] !== undefined && (id !== "vehicle" || currentPreferences)) { const el = $(id), old = el.value; el.value = stored[key]; if (!el.value) el.value = old; if (id === "vehicle") updateVehicleControls(true); }
        });
        [["charge-start", "chargeStartHour"], ["charge-stop", "chargeStopHour"]].forEach(([id, key]) => {
          if (Number.isFinite(stored[key]) && stored[key] >= 0 && stored[key] < 24) $(id).value = timeValue(stored[key]);
        });
        if (DATA.days.includes(stored.date)) { selectedDate = stored.date; scenario.value = selectedDate; }
      }
    } catch { /* An empty/private storage space starts with the defaults. */ }
    updateVehicleControls();
    $("vehicle").addEventListener("change", () => { updateVehicleControls(true); reset(); });
    ["average", "heavy"].forEach((value) => $("usage-" + value).addEventListener("click", () => {
      if ($("usage").value === value) return;
      $("usage").value = value; updateUsageSwitch(); reset();
    }));
    ["arrival", "departure", "power", "tariff", "strategy", "timer-hour", "cap-cents", "charge-target", "charge-window", "charge-start", "charge-stop"].forEach((id) => $(id).addEventListener("change", reset));
    $("charge-target").addEventListener("input", () => {
      const target = Number($("charge-target").value);
      text("target-value", `${target}%`);
      $("charge-target").style.setProperty("--target-fill", `${(target - 55) / 45 * 100}%`);
    });
    $("cap-cents").addEventListener("input", () => text("cap-value", `${Number($("cap-cents").value).toFixed(1)}¢`));
    scenario.addEventListener("change", () => { selectedDate = scenario.value; reset(); });
    $("race-mode").addEventListener("click", () => setMode(false));
    $("challenge-mode").addEventListener("click", () => setMode(true));
    $("play").addEventListener("click", () => {
      if (cursor >= (baseline?.slots?.length || Infinity)) reset();
      setPlaying(!playing);
    });
    $("reset").addEventListener("click", reset);
    $("finish").addEventListener("click", () => advance(baseline?.slots?.length || 0));
    $("next-night").addEventListener("click", () => {
      const start = DATA.days.indexOf(selectedDate);
      for (let offset = 1; offset <= DATA.days.length; offset++) {
        const date = DATA.days[(start + offset + DATA.days.length) % DATA.days.length];
        if (Engine.buildSession(market, { ...config, date }).valid) { selectedDate = date; scenario.value = date; reset(); return; }
      }
    });
    $("timeline").addEventListener("input", () => {
      if (challenge || !baseline) return;
      const requested = Number($("timeline").value);
      setPlaying(false);
      cursor = Math.round(requested / 1000 * baseline.slots.length);
      accumulated = 0;
      $("run-summary").hidden = true; draw(true);
      if (cursor === baseline.slots.length) finishRound();
    });
    $("manual-charge").addEventListener("click", () => {
      if (!challenge || cursor >= (baseline?.slots?.length || 0)) return;
      humanOn = !humanOn; updateManualButton();
      drawMotion();
      pushEvent(humanOn ? "You switched charging on." : "You paused charging.", session.start + cursor * 300);
    });
    $("benchmark-button").addEventListener("click", runBenchmark);
    ["yearly-close", "yearly-done"].forEach((id) => $(id).addEventListener("click", () => $("yearly-savings-dialog").close()));
    $("yearly-next").addEventListener("click", () => { $("yearly-savings-dialog").close(); $("next-night").click(); });
    $("yearly-savings-dialog").addEventListener("close", () => { annualToken++; });
    document.addEventListener("keydown", (event) => {
      if ($("yearly-savings-dialog").open) return;
      if (/INPUT|SELECT|TEXTAREA|BUTTON/.test(event.target.tagName) || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.code === "Space") { event.preventDefault(); $("play").click(); }
      if (event.code === "KeyC" && challenge) $("manual-charge").click();
      if (event.code === "KeyR") reset();
    });
    document.addEventListener("visibilitychange", () => { frameTime = null; });
  }
  function frame(timestamp) {
    if (playing && !document.hidden) {
      if (frameTime !== null) {
        accumulated += Math.min(.25, (timestamp - frameTime) / 1000) * Number($("speed").value) / 5;
        const steps = Math.floor(accumulated);
        if (steps > 0) { accumulated -= steps; advance(steps); }
      }
      frameTime = timestamp;
      if (playing && timestamp - lastMotionFrame >= 50) { drawMotion(); lastMotionFrame = timestamp; }
    } else frameTime = null;
    requestAnimationFrame(frame);
  }
  setupControls(); reset(); requestAnimationFrame(frame);
  // A small public QA surface makes the state observable without changing gameplay.
  window.SparcNightShift = { getState: () => ({ config: { ...config }, cursor, playing, challenge,
    valid: !!session?.valid, complete: !!baseline && cursor === baseline.slots.length,
    selectedResult: currentTrack(), benchmarkResult }), reset, advance, runBenchmark };
})();
