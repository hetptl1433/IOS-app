"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const put = (id, value) => { $(id).textContent = value; };
  const DATA = window.SPARC_SIM_DATA, HISTORY = window.SPARC_CHARGING_HISTORY;
  const Lab = window.SparcChargingHistory;
  if (!DATA || !HISTORY || !Lab || !window.SparcSimulation) {
    put("history-error", "The replay files could not load. Serve the complete night-shift/dist folder, then reload this page.");
    $("history-error").hidden = false;
    $("run-history").disabled = true;
    return;
  }
  const market = Lab.createMarket(DATA, HISTORY);
  const rows = HISTORY.rows;
  const byId = new Map(rows.map(row => [String(row.id), row]));
  const nextRows = new Map(rows.map((row, i) => [row.id, rows[i + 1]]));
  const moneyFormat = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
  const numberFormat = new Intl.NumberFormat("en-US", {maximumFractionDigits: 1});
  const clockFormat = new Intl.DateTimeFormat("en-US", {timeZone: HISTORY.timezone, hour: "numeric", minute: "2-digit"});
  const dateFormat = new Intl.DateTimeFormat("en-US", {timeZone: "UTC", month: "short", day: "numeric", year: "numeric"});
  const money = n => moneyFormat.format(Math.abs(n) < .005 ? 0 : n);
  const num = n => numberFormat.format(n);
  const clock = t => clockFormat.format(new Date(t * 1000));
  const date = day => dateFormat.format(new Date(day + "T12:00:00Z"));
  const dateTime = row => `${date(row.startLocal.slice(0, 10))} · ${clock(row.start)}`;
  const rateName = tariff => tariff === "standard" ? "standard delivery" : "time-of-day delivery";
  const element = (tag, className, value) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (value !== undefined) el.textContent = value;
    return el;
  };
  const svgElement = (tag, attributes, value) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([key, item]) => el.setAttribute(key, item));
    if (value !== undefined) el.textContent = value;
    return el;
  };
  let filtered = [], selected, run, page = 0, batch = null, batchToken = 0;
  let cursor = 0, playing = false, lastFrame = null, accumulated = 0, replayFrame;
  const pageSize = 20;

  function options() {
    return {window: $("history-window").value, departureHour: Number($("history-departure").value),
      tariff: $("history-tariff").value, powerKw: $("history-power").value};
  }
  function stop() {
    playing = false;
    cancelAnimationFrame(replayFrame);
    lastFrame = null;
    accumulated = 0;
    put("replay-play", "▶ Play replay");
  }
  function updateFilters() {
    stop();
    batchToken++;
    batch = null;
    page = 0;
    $("run-history").disabled = false;
    $("export-history").disabled = true;
    $("batch-progress").hidden = true;
    const start = $("history-start").value, end = $("history-end").value;
    const invalid = !start || !end || start > end;
    $("history-error").hidden = !invalid;
    if (invalid) put("history-error", "Choose a valid date range with the start on or before the end.");
    filtered = invalid ? [] : rows.filter(row => row.startLocal.slice(0, 10) >= start && row.startLocal.slice(0, 10) <= end && row.gridKwh >= Number($("history-min-energy").value));
    put("summary-sessions", num(filtered.length));
    put("summary-coverage", "Recorded home AC sessions in range");
    put("summary-energy", `${num(filtered.reduce((sum, row) => sum + row.gridKwh, 0))} kWh`);
    put("summary-energy-note", "All filtered grid energy, including losses");
    put("summary-cost", "—");
    put("summary-saving", "—");
    put("summary-baseline", "Run the history to compare costs");
    const rateChange = options().tariff === "time-of-day";
    put("summary-saving-note", rateChange ? "Includes delivery-rate and timing changes" : "Timing savings on standard delivery");
    put("comparison-note", `Baseline: immediate charging on standard delivery. SPARC and the alternative plans use ${rateName(options().tariff)}. ${rateChange ? "Savings include the rate and timing changes." : "This isolates timing savings."}`);
    put("sparc-rate-heading", rateChange ? "SPARC · time-of-day" : "SPARC · standard");
    put("batch-status", filtered.length ? "Ready to compare these sessions. Run filtered sessions to calculate the totals." : "No sessions match these filters. Choose another date range or session size.");
    const morning = $("history-window").value === "morning";
    $("departure-field").hidden = !morning;
    put("window-note", morning ? "Assumes the car stays connected until departure. Extensions stop at the next recorded session; the original window is kept." : "Uses only the recorded charging window, rounded outward to 5 minutes.");
    $("run-history").disabled = !filtered.length;
    const picker = $("history-session");
    picker.replaceChildren(...filtered.slice().reverse().map(row => {
      const option = element("option", "", `#${row.id} · ${dateTime(row)} · ${num(row.gridKwh)} kWh`);
      option.value = row.id;
      return option;
    }));
    if (!filtered.some(row => String(row.id) === String(selected))) {
      selected = filtered.slice().reverse().find(row => !row.issue && row.gridKwh >= 10)?.id || filtered.at(-1)?.id;
    }
    picker.disabled = !filtered.length;
    if (selected !== undefined) picker.value = selected;
    updateReplay();
    renderTable();
  }

  function updateReplay() {
    stop();
    cursor = 0;
    $("session-warning").hidden = true;
    const row = byId.get(String(selected));
    if (!row || !filtered.includes(row)) {
      run = null;
      $("session-facts").replaceChildren();
      $("history-tracks").replaceChildren();
      $("history-chart").replaceChildren();
      put("replay-clock", "—");
      put("replay-outcome", "No sessions match these filters.");
      put("session-note", "Choose another date range or session size to replay your history.");
      put("session-id", "NO SESSION");
      $("replay-play").disabled = true;
      $("replay-scrub").disabled = true;
      $("replay-reset").disabled = true;
      return;
    }
    run = Lab.simulate(market, row, options(), nextRows.get(row.id));
    put("session-id", `SESSION #${row.id}`);
    const power = run.config?.power;
    const facts = [
      ["GRID ENERGY", `${num(row.gridKwh)} kWh`, `${num(row.batteryKwh)} kWh added to battery`],
      ["RECORDED BATTERY", `${num(row.startSoc)} → ${num(row.endSoc)}%`, "State of charge from the CSV"],
      ["MODELED POWER", power ? `${num(power)} kW` : "—", `CSV peak: ${num(row.powerKw)} kW`],
      ["LOGGED CSV COST", money(row.loggedCost), "Separate from simulated costs"],
    ];
    $("session-facts").replaceChildren(...facts.map(([label, value, note]) => {
      const block = element("div");
      block.append(element("span", "", label), element("strong", "", value), element("small", "", note));
      return block;
    }));
    let note = `Recorded ${dateTime(row)} → ${date(row.endLocal.slice(0, 10))} ${clock(row.end)}. `;
    if (run.valid) note += `Simulation window: ${clock(run.session.start)} → ${clock(run.session.end)}${run.session.end - run.session.start >= 86400 ? " (+1 day)" : ""} · ${num((run.session.end - run.session.start) / 3600)} hours · Chicago time.`;
    put("session-note", note);
    const warnings = [];
    if (!run.valid) warnings.push(run.reason);
    else {
      if (run.prepared.powerAdjusted && options().powerKw === "recorded") warnings.push("CSV peak power is below the energy-implied average. Modeled power was raised to preserve the recorded energy.");
      if (run.prepared.extended) warnings.push("Morning availability is an assumption; actual unplug time is not recorded.");
      if (run.prepared.capped) warnings.push("The morning extension is capped at the next recorded charging session.");
      if (!run.eligible) warnings.push("The selected power cannot deliver the recorded energy in this window. This session is excluded from savings totals.");
    }
    if (warnings.length) { put("session-warning", warnings.join(" ")); $("session-warning").hidden = false; }
    $("replay-play").disabled = !run.valid;
    $("replay-scrub").disabled = !run.valid;
    $("replay-reset").disabled = !run.valid;
    $("replay-scrub").value = 0;
    $("replay-scrub").max = run.valid ? run.tracks[0].result.slots.length : 1;
    $("history-tracks").replaceChildren(...run.tracks.map(track => {
      const lane = element("div", `history-track${track.id === "day-ahead" ? " is-sparc" : ""}`);
      lane.dataset.strategy = track.id;
      lane.style.setProperty("--track-color", track.color);
      const name = element("div", "track-name", track.label);
      name.append(element("span", "track-state", "Ready to replay"));
      const progress = element("div");
      const meter = element("div", "track-meter");
      meter.append(element("span"));
      progress.append(meter, element("div", "track-energy", "0 kWh"));
      lane.append(name, progress, element("strong", "track-cost", "$0.00"));
      return lane;
    }));
    drawChart();
    drawReplay();
  }

  function drawReplay() {
    if (!run?.valid) {
      put("replay-clock", "—");
      put("replay-outcome", run?.reason || "Choose a session to compare charging strategies.");
      return;
    }
    const length = run.tracks[0].result.slots.length;
    $("replay-scrub").value = cursor;
    put("replay-clock", clock(cursor === length ? run.session.end : run.session.start + cursor * 300));
    run.tracks.forEach(track => {
      const lane = $("history-tracks").querySelector(`[data-strategy="${track.id}"]`);
      if (!track.result.valid) {
        lane.querySelector(".track-cost").textContent = "—";
        lane.querySelector(".track-state").textContent = "Unavailable";
        lane.title = track.result.reason;
        return;
      }
      const slot = track.result.slots[cursor - 1];
      const kwh = slot?.cumulativeKwh || 0;
      lane.querySelector(".track-cost").textContent = money(slot?.cumulativeCost || 0);
      lane.querySelector(".track-meter > span").style.width = `${Math.min(100, kwh / run.row.gridKwh * 100)}%`;
      lane.querySelector(".track-energy").textContent = `${num(kwh)} / ${num(run.row.gridKwh)} kWh`;
      lane.querySelector(".track-state").textContent = kwh >= run.row.gridKwh - .000001 ? "✓ Energy delivered" : cursor === length ? "Energy shortfall" : !cursor ? "Ready to replay" : slot?.charging ? "ϟ Charging" : "Waiting";
    });
    if (cursor === length) {
      const base = run.tracks.find(track => track.id === "charge-now").result;
      const plan = run.tracks.find(track => track.id === "day-ahead").result;
      const percent = base.totalCost > 0 ? `${(Math.abs(run.saving) / base.totalCost * 100).toFixed(1)}%` : null;
      const savingLabel = Math.abs(run.saving) > .000001 && Math.abs(run.saving) < .005 ? "less than $0.01" : money(Math.abs(run.saving));
      put("replay-outcome", run.eligible ? `SPARC on ${rateName(run.config.tariff)} ${run.saving >= 0 ? "saves" : "costs an extra"} ${savingLabel}${percent ? ` (${percent})` : ""} versus immediate charging on standard delivery. Both deliver ${num(plan.energyKwh)} grid kWh.` : "At least one strategy could not deliver all the recorded energy. Savings are not counted for this session.");
    } else put("replay-outcome", cursor ? "Replay in progress. Final savings are compared after both strategies deliver the same energy." : "Play the replay or move the slider to inspect each strategy. Costs settle at the historical hourly prices.");
    const marker = $("history-chart").querySelector(".replay-marker");
    if (marker) {
      const x = 45 + cursor / length * 670;
      marker.setAttribute("x1", x); marker.setAttribute("x2", x);
    }
  }

  function frame(now) {
    if (!playing || !run?.valid) return;
    if (lastFrame !== null) accumulated += Math.min(250, now - lastFrame);
    lastFrame = now;
    const step = 1000 / Number($("replay-speed").value);
    const advance = Math.floor(accumulated / step);
    if (advance) {
      accumulated -= advance * step;
      cursor = Math.min(run.tracks[0].result.slots.length, cursor + advance);
      drawReplay();
    }
    if (cursor >= run.tracks[0].result.slots.length) { stop(); return; }
    replayFrame = requestAnimationFrame(frame);
  }

  function drawChart() {
    const svg = $("history-chart");
    svg.replaceChildren();
    if (!run?.valid) return;
    const slots = run.tracks.find(track => track.id === "day-ahead").result.slots;
    const prices = slots.map(slot => slot.billedPrice + slot.deliveryCents);
    const low = Math.min(0, ...prices), high = Math.max(1, ...prices);
    const x = i => 45 + i / slots.length * 670;
    const y = price => 148 - (price - low) / (high - low) * 115;
    svg.append(svgElement("title", {}, "Settled hourly prices and SPARC charging energy"));
    svg.append(svgElement("desc", {}, "The gray line is supply plus delivery in cents per kilowatt-hour. Green bars are SPARC grid kilowatt-hours per five-minute interval. The dashed line marks replay progress."));
    for (let i = 0; i <= 3; i++) {
      const price = low + (high - low) * i / 3;
      svg.append(svgElement("line", {x1:45, y1:y(price), x2:715, y2:y(price), stroke:"#e2e7d7", "stroke-width":1}));
      svg.append(svgElement("text", {x:36, y:y(price) + 3, "text-anchor":"end"}, `${price.toFixed(1)}¢`));
    }
    const maxEnergy = Math.max(.001, ...slots.map(slot => slot.energyKwh));
    slots.forEach((slot, i) => {
      if (slot.energyKwh <= 0) return;
      const height = slot.energyKwh / maxEnergy * 90;
      const rect = svgElement("rect", {x:x(i) + .4, y:148-height, width:Math.max(.5,670/slots.length - .8), height, fill:"#a2c664", opacity:.65, rx:1});
      rect.append(svgElement("title", {}, `${clock(slot.time)} · ${num(slot.energyKwh)} kWh · ${prices[i].toFixed(2)}¢/kWh`));
      svg.append(rect);
    });
    let path = "";
    prices.forEach((price, i) => { path += `${i ? "L" : "M"}${x(i)},${y(price)}L${x(i + 1)},${y(price)}`; });
    svg.append(svgElement("path", {d:path, fill:"none", stroke:"#667e69", "stroke-width":2, "stroke-linejoin":"round"}));
    [0, .25, .5, .75, 1].forEach(part => {
      const t = run.session.start + (run.session.end - run.session.start) * part;
      svg.append(svgElement("text", {x:x(slots.length*part), y:171, "text-anchor":"middle"}, clock(t)));
    });
    svg.append(svgElement("text", {x:715, y:190, "text-anchor":"end"}, `Tallest bar: ${num(maxEnergy)} grid kWh / 5 min`));
    svg.append(svgElement("line", {class:"replay-marker", x1:45, x2:45, y1:24, y2:149, stroke:"#203c31", "stroke-dasharray":"4 4", "stroke-width":1}));
  }

  function renderTable() {
    const list = filtered.slice().reverse();
    const resultById = new Map((batch || []).map(result => [result.row.id, result]));
    const subset = list.slice(page*pageSize, (page+1)*pageSize);
    const body = $("history-table-body");
    body.replaceChildren(...subset.map(row => {
      const tr = element("tr", String(row.id) === String(selected) ? "is-selected" : "");
      const first = element("td");
      const button = element("button", "", date(row.startLocal.slice(0, 10)));
      button.type = "button";
      button.setAttribute("aria-label", `Replay session ${row.id} on ${dateTime(row)}`);
      button.addEventListener("click", () => {
        selected = row.id;
        $("history-session").value = selected;
        updateReplay(); renderTable();
        $("replay-title").scrollIntoView({behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block:"start"});
      });
      first.append(button, element("small", "", `#${row.id} · ${clock(row.start)} → ${clock(row.end)}`));
      tr.append(first, element("td", "", num(row.gridKwh)));
      const result = resultById.get(row.id);
      const baseline = result?.tracks.find(track => track.id === "charge-now")?.result;
      const sparc = result?.tracks.find(track => track.id === "day-ahead")?.result;
      tr.append(element("td", "", baseline ? money(baseline.totalCost) : "—"), element("td", "", sparc ? money(sparc.totalCost) : "—"), element("td", result?.eligible ? result.saving >= 0 ? "positive" : "negative" : "", result?.eligible ? money(result.saving) : "—"));
      const status = result ? !result.valid ? "Skipped" : !result.eligible ? "Shortfall" : "Compared" : row.issue ? "Data issue" : "Ready";
      const cell = element("td");
      const chip = element("span", `status-chip${["Skipped","Shortfall","Data issue"].includes(status) ? " skipped" : ""}`, status);
      if (result?.reason || row.issue) chip.title = result?.reason || row.issue;
      cell.append(chip); tr.append(cell);
      return tr;
    }));
    if (!subset.length) {
      const tr = element("tr"), td = element("td", "empty-cell", "No sessions match these filters. Try a wider date range.");
      td.colSpan = 6; tr.append(td); body.append(tr);
    }
    put("table-count", list.length ? `${page*pageSize + 1}–${Math.min((page+1)*pageSize,list.length)} of ${num(list.length)} sessions` : "0 sessions");
    $("table-previous").disabled = !page;
    $("table-next").disabled = (page+1)*pageSize >= list.length;
  }

  async function runBatch(event) {
    event.preventDefault();
    if (!filtered.length) return;
    const token = ++batchToken;
    const settings = options();
    const results = [];
    $("run-history").disabled = true;
    $("export-history").disabled = true;
    $("batch-progress").hidden = false;
    $("batch-meter").value = 0;
    $("batch-meter").max = filtered.length;
    try {
      for (let i = 0; i < filtered.length; i++) {
        if (token !== batchToken) return;
        const row = filtered[i];
        const result = Lab.simulate(market, row, settings, nextRows.get(row.id));
        // Replay arrays are only needed for the selected session; retain small batch results.
        result.tracks.forEach(track => { track.result.slots = []; delete track.result.session; });
        delete result.session;
        results.push(result);
        if (i % 12 === 0 || i === filtered.length - 1) {
          $("batch-meter").value = i+1;
          put("batch-status", `Comparing session ${num(i+1)} of ${num(filtered.length)}…`);
          await new Promise(resolve => setTimeout(resolve, 0));
        }
      }
      if (token !== batchToken) return;
      batch = results;
      const summary = Lab.summarize(results);
      put("summary-cost", summary.comparable ? money(summary.sparcCost) : "—");
      put("summary-saving", summary.comparable ? money(summary.saving) : "—");
      put("summary-coverage", `${num(summary.comparable)} compared · ${num(summary.skipped)} skipped · ${num(summary.shortfall)} shortfall`);
      put("summary-energy", `${num(summary.energyKwh)} kWh`);
      put("summary-energy-note", "Comparable sessions only");
      put("summary-baseline", summary.comparable ? `${money(summary.baselineCost)} immediate · standard delivery` : "No comparable sessions in this range");
      const ratio = summary.baselineCost > 0 ? Math.abs(summary.saving) / summary.baselineCost * 100 : null;
      put("summary-saving-note", summary.comparable ? `${ratio === null ? "" : `${ratio.toFixed(1)}% ${summary.saving >= 0 ? "saved" : "extra cost"} · `}${settings.tariff === "time-of-day" ? "rate + timing" : "timing only"}` : "Savings require complete, equal-energy comparisons");
      put("batch-status", `${num(summary.comparable)} sessions compared: standard immediate charging versus SPARC on ${rateName(settings.tariff)}. ${settings.tariff === "time-of-day" ? "Savings include delivery-rate and timing changes." : "Savings reflect timing changes only."} ${num(summary.skipped)} skipped for data or price gaps; ${num(summary.shortfall)} excluded for energy shortfall. ${settings.window === "morning" ? "Morning availability is modeled, with extensions capped at the next session." : "Only recorded charging windows are used."} Totals cover this selected history; they are not annualized.`);
      $("export-history").disabled = false;
      renderTable();
    } catch (error) {
      put("history-error", `The comparison could not finish: ${error.message}`);
      $("history-error").hidden = false;
    } finally {
      if (token === batchToken) { $("run-history").disabled = false; $("batch-progress").hidden = true; }
    }
  }

  function exportCsv() {
    if (!batch) return;
    const blob = new Blob([Lab.exportCsv(batch, options())], {type:"text/csv;charset=utf-8"});
    const url = URL.createObjectURL(blob), anchor = element("a");
    anchor.href = url; anchor.download = `sparc-charging-history-${$("history-start").value}-${$("history-end").value}.csv`;
    document.body.append(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  put("source-count", `${num(rows.length)} home AC sessions`);
  put("source-period", `${date(rows[0].startLocal.slice(0, 10))} — ${date(rows.at(-1).startLocal.slice(0, 10))}`);
  for (const id of ["history-start","history-end"]) {
    $(id).min = rows[0].startLocal.slice(0,10);
    $(id).max = rows.at(-1).startLocal.slice(0,10);
  }
  $("history-start").value = rows[0].startLocal.slice(0,10);
  $("history-end").value = rows.at(-1).startLocal.slice(0,10);
  $("history-form").addEventListener("submit", runBatch);
  ["history-start","history-end","history-min-energy","history-window","history-departure","history-power","history-tariff"].forEach(id => $(id).addEventListener("change", updateFilters));
  $("history-session").addEventListener("change", () => { selected = $("history-session").value; updateReplay(); renderTable(); });
  $("replay-play").addEventListener("click", () => {
    if (playing) { stop(); return; }
    if (!run?.valid) return;
    if (cursor >= run.tracks[0].result.slots.length) cursor = 0;
    playing = true; lastFrame = null; accumulated = 0;
    put("replay-play", "Ⅱ Pause replay");
    drawReplay(); replayFrame = requestAnimationFrame(frame);
  });
  $("replay-reset").addEventListener("click", () => { stop(); cursor = 0; drawReplay(); });
  $("replay-scrub").addEventListener("input", () => { stop(); cursor = Number($("replay-scrub").value); drawReplay(); });
  $("table-previous").addEventListener("click", () => { page--; renderTable(); });
  $("table-next").addEventListener("click", () => { page++; renderTable(); });
  $("export-history").addEventListener("click", exportCsv);
  updateFilters();
})();
