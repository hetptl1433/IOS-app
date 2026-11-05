(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) {
    root.SparcNorwayLocalPage = api;
    if (root.document) api.mount(root.document, root.SPARC_NORWAY_LOCAL_DATA, root.SparcNorwayReplay, { window: root });
  }
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";
  const TIMEZONE = "Europe/Oslo";
  const number = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
  const decimal = new Intl.NumberFormat("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const dateParts = new Intl.DateTimeFormat("en-GB", { timeZone: TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit" });
  const clock = new Intl.DateTimeFormat("en-GB", { timeZone: TIMEZONE, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const fullClock = new Intl.DateTimeFormat("en-GB", { timeZone: TIMEZONE, year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "shortOffset" });
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const numeric = value => finite(value) ? decimal.format(value) : "—";
  const nok = value => finite(value) ? "NOK " + decimal.format(value) : "—";
  const percent = value => finite(value) ? decimal.format(value) + "%" : "—";
  const epochMs = value => typeof value === "number" ? (value < 1e12 ? value * 1000 : value) : Date.parse(value);
  const timeLabel = (value, full) => Number.isFinite(epochMs(value)) ? (full ? fullClock : clock).format(new Date(epochMs(value))) : "Time unavailable";
  const isReference = row => /hindsight|reference/i.test(row.id || "");
  const isBaseline = row => /^(charge-now|immediate|baseline)$/i.test(row.id || "");
  const isAvailable = row => row.available !== false && finite(row.costNok);
  function dateKey(row) {
    if (typeof row.startLocal === "string" && /^\d{4}-\d{2}-\d{2}/.test(row.startLocal)) return row.startLocal.slice(0, 10);
    const ms = epochMs(row.start);
    if (!Number.isFinite(ms)) return "";
    const parts = Object.fromEntries(dateParts.formatToParts(new Date(ms)).map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }
  function filterRows(rows, filters) {
    return rows.filter(row => {
      const date = dateKey(row);
      return date && (!filters.from || date >= filters.from) && (!filters.to || date <= filters.to) && (!filters.area || row.area === filters.area) && (!filters.site || row.site === filters.site);
    });
  }
  function validateFilters(filters) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(filters.from || "") || !/^\d{4}-\d{2}-\d{2}$/.test(filters.to || "")) return "Choose both a start and end date.";
    if (filters.from > filters.to) return "The start date must be on or before the end date.";
    return "";
  }
  function compactRun(run) {
    return { ...run, tracks: (run.tracks || []).map(track => track.result ? { ...track, result: { ...track.result, slots: [] } } : { ...track }) };
  }
  async function compareHistory(engine, market, rows, options) {
    const opts = options || {}, runs = [], size = Math.max(1, opts.chunkSize || 8);
    const yieldFrame = opts.yieldFrame || (() => new Promise(resolve => setTimeout(resolve, 0)));
    for (let i = 0; i < rows.length; i++) {
      if (opts.cancelled && opts.cancelled()) return null;
      runs.push(compactRun(engine.compareSession(market, rows[i])));
      if ((i + 1) % size === 0 || i + 1 === rows.length) {
        if (opts.progress) opts.progress(i + 1, rows.length);
        await yieldFrame();
      }
    }
    if (opts.cancelled && opts.cancelled()) return null;
    return { runs, summary: engine.summarize(runs) };
  }
  function bestResult(summary) {
    return (summary.results || []).filter(row => isAvailable(row) && !isReference(row)).sort((a, b) => a.costNok - b.costNok)[0] || null;
  }
  function description(row) {
    if (row.available === false) return row.reason || "Unavailable with this historical price source.";
    if (isReference(row)) return "Knows every future spot price. Reference only.";
    if (isBaseline(row)) return "Starts charging as soon as the vehicle is plugged in.";
    if (/timer/i.test(row.id)) return "A fixed start time in Europe/Oslo, with a finish-by-unplug safeguard.";
    if (/cap/i.test(row.id)) return "Waits for the price cap, with a finish-by-unplug safeguard.";
    if (/day|sparc|planner/i.test(row.id)) return "Plans with the prices available at the time; preserves the unplug deadline.";
    return "Uses the original session window and Norwegian spot prices.";
  }
  function chartModel(row, selected, baseline, metric) {
    const results = [selected, baseline].filter(Boolean);
    const start = epochMs(row.start), end = epochMs(row.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;
    const endValue = result => metric === "energy" ? result.energyKwh : result.costNok;
    const key = metric === "energy" ? "cumulativeKwh" : "cumulativeCost";
    const lines = results.map(result => {
      let running = 0;
      const points = [[start, 0]];
      for (const slot of result.slots || []) {
        const time = epochMs(finite(slot.availableStart) ? slot.availableStart : slot.time);
        if (!Number.isFinite(time)) continue;
        const increment = metric === "energy" ? (slot.energyKwh || 0) : (slot.energyKwh || 0) * (slot.priceNokKwh || 0);
        const value = finite(slot[key]) ? slot[key] : running + increment;
        // Slot values describe energy delivered over the interval. Infer the interval
        // duration from charger power, and finish no later than the unplug deadline.
        const duration = row.powerKw > 0 && slot.energyKwh > 0 ? slot.energyKwh / row.powerKw * 3600000 : 0;
        const intervalEnd = finite(slot.availableEnd) ? Math.min(epochMs(slot.availableEnd), time + duration) : time + duration;
        points.push([Math.max(start, Math.min(end, time)), running]);
        points.push([Math.max(start, Math.min(end, intervalEnd)), value]);
        running = value;
      }
      points.push([end, finite(endValue(result)) ? endValue(result) : running]);
      return points;
    });
    const values = lines.flatMap(points => points.map(point => point[1])).filter(Number.isFinite);
    let min = Math.min(0, ...values), max = Math.max(0, ...values);
    if (max === min) max = min + 1;
    const gap = (max - min) * .07; max += gap; if (min < 0) min -= gap;
    return { start, end, min, max, lines };
  }
  function mount(doc, data, engine, options) {
    const opts = options || {}, win = opts.window || (typeof window !== "undefined" ? window : null);
    const el = id => doc.getElementById(id);
    const node = (tag, text, className) => { const item = doc.createElement(tag); if (text !== undefined) item.textContent = text; if (className) item.className = className; return item; };
    const state = { rows: [], runs: [], summary: null, market: null, index: 0, token: 0, running: false };
    function error(message) { el("nl-error").textContent = message; el("nl-error").hidden = !message; }
    function filters() { return { from: el("nl-date-from").value, to: el("nl-date-to").value, area: el("nl-area").value || "", site: el("nl-site").value || "" }; }
    function updateFilters() {
      state.token++; state.running = false; state.runs = []; state.summary = null;
      const current = filters(), invalid = validateFilters(current);
      state.rows = invalid ? [] : filterRows(data.rows, current);
      el("nl-filter-count").textContent = invalid || `${number.format(state.rows.length)} reported sessions selected. Sessions missing usable inputs or prices will be counted separately.`;
      el("nl-run").disabled = !!invalid || !state.rows.length;
      el("nl-results").hidden = true; el("nl-replay").hidden = true; el("nl-download").disabled = true; el("nl-progress").hidden = true;
      el("nl-run-status").textContent = "Ready to compare this selection.";
      error("");
    }
    function addOptions(id, rows) {
      const target = el(id);
      rows.forEach(row => { const option = node("option", row.label); option.value = row.id; target.append(option); });
    }
    function renderProvenance() {
      const p = data.provenance || {}, coverage = data.coverage || {};
      el("nl-source-line").textContent = `${data.dataSource || "Norway Dataset 1"} · ${number.format(data.rows.length)} reported sessions · ${coverage.firstDate || "start unavailable"} to ${coverage.lastDate || "end unavailable"} · NOK · original session dates.`;
      const priceCoverage = data.priceCoverage || p.coverage;
      el("nl-price-line").textContent = priceCoverage && priceCoverage.firstDate && priceCoverage.lastDate ? `Norwegian price coverage: ${priceCoverage.firstDate} to ${priceCoverage.lastDate}. Sessions outside usable price coverage remain in the history and are counted as excluded.` : p.priceCoverage || "Matching Norwegian price coverage is checked for every session before calculating costs.";
      const summary = [p.summary, p.priceCoverage, p.priceSource, p.publicationRule].filter(value => typeof value === "string" && value.trim());
      el("nl-provenance-summary").textContent = summary.join(" ") || "The loaded bundle uses historical Norwegian spot prices at the original charging dates. Sessions without matching prices are excluded from cost totals.";
      if (typeof p.costScope === "string" && p.costScope) el("nl-cost-scope").textContent = p.costScope;
      const mapping = p.areaMapping;
      if (mapping && typeof mapping === "object" && !Array.isArray(mapping)) {
        const grouped = new Map();
        for (const [site, area] of Object.entries(mapping)) { if (!grouped.has(area)) grouped.set(area, []); grouped.get(area).push(site); }
        el("nl-area-note").textContent = `Price areas: ${[...grouped.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([area, sites]) => `${area}: ${sites.sort().join(", ")}`).join("; ")}. All times use Europe/Oslo.`;
      } else el("nl-area-note").textContent = typeof mapping === "string" ? mapping : p.areaNote || "Each charging location uses its assigned Norwegian electricity price area. All displayed times use Europe/Oslo, including daylight saving.";
      const publication = p.publishedAssumption;
      el("nl-publication-note").textContent = publication && typeof publication === "object" ? `Price availability assumption: ${publication.localTime || "prior-day publication"}. ${publication.description || "The source does not include historical publication timestamps."}` : "";
      el("nl-attribution").textContent = p.attribution || "";
      el("nl-charging-attribution").textContent = p.chargingAttribution || "";
      for (const source of p.sources || []) {
        if (!source || typeof source.url !== "string" || !/^https?:\/\//i.test(source.url)) continue;
        const li = node("li"), a = node("a", source.label || "Source"); a.href = source.url; a.target = "_blank"; a.rel = "noopener noreferrer"; li.append(a); el("nl-source-links").append(li);
      }
      if (typeof p.licenseUrl === "string" && /^https?:\/\//i.test(p.licenseUrl)) {
        const li = node("li"), a = node("a", `Price-data licence: ${p.license || "view licence"}`); a.href = p.licenseUrl; a.target = "_blank"; a.rel = "noopener noreferrer"; li.append(a); el("nl-source-links").append(li);
      }
    }
    function renderSummary(summary) {
      state.summary = summary;
      const winner = bestResult(summary), common = Number(summary.comparable) || 0;
      const current = filters();
      el("nl-results").hidden = false;
      el("nl-common-note").textContent = `${current.from} to ${current.to}${current.area ? " · " + current.area : " · all price areas"}${current.site ? " · location " + current.site : ""}. Savings compare with immediate charging at the same Norwegian spot prices. Only sessions completed by every available strategy enter every cost total.`;
      el("nl-winner").textContent = common && winner ? winner.label : "No comparable completed sessions";
      el("nl-winner-saving").textContent = common && winner ? percent(winner.savingPct) : "—";
      el("nl-winner-note").textContent = common && winner ? `${nok(winner.savingNok)} saved · charging cost ${nok(winner.costNok)}` : "Try dates and locations with complete price and charging inputs.";
      el("nl-baseline").textContent = common ? nok(summary.baselineCostNok) : "—";
      el("nl-comparable").textContent = number.format(common);
      el("nl-energy").textContent = `${numeric(summary.energyKwh)} kWh delivered by every available strategy.`;
      const tableRows = (summary.results || []).map(result => {
        const tr = node("tr"); tr.dataset.strategy = result.id;
        if (winner && common && result.id === winner.id) tr.className = "nl-is-best";
        if (isReference(result)) tr.className = "nl-is-reference";
        if (result.available === false) tr.className = "nl-is-unavailable";
        const name = node("td"); name.append(node("span", result.label || result.id, "nl-strategy-name"), node("span", description(result), "nl-strategy-note")); tr.append(name);
        if (result.available === false) {
          const unavailable = node("td", "Unavailable"); unavailable.colSpan = 3; tr.append(unavailable);
        } else {
          tr.append(node("td", common ? numeric(result.costNok) : "—"), node("td", common ? numeric(result.savingNok) : "—"));
          const saving = node("td", common ? percent(result.savingPct) : "—"); if (result.savingNok < 0) saving.className = "nl-is-negative"; else if (result.savingNok > 0) saving.className = "nl-is-positive"; tr.append(saving);
        }
        return tr;
      });
      el("nl-strategy-rows").replaceChildren(...tableRows);
      const skipped = Number(summary.skipped) || 0, shortfall = Number(summary.shortfall) || 0;
      el("nl-exclusion-title").textContent = `${number.format((Number(summary.total) || 0) - common)} sessions outside the common cost comparison`;
      el("nl-exclusion-summary").textContent = `${number.format(summary.total || 0)} selected reports: ${number.format(common)} common completed sessions, ${number.format(skipped)} with missing or unusable inputs, and ${number.format(shortfall)} outside the completion requirement. Excluded sessions contribute no savings.`;
      el("nl-exclusion-list").replaceChildren(...Object.entries(summary.exclusions || {}).map(([reason, count]) => node("li", `${number.format(count)} · ${reason}`)));
      el("nl-download").disabled = !state.runs.length;
    }
    function svgElement(tag, attributes, text) {
      const item = doc.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key, value] of Object.entries(attributes || {})) item.setAttribute(key, value);
      if (text !== undefined) item.textContent = text;
      return item;
    }
    function renderChart(id, row, selected, baseline, metric) {
      const target = el(id), model = chartModel(row, selected, baseline, metric);
      target.replaceChildren(); if (!model) return;
      const width = 500, height = 240, left = 52, right = 17, top = 22, bottom = 42;
      const x = time => left + (time - model.start) / (model.end - model.start) * (width - left - right);
      const y = value => height - bottom - (value - model.min) / (model.max - model.min) * (height - top - bottom);
      const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `${metric === "energy" ? "Cumulative energy in kWh" : "Cumulative electricity cost in NOK"}, selected strategy versus immediate charging. ${timeLabel(row.start)} to ${timeLabel(row.end)}, Europe/Oslo.` });
      for (let step = 0; step <= 4; step++) {
        const value = model.min + (model.max - model.min) * step / 4;
        svg.append(svgElement("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), class: "nl-grid" }));
        svg.append(svgElement("text", { x: left - 8, y: y(value) + 4, "text-anchor": "end" }, value.toFixed(1)));
      }
      for (let step = 0; step <= 2; step++) {
        const time = model.start + (model.end - model.start) * step / 2;
        svg.append(svgElement("text", { x: x(time), y: height - 16, "text-anchor": step === 0 ? "start" : step === 2 ? "end" : "middle" }, timeLabel(time)));
      }
      model.lines.forEach((points, index) => {
        const d = points.map((point, i) => `${i ? "L" : "M"}${x(point[0]).toFixed(2)},${y(point[1]).toFixed(2)}`).join(" ");
        svg.append(svgElement("path", { d, class: index === 0 ? "nl-selected-line" : "nl-baseline-line" }));
      });
      target.append(svg);
    }
    function renderSession(index) {
      if (!state.rows.length) return;
      state.index = Math.max(0, Math.min(state.rows.length - 1, Number.isFinite(index) ? Math.trunc(index) : 0));
      const row = state.rows[state.index], run = engine.compareSession(state.market, row);
      el("nl-replay").hidden = false; el("nl-session-number").value = String(state.index + 1); el("nl-session-number").max = String(state.rows.length);
      el("nl-session-total").textContent = `of ${number.format(state.rows.length)}`;
      el("nl-previous").disabled = state.index === 0; el("nl-next").disabled = state.index === state.rows.length - 1;
      const location = (data.locations || []).find(item => item.id === row.site);
      el("nl-session-area").textContent = `${row.area || "Area unavailable"} · ${location ? location.label : row.site || "Location unavailable"}`;
      el("nl-session-note").textContent = `Session ${row.id} · ${timeLabel(row.start, true)} → ${timeLabel(row.end, true)} · ${numeric(row.gridKwh)} kWh reported · ${numeric(row.powerKw)} kW estimated charger`;
      const select = el("nl-replay-strategy"), previous = select.value;
      const tracks = run.tracks || [];
      select.replaceChildren(...tracks.filter(track => track.available !== false && track.result).map(track => { const option = node("option", track.label || track.id); option.value = track.id; return option; }));
      if (tracks.some(track => track.id === previous && track.available !== false && track.result)) select.value = previous;
      else {
        const winner = state.summary && bestResult(state.summary), choice = tracks.find(track => winner && track.id === winner.id && track.result) || tracks.find(track => track.available !== false && track.result);
        select.value = choice ? choice.id : "";
      }
      select.disabled = !tracks.some(track => track.available !== false && track.result);
      const track = tracks.find(item => item.id === select.value), result = track && track.result;
      const baselineTrack = tracks.find(item => isBaseline(item)), baseline = baselineTrack && baselineTrack.result;
      if (!result || result.valid === false) {
        el("nl-session-result").textContent = run.reason || (track && track.reason) || "This report cannot be replayed with the available session inputs and spot prices.";
        el("nl-charts").hidden = true; el("nl-hourly-detail").hidden = true; el("nl-chart-strategy").textContent = "No available replay";
        el("nl-hourly-rows").replaceChildren(); return;
      }
      const saving = baseline && finite(baseline.costNok) && finite(result.costNok) ? baseline.costNok - result.costNok : null;
      el("nl-session-result").textContent = `${track.label}: ${numeric(result.energyKwh)} of ${numeric(result.requiredKwh)} kWh delivered · ${nok(result.costNok)}${saving === null ? "" : ` · ${nok(saving)} saved versus immediate charging`}. ${result.ready ? "Ready before unplugging." : "The required energy was not completed before unplugging."}${run.eligible === false ? " This session is outside the common cost totals." : ""}`;
      el("nl-charts").hidden = false; el("nl-hourly-detail").hidden = false; el("nl-chart-strategy").textContent = track.label;
      renderChart("nl-energy-chart", row, result, baseline, "energy"); renderChart("nl-cost-chart", row, result, baseline, "cost");
      el("nl-hourly-rows").replaceChildren(...(result.slots || []).filter(slot => slot.energyKwh > 0).map(slot => {
        const tr = node("tr"); tr.append(node("td", timeLabel(slot.time, true)), node("td", numeric(slot.energyKwh)), node("td", numeric(slot.priceNokKwh * 100)), node("td", numeric(slot.energyKwh * slot.priceNokKwh))); return tr;
      }));
    }
    async function runHistory() {
      const invalid = validateFilters(filters()); if (invalid) { error(invalid); return null; }
      if (!state.rows.length) { error("No charging sessions match these filters."); return null; }
      error(""); const token = ++state.token; state.running = true;
      el("nl-run").disabled = true; el("nl-download").disabled = true; el("nl-progress").hidden = false; el("nl-progress").max = state.rows.length; el("nl-progress").value = 0;
      el("nl-results").hidden = true; el("nl-replay").hidden = true;
      el("nl-run-status").textContent = `Comparing ${number.format(state.rows.length)} original historical reports…`;
      try {
        const compared = await compareHistory(engine, state.market, state.rows, { cancelled: () => token !== state.token, yieldFrame: opts.yieldFrame, chunkSize: opts.chunkSize, progress: (done, total) => {
          if (token !== state.token) return; el("nl-progress").value = done;
          el("nl-run-status").textContent = `Compared ${number.format(done)} of ${number.format(total)} historical reports…`;
        } });
        if (!compared || token !== state.token) return null;
        state.runs = compared.runs; renderSummary(compared.summary);
        el("nl-run-status").textContent = `Comparison complete. ${number.format(compared.summary.comparable || 0)} sessions enter the same-energy cost comparison.`;
        const first = compared.runs.findIndex(run => run.eligible && run.valid !== false);
        renderSession(first >= 0 ? first : 0); return compared;
      } catch (err) {
        if (token === state.token) { error(`The historical comparison could not finish: ${err.message || err}`); el("nl-run-status").textContent = "Comparison stopped. No partial savings are shown."; }
        return null;
      } finally {
        if (token === state.token) { state.running = false; el("nl-progress").hidden = true; el("nl-run").disabled = !state.rows.length; }
      }
    }
    function download() {
      if (!state.runs.length || !win || !win.URL || !win.Blob) return;
      const url = win.URL.createObjectURL(new win.Blob([engine.exportCsv(state.runs)], { type: "text/csv;charset=utf-8" }));
      const a = node("a"); a.href = url; a.download = `norway-history-${filters().from}-${filters().to}.csv`; doc.body.append(a); a.click(); a.remove(); win.setTimeout(() => win.URL.revokeObjectURL(url), 1000);
    }
    if (!data || !Array.isArray(data.rows) || !data.rows.length || !engine || typeof engine.createMarket !== "function" || typeof engine.compareSession !== "function" || typeof engine.summarize !== "function") {
      error("The Norway history or Norwegian spot-price simulation files could not be loaded. Reload this page after the local data bundle is available.");
      el("nl-source-line").textContent = "Norway history and price data are not loaded."; el("nl-run-status").textContent = "Waiting for the Norway history and price files."; return { state };
    }
    try { state.market = engine.createMarket(data); } catch (err) { error(`The Norwegian price data could not be prepared: ${err.message || err}`); return { state }; }
    renderProvenance();
    const coverage = data.coverage || {}, dates = data.rows.map(dateKey).filter(Boolean).sort();
    const from = coverage.firstDate || dates[0], to = coverage.lastDate || dates[dates.length - 1];
    ["nl-date-from", "nl-date-to"].forEach(id => { el(id).min = from; el(id).max = to; });
    el("nl-date-from").value = from; el("nl-date-to").value = to;
    addOptions("nl-area", data.areas || [...new Set(data.rows.map(row => row.area).filter(Boolean))].sort().map(id => ({ id, label: id })));
    addOptions("nl-site", data.locations || [...new Set(data.rows.map(row => row.site).filter(Boolean))].sort().map(id => ({ id, label: id })));
    ["nl-date-from", "nl-date-to", "nl-area", "nl-site"].forEach(id => el(id).addEventListener("change", updateFilters));
    el("nl-filter-form").addEventListener("submit", event => { event.preventDefault(); runHistory(); });
    el("nl-download").addEventListener("click", download);
    el("nl-previous").addEventListener("click", () => renderSession(state.index - 1)); el("nl-next").addEventListener("click", () => renderSession(state.index + 1));
    el("nl-session-number").addEventListener("change", () => renderSession(Number(el("nl-session-number").value) - 1));
    el("nl-replay-strategy").addEventListener("change", () => renderSession(state.index));
    el("nl-find-form").addEventListener("submit", event => { event.preventDefault(); const wanted = el("nl-find-id").value.trim(), index = state.rows.findIndex(row => String(row.id) === wanted); if (index < 0) el("nl-session-result").textContent = "That session ID is not in the selected dates and locations."; else renderSession(index); });
    updateFilters();
    if (data.historySummary && Array.isArray(data.historySummary.results)) {
      renderSummary(data.historySummary);
      el("nl-run-status").textContent = `Showing the saved full-history comparison${data.generatedAt ? " generated " + timeLabel(data.generatedAt, true) : ""}. Run again to compare a filtered selection or download session results.`;
      const first = state.rows.findIndex(row => {
        if (typeof engine.prepare === "function" && !engine.prepare(state.market, row).valid) return false;
        const run = engine.compareSession(state.market, row); return run.valid && run.eligible;
      });
      renderSession(first >= 0 ? first : 0);
    }
    return { state, runHistory, updateFilters, renderSession, renderSummary };
  }
  return { TIMEZONE, dateKey, filterRows, validateFilters, compactRun, compareHistory, bestResult, chartModel, nok, percent, timeLabel, mount };
});
