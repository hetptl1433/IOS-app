"use strict";
// Fills the simple results pages (index.html, accuracy.html, how-it-works.html) from data.js and example.js.
(() => {
  const DATA = window.SPARC_RESULTS || {};
  const EXAMPLE = window.SPARC_EXAMPLE || null;
  const DRIVERS = ["Average", "Moderate", "Heavy"];
  const minus = "−";
  const $ = (selector) => document.querySelector(selector);
  const money = (v) => (v < -0.5 ? minus : "") + "$" + Math.round(Math.abs(v)).toLocaleString("en-US");
  const money2 = (v) => (v < 0 ? minus : "") + "$" + Math.abs(v).toFixed(2);
  const cents = (v, digits = 2) => `${v.toFixed(digits)}¢`;
  const pct = (v) => `${Math.round(v * 100)}%`;
  const byDriver = (rows) => Object.fromEntries((rows || []).map((r) => [r.driver, r]));
  const best = (rows, key) => rows.reduce((a, b) => (b[key] > a[key] ? b : a));

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (key === "class") node.className = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }

  function fill(id, ...content) {
    const node = document.getElementById(id);
    if (node) node.replaceChildren(...content.flat(Infinity).filter((c) => c !== null && c !== undefined));
  }

  function stored(key, fallback) {
    try {
      return localStorage.getItem(key) || fallback;
    } catch {
      return fallback;
    }
  }

  function keep(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      // A private window: not kept.
    }
  }

  // The "Test models" link opens the app's page; opened from a file, it points where the app will be.
  for (const link of document.querySelectorAll("[data-app-link]")) {
    if (location.protocol === "file:") {
      link.href = "http://127.0.0.1:8765/";
      link.title = "Start SPARC first: double-click start.command in the repository folder.";
    }
  }

  let driver = stored("sparc.driver", "Average");
  if (!DRIVERS.includes(driver)) driver = "Average";

  function driverButtons(id, onchange) {
    const head = byDriver(DATA.headline);
    fill(id, el("span", { class: "label" }, "Driver"), DRIVERS.map((name) => el("button", {
      type: "button", "aria-pressed": String(name === driver),
      onclick: () => {
        driver = name;
        keep("sparc.driver", name);
        onchange();
      },
    }, head[name] ? `${name} · ${head[name].kwh_per_day} kWh a night, about ${head[name].miles_per_day} miles` : name)));
  }

  function barRows(rows, scale) {
    return rows.map((row) => el("div", { class: "bar-row" },
      el("span", { class: "bar-name" }, row.name, row.small ? el("small", {}, row.small) : null),
      el("span", { class: "bar-track" },
        el("span", { class: `bar-fill ${row.kind || ""}`, style: `width: ${Math.max(1, (row.value / scale) * 70)}%` }),
        el("span", { class: "bar-value" }, row.text))));
  }

  // ---------- index.html: the results in plain words ----------

  function results() {
    const head = byDriver(DATA.headline);
    const plugIn = DATA.plug_in || [];
    function draw() {
      driverButtons("drivers", draw);
      const h = head[driver];
      const year = h.default_per_night * 365;
      fill("cost-default", money(year));
      fill("cost-plan", money(year - h.plan));
      fill("save-plan", el("b", {}, `Saves ${money(h.plan)}`), ` a year, ${h.plan_pct}% less.`);
      fill("cost-buffer", money(year - h.buffer));
      fill("save-buffer", el("b", {}, `Saves ${money(h.buffer)}`), ` a year, ${h.buffer_pct}% less.`);

      const timer = best(DATA.strategies.filter((s) => s.family === "Timer"), driver);
      const ai = best(DATA.forecasts.filter((f) => f.family === "Foundation model"), driver);
      const rows = [
        { name: "Charge right away", small: "Standard delivery rate. The starting point.", value: year },
        { name: "Charge right away", small: "Time-of-Day delivery rate", value: year - h.rate_alone },
        { name: `Best fixed timer: ${timer.strategy.replace("Timer ", "start at ")}`, small: "Time-of-Day delivery rate",
          value: year - timer[driver] },
        { name: "SPARC's plan", small: "Cheapest hours by the day-ahead price", value: year - h.plan, kind: "plan" },
        { name: `Best AI forecast: ${ai.model}`, small: "SPARC's planner with an AI price forecast", value: year - ai[driver],
          kind: "ai" },
        { name: "SPARC + battery buffer", small: "Buys more on cheap nights. Not in the app yet.", value: year - h.buffer,
          kind: "buffer" },
        { name: "Perfect hindsight", small: "Knows every price in advance: impossible in real life", value: year - h.hindsight,
          kind: "ref" },
      ].map((row) => ({ ...row, text: money(row.value) }));
      fill("cost-bars", barRows(rows, year));

      const accurate = DATA.accuracy.filter((a) => a.family === "Foundation model")
        .reduce((a, b) => (b.rms_error < a.rms_error ? b : a));
      const accurateSaving = DATA.forecasts.find((f) => f.model === accurate.model);
      const sharper = 1 - accurate.rms_error / accurate.day_ahead_rms;
      const gap = h.plan - accurateSaving[driver];
      const night = plugIn.find((p) => p.driver === driver && p.plug_in === "8 p.m.");
      const late = plugIn.find((p) => p.driver === driver && p.plug_in === "11 p.m.");
      const facts = [
        [money(h.rate_alone), "Switching rate plans pays by itself.",
          `ComEd's Time-of-Day delivery rate is cheaper at night. Switching to it saves ${money(h.rate_alone)} a year ` +
          `for the ${driver} driver, even when charging as soon as the car is plugged in.`],
        [pct(h.plan / h.hindsight), "SPARC comes close to perfect.",
          `Its plan saves ${pct(h.plan / h.hindsight)} of what you would save if you knew every price in advance. ` +
          "Timers, price limits and live reactions to prices all did worse or the same."],
        [`${pct(sharper)} better, ${gap >= 0.5 ? `${money(gap)} less` : "no more saved"}`, "Better forecasts did not save more money.",
          `The most accurate AI model, ${accurate.model}, predicted prices ${pct(sharper)} better than the day-ahead price, ` +
          `yet ${gap >= 0.5 ? `saved ${money(gap)} a year less` : "saved no more"}. Planning only needs to spot the ` +
          "cheapest hours, and the day-ahead price already does that well."],
        [`+${money(h.buffer_vs_plan)}`, "Using the battery across nights adds more.",
          "A 123 kWh battery doesn't need a full refill every night. Buying extra on cheap nights and less on expensive " +
          `ones adds ${money(h.buffer_vs_plan)} a year to SPARC's plan` +
          (h.buffer_vs_plan < 20 ? ". Driving 80 kWh a day leaves little room in the battery for this." : ".")],
        night && late ? [`${money(night.plan)} vs ${money(late.plan)}`, "Plug in early.",
          `Plugged in at 8 p.m., the plan saves ${money(night.plan)} a year over charging right away on the same rate. ` +
          `Plugged in at 11 p.m., only ${money(late.plan)}: the expensive evening hours are already over.`] : null,
      ].filter(Boolean);
      fill("facts", facts.map(([big, title, text]) => el("article", { class: "fact" },
        el("span", { class: "big" }, big), el("h3", {}, title), el("p", {}, text))));
    }
    draw();
  }

  // ---------- accuracy.html: RMS error only ----------

  function accuracy() {
    const rows = [...DATA.accuracy].sort((a, b) => a.rms_error - b.rms_error);
    const kind = { "Published day-ahead": "plan", "Foundation model": "ai", "Trained by SPARC": "", "Simple baseline": "ref" };
    const worst = Math.max(...rows.map((r) => r.rms_error));
    fill("rms-bars", barRows(rows.map((r) => ({
      name: r.model, small: r.family, value: r.rms_error, kind: kind[r.family],
      text: `${cents(r.rms_error)}${r.family === "Published day-ahead" ? "" : `  (${r.rms_error < r.day_ahead_rms ? minus : "+"}${
        Math.abs(Math.round((r.rms_error / r.day_ahead_rms - 1) * 100))}%)`}`,
    })), worst));
    const top = rows[0];
    const ahead = rows.find((r) => r.family === "Published day-ahead");
    const beat = rows.filter((r) => r.rms_error < r.day_ahead_rms && r.family !== "Published day-ahead").length;
    fill("rms-summary", `${beat} of the ${rows.length - 1} other forecasts miss by less than the day-ahead price's `,
      el("b", {}, cents(ahead.rms_error)), ". The best, ", el("b", {}, top.model), `, misses by ${cents(top.rms_error)}: `,
      `${pct(1 - top.rms_error / top.day_ahead_rms)} less.`);
    fill("rms-table", rows.map((r) => el("tr", {},
      el("td", {}, r.model), el("td", {}, r.family), el("td", { class: "num" }, r.rms_error.toFixed(3)),
      el("td", { class: "num" }, r.day_ahead_rms.toFixed(3)),
      el("td", { class: "num" }, r.family === "Published day-ahead" ? "—"
        : `${r.rms_error < r.day_ahead_rms ? minus : "+"}${Math.abs((r.rms_error / r.day_ahead_rms - 1) * 100).toFixed(1)}%`))));
  }

  // ---------- how-it-works.html: costs, savings and the simulation ----------

  function hourName(hour) {
    if (hour === 0 || hour === 24) return "12 a.m.";
    if (hour === 12) return "noon";
    return hour < 12 ? `${hour} a.m.` : `${hour - 12} p.m.`;
  }

  function strip(bands) {
    const parts = bands.map(([first, rate], i) => [first, i + 1 < bands.length ? bands[i + 1][0] : 24, rate]);
    const level = (rate) => (rate < 3.5 ? "rate-low" : rate > 8 ? "rate-high" : "rate-mid");
    return el("div", {},
      el("div", { class: "strip-row" }, parts.map(([from, to, rate]) => el("span", {
        class: level(rate), style: `flex: ${to - from} 1 0`, title: `${hourName(from)} to ${hourName(to)}: ${cents(rate, 3)} per kWh`,
      }, cents(rate, 2)))),
      el("div", { class: "strip-axis" }, ["12 a.m.", "6 a.m.", "noon", "6 p.m.", "12 a.m."].map((t) => el("span", {}, t))));
  }

  function how() {
    if (!EXAMPLE) return;
    const d = EXAMPLE.driver;
    const rates = EXAMPLE.delivery_rates;
    fill("rate-standard", strip(rates.standard));
    fill("rate-tod", strip(rates.tod));
    fill("standard-rate", cents(rates.standard[0][1], 3));
    const minutes = Math.round((d.grid_kwh / d.power_kw) * 60);
    fill("grid-kwh", `${d.grid_kwh.toFixed(1)} kWh`);
    fill("battery-kwh", `${d.battery_kwh} kWh`);
    fill("loss", pct(1 - d.efficiency));
    fill("power", `${d.power_kw} kW`);
    fill("slot-kwh", `${d.slot_kwh.toFixed(2)} kWh`);
    fill("charge-time", `${Math.floor(minutes / 60)} hour${minutes >= 120 ? "s" : ""} ${minutes % 60} minutes`);

    const night = new Date(`${EXAMPLE.night}T12:00:00Z`).toLocaleDateString("en-US",
      { weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
    fill("example-night", night);
    const cost = (kwh, price, rate) => (kwh * (price + rate)) / 100;
    fill("example-rows", EXAMPLE.hours.map((h) => {
      const classes = [h.default_kwh > 0 ? "charging-default" : "", h.plan_kwh > 0 ? "charging-plan" : ""].join(" ").trim();
      const cell = (kwh, rate, kind) => [
        el("td", { class: `num ${kind}` }, kwh > 0 ? kwh.toFixed(2) : "–"),
        el("td", { class: `num ${kind}` }, cents(rate, 2)),
        el("td", { class: `num ${kind}` }, kwh > 0 ? money2(cost(kwh, h.price, rate)) : "–"),
      ];
      return el("tr", { class: classes || null },
        el("td", {}, h.label), el("td", { class: "num" }, cents(h.price, 1)), el("td", { class: "num" }, cents(h.day_ahead, 1)),
        cell(h.default_kwh, h.standard, "default"), cell(h.plan_kwh, h.tod, "plan"));
    }));
    const sample = EXAMPLE.hours.find((h) => h.default_kwh > 0);
    if (sample) {
      fill("example-formula", `Each cost is kWh × (ComEd price + delivery). For ${sample.label} on the default setup: `,
        `${sample.default_kwh.toFixed(2)} kWh × (${cents(sample.price, 1)} + ${cents(sample.standard, 3)}) = `,
        el("b", {}, money2(cost(sample.default_kwh, sample.price, sample.standard))), ".");
    }
    const def = EXAMPLE.default;
    const plan = EXAMPLE.plan;
    fill("example-total-default", money2(def.cost));
    fill("example-total-plan", money2(plan.cost));
    fill("example-split-default", `${money2(def.supply)} for the electricity + ${money2(def.delivery)} for delivery = ${money2(def.cost)}`);
    fill("example-split-plan", `${money2(plan.supply)} for the electricity + ${money2(plan.delivery)} for delivery = ${money2(plan.cost)}`);
    fill("example-saving", money2(def.cost - plan.cost));

    const chosen = EXAMPLE.hours.filter((h) => h.plan_kwh > 0);
    const cheapest = EXAMPLE.hours.reduce((a, b) => (b.price + b.tod < a.price + a.tod ? b : a));
    const first = chosen[0];
    fill("example-why", chosen.includes(cheapest)
      ? `SPARC charged ${first.label.split(" to ")[0]} onward because the day-ahead prices said those hours were cheapest, and they were.`
      : `SPARC charged from ${first.label.split(" to ")[0]} because the day-ahead prices said those hours were cheapest ` +
        `(${cents(first.day_ahead, 1)}). The real cheapest hour turned out to be ${cheapest.label} ` +
        `(${cents(cheapest.price, 1)}). A plan made the evening before can't know every price, which is why perfect ` +
        "hindsight, which does, saves a little more.");

    const p = EXAMPLE.period;
    fill("year-nights", p.nights.toLocaleString("en-US"));
    fill("year-default", money2(p.default_mean));
    fill("year-plan", money2(p.plan_mean));
    fill("year-gap", money2(p.default_mean - p.plan_mean));
    fill("year-saving", money(p.saved_per_year));
    fill("year-share", pct(1 - p.plan_mean / p.default_mean));
    fill("year-hindsight", money((p.default_mean - p.hindsight_mean) * 365));
    for (const node of document.querySelectorAll("[data-nights]")) node.textContent = p.nights.toLocaleString("en-US");
  }

  const page = document.body.dataset.page;
  if (page === "results") results();
  else if (page === "accuracy") accuracy();
  else if (page === "how") how();
})();
