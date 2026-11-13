"use strict";
(() => {
  const root = document.getElementById("window-comparison");
  if (!root) return;
  root.innerHTML = `
    <div class="wc-heading"><div><p class="eyebrow">YOUR CHARGING HISTORY</p><h2 id="window-comparison-title">What if every charge had all night?</h2><p>The same electricity. Recorded times versus an 8 p.m.–8 a.m. window.</p></div><span class="wc-tag">TESLAMATE HISTORY</span></div>
    <form id="wc-form" class="wc-filters">
      <div><label for="wc-from">From</label><input id="wc-from" type="date" required></div>
      <div><label for="wc-to">Through</label><input id="wc-to" type="date" required></div>
      <div><label for="wc-tariff">SPARC delivery rate</label><select id="wc-tariff"><option value="time-of-day">Time-of-day</option><option value="standard">Standard · timing only</option></select></div>
      <button class="button button-primary" id="wc-run" type="submit">Compare windows <span aria-hidden="true">↗</span></button>
    </form>
    <p id="wc-status" class="wc-status" role="status" aria-live="polite">Loading the recorded charging history…</p>
    <p id="wc-error" class="wc-error" role="alert" hidden></p>
    <div id="wc-results" hidden>
      <div class="wc-metrics">
        <div class="wc-metric wc-impact"><span id="wc-impact-label">ADDITIONAL SAVINGS</span><strong id="wc-impact">—</strong><p id="wc-impact-note"></p></div>
        <div class="wc-metric"><span>ENERGY IN BOTH WINDOWS</span><strong id="wc-energy">—</strong><p>Same grid kWh and modeled charger power</p></div>
        <div class="wc-metric"><span>MATCHED CHARGING EVENTS</span><strong id="wc-count">—</strong><p id="wc-coverage"></p></div>
      </div>
      <p id="wc-baseline" class="wc-baseline"></p>
      <div class="wc-charts">
        <div><h3>Charging cost</h3><p class="wc-chart-note" id="wc-rate-note"></p><div id="wc-costs" class="wc-costs"></div><p class="wc-chart-note">Savings percentages below the bars use the same recorded-time baseline.</p></div>
        <div><h3>When SPARC delivers the energy</h3><p class="wc-chart-note">Share of total kWh by Chicago clock hour</p><div class="wc-legend"><span><i class="wc-original"></i>Recorded times</span><span><i class="wc-fixed"></i>8 p.m.–8 a.m.</span></div><div class="wc-chart-scroll"><svg id="wc-hours" viewBox="0 0 600 205" role="img" aria-label="Charging energy by hour for recorded and overnight windows"></svg></div></div>
      </div>
      <details class="wc-method"><summary>How this comparison works</summary><p>Each recorded event keeps its grid energy and modeled power. Only its available hours change: 8 p.m. on its original start date to 8 a.m. the next morning, in America/Chicago. Charging stops when the energy target is met. SPARC plans with day-ahead prices available at the time; settled hourly prices determine cost.</p><p>Both windows use exactly the same completed events. Their savings use one baseline: immediate charging at the recorded time on standard delivery. Time-of-day savings include a delivery-rate change. Standard delivery isolates timing. This panel uses the TeslaMate file independently of the vehicle and battery settings above.</p><p>Events are replayed independently, including multiple events on the same date. Overnight availability is hypothetical; recorded times mark charging events, not verified plug-in and unplug times. Original boundaries are rounded outward to five minutes. Fixed study rates cover supply and delivery, excluding other fees, taxes and capacity charges. Totals cover the selected period and are not annualized.</p><p id="wc-quality"></p><ul id="wc-exclusions"></ul></details>
    </div>`;
  const $ = id => document.getElementById(id);
  const put = (id, text) => { $(id).textContent = text; };
  const History = window.SPARC_CHARGING_HISTORY, Lab = window.SparcChargingHistory, Comparison = window.SparcChargingWindows;
  if (!History || !Lab || !Comparison || !window.SPARC_SIM_DATA) {
    put("wc-error", "Charging history could not load. Reload the complete simulation page.");
    $("wc-error").hidden = false;
    $("wc-status").hidden = true;
    $("wc-run").disabled = true;
    return;
  }
  const market = Lab.createMarket(window.SPARC_SIM_DATA, History);
  const first = History.rows[0].startLocal.slice(0, 10), last = History.rows.at(-1).startLocal.slice(0, 10);
  const number = (n, digits = 0) => n.toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const money = n => new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(Math.abs(n) < .005 ? 0 : n);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svg = (tag, attributes, text) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  };
  for (const [id, value] of [["wc-from", first], ["wc-to", last]]) {
    $(id).min = first; $(id).max = last; $(id).value = value;
  }
  let token = 0;

  function render(summary, tariff) {
    $("wc-results").hidden = !summary.compared;
    put("wc-status", `${number(summary.compared)} of ${number(summary.total)} events compared. ${number(summary.skipped)} excluded for data or price gaps; ${number(summary.shortfall)} excluded for incomplete energy.`);
    if (!summary.compared) {
      put("wc-status", summary.total ? "No equal-energy comparisons are available in this range. Try a wider date range." : "No charging events fall in this date range.");
      const reasons = Object.entries(summary.exclusions).map(([reason, count]) => `${count}: ${reason}`).join(" ");
      if (reasons) { put("wc-error", reasons); $("wc-error").hidden = false; }
      return;
    }
    const better = summary.additionalSaving >= 0;
    root.classList.toggle("wc-more-expensive", !better);
    put("wc-impact-label", better ? "ADDITIONAL SAVINGS" : "ADDITIONAL COST");
    put("wc-impact", money(Math.abs(summary.additionalSaving)));
    const costPct = summary.originalCost > 0 ? `${number(Math.abs(summary.additionalSaving) / summary.originalCost * 100, 1)}% ${better ? "lower" : "higher"} SPARC cost` : "Compared with SPARC at recorded times";
    put("wc-impact-note", `${costPct} with the overnight window`);
    put("wc-energy", `${number(summary.energyKwh, 2)} kWh`);
    put("wc-count", number(summary.compared));
    put("wc-coverage", `${number(summary.skipped + summary.shortfall)} events excluded from both windows`);
    put("wc-baseline", `One shared baseline: ${money(summary.baselineCost)} for immediate charging at the recorded times on standard delivery.`);
    put("wc-rate-note", tariff === "standard" ? "Standard delivery in both SPARC windows · timing only" : "Time-of-day delivery in both SPARC windows · rate + timing savings");
    const values = [
      ["Recorded-time immediate baseline", "wc-base", summary.baselineCost, null, null],
      ["SPARC · recorded times", "wc-original", summary.originalCost, summary.originalSaving, summary.originalPct],
      ["SPARC · 8 p.m.–8 a.m.", "wc-fixed", summary.fixedCost, summary.fixedSaving, summary.fixedPct],
    ];
    const low = Math.min(0, ...values.map(v => v[2])), high = Math.max(0, ...values.map(v => v[2]));
    const span = high - low || 1, zero = -low / span * 100;
    $("wc-costs").replaceChildren(...values.map(([label, style, cost, saving, percent]) => {
      const item = el("div", "wc-cost-row"), heading = el("div", "wc-cost-label");
      heading.append(el("span", "", label), el("strong", "", money(cost)));
      const track = el("div", "wc-bar-track"), bar = el("span", `wc-bar ${style}`), axis = el("i", "wc-zero");
      axis.style.left = `${zero}%`;
      bar.style.left = `${Math.min(zero, (cost - low) / span * 100)}%`;
      bar.style.width = `${Math.abs(cost) / span * 100}%`;
      track.setAttribute("aria-hidden", "true");
      track.append(bar, axis);
      item.append(heading, track);
      if (saving !== null) item.append(el("p", "wc-cost-saving", `${money(Math.abs(saving))} ${saving >= 0 ? "saved" : "extra cost"}${percent === null ? "" : ` (${number(Math.abs(percent), 1)}%)`}`));
      return item;
    }));
    drawHours(summary);
    put("wc-quality", `${number(summary.powerAdjustments)} events use the existing simulator's power correction because the recorded peak cannot explain their energy. The corrected power is identical in both windows. Source: charging-history-data/charging_sessions_tm1.csv.`);
    $("wc-exclusions").replaceChildren(...Object.entries(summary.exclusions).map(([reason, count]) => el("li", "", `${number(count)} events: ${reason}`)));
  }

  function drawHours(summary) {
    const chart = $("wc-hours"); chart.replaceChildren();
    const max = Math.max(...summary.originalHourly, ...summary.fixedHourly, .001) / summary.energyKwh * 100;
    const ceiling = Math.max(10, Math.ceil(max / 10) * 10), width = 548 / 24;
    const x = h => 36 + h * width, y = p => 164 - p / ceiling * 128;
    chart.append(svg("title", {}, "Equal total energy, different charging hours"));
    chart.append(svg("desc", {}, `${number(summary.energyKwh, 2)} kWh in each window. Brown bars show the recorded-time SPARC schedule. Green bars show the fixed overnight SPARC schedule. All hours use Chicago time.`));
    for (const [start, end] of [[0, 8], [20, 24]]) chart.append(svg("rect", {x:x(start), y:26, width:(end-start)*width, height:138, fill:"#edf4df"}));
    for (let tick = 0; tick <= 2; tick++) {
      const value = ceiling / 2 * tick;
      chart.append(svg("line", {x1:36, x2:584, y1:y(value), y2:y(value), stroke:"#dce0d3"}));
      chart.append(svg("text", {x:29, y:y(value)+4, "text-anchor":"end"}, `${number(value)}%`));
    }
    for (let h = 0; h < 24; h++) {
      for (const [i, hours, color, label] of [[0, summary.originalHourly, "#aa8060", "Recorded"], [1, summary.fixedHourly, "#5b8134", "Overnight"]]) {
        const share = hours[h] / summary.energyKwh * 100;
        const bar = svg("rect", {x:x(h)+2+i*width*.42, y:y(share), width:width*.36, height:164-y(share), rx:1, fill:color});
        bar.append(svg("title", {}, `${label}, ${h}:00–${h+1}:00: ${number(hours[h], 2)} kWh (${number(share, 1)}%)`));
        chart.append(bar);
      }
    }
    for (const [h, label] of [[0,"12 AM"],[4,"4 AM"],[8,"8 AM"],[12,"12 PM"],[16,"4 PM"],[20,"8 PM"]]) chart.append(svg("text", {x:x(h), y:185, "text-anchor":h ? "middle" : "start"}, label));
    chart.append(svg("text", {x:584, y:202, "text-anchor":"end"}, "Shaded hours: fixed overnight availability"));
  }

  async function run(event) {
    event?.preventDefault();
    const current = ++token;
    $("wc-results").hidden = true;
    $("wc-error").hidden = true;
    const from = $("wc-from").value, to = $("wc-to").value, tariff = $("wc-tariff").value;
    if (!from || !to || from > to || !$("wc-form").checkValidity()) {
      put("wc-error", "Choose valid dates with From on or before Through.");
      $("wc-error").hidden = false;
      put("wc-status", "Update the dates to compare charging windows.");
      $("wc-run").disabled = false;
      root.setAttribute("aria-busy", "false");
      return;
    }
    const rows = History.rows.filter(row => row.startLocal.slice(0, 10) >= from && row.startLocal.slice(0, 10) <= to);
    $("wc-run").disabled = true;
    root.setAttribute("aria-busy", "true");
    put("wc-status", "Comparing the same charging events in both windows…");
    try {
      const summary = await Comparison.compareHistory(market, rows, {tariff, cancelled: () => current !== token,
        onProgress: (done, total) => put("wc-status", `Comparing ${number(done)} of ${number(total)} charging events…`)});
      if (current === token && summary) render(summary, tariff);
    } catch (error) {
      if (current === token) {
        put("wc-error", `The window comparison could not finish: ${error.message}`);
        $("wc-error").hidden = false;
        put("wc-status", "Adjust the dates or retry the comparison.");
      }
    } finally {
      if (current === token) { $("wc-run").disabled = false; root.setAttribute("aria-busy", "false"); }
    }
  }
  $("wc-form").addEventListener("submit", run);
  for (const id of ["wc-from", "wc-to", "wc-tariff"]) $(id).addEventListener("change", run);
  run();
})();
