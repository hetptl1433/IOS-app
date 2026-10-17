"use strict";
// The home page: pick AI price forecasters and nights, then one button downloads what is missing and runs the test.

const $ = (selector) => document.querySelector(selector);
const DRIVERS = ["Average", "Moderate", "Heavy"];
const DECISIONS_PER_NIGHT = 17.2; // the study: 9,528 forecasts over 554 nights
const FAST = 30; // seconds per 1,000 forecasts
const minus = "−";

const state = {
  info: null, // GET /api/ai
  picked: new Set(remembered("sparc.picked", [])),
  period: remembered("sparc.period", "study"),
  driver: remembered("sparc.driver", "Average"),
  job: null,
  result: null,
};

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

function remembered(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function remember(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // A private window: the choice is simply not kept.
  }
}

const money = (v) => (v < -0.5 ? minus : "") + "$" + Math.round(Math.abs(v)).toLocaleString("en-US");
const signedMoney = (v) => (v >= 0 ? "+" : minus) + "$" + Math.round(Math.abs(v)).toLocaleString("en-US");
const cents = (v) => `${v.toFixed(2)}¢`;
const share = (v) => `${Math.round(Math.abs(v) * 100)}%`;
const count = (v) => Math.round(v).toLocaleString("en-US");
const size = (mb) => (mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${count(mb)} MB`);

function day(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US",
    { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

function when(iso) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function duration(seconds) {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} seconds`;
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)} minutes`;
  return `${(seconds / 3600).toFixed(1)} hours`;
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-Sparc"] = "1";
  if (body) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Something went wrong (${response.status}).`);
  return data;
}

function showError(text) {
  $("#error").textContent = text || "";
  $("#error").hidden = !text;
}

const busy = () => Boolean(state.job && state.job.state === "running");
const models = () => (state.info ? state.info.models : []);
const pickedModels = () => models().filter((m) => m.id !== "day-ahead" && state.picked.has(m.id));
const period = () => (state.info ? state.info.periods[state.period] : null);

// ---------- step 1: prices ----------

function renderData() {
  const data = state.info.data;
  const line = $("#data-line");
  if (!data.five_minute) {
    line.textContent = "There are no prices on this computer yet. Press Update prices to download them from ComEd " +
      "(about 20 minutes the first time).";
    return;
  }
  const [first, last] = data.five_minute;
  const oldest = data.hourly && data.hourly[0] < first ? data.hourly[0] : first;
  const stale = new Date(`${state.info.today}T00:00:00Z`) - new Date(`${last}T00:00:00Z`) > 2 * 86400000;
  line.replaceChildren("Prices on this computer: ", el("b", {}, `${day(oldest)} to ${day(last)}`),
    oldest < first ? `. ComEd's 5-minute prices start ${day(first)}.` : ".",
    stale ? " Press Update prices to add the newest days." : "");
}

// ---------- step 2: models ----------

function speed(model) {
  const chosen = period();
  const nights = chosen ? chosen.nights : 554;
  return `About ${duration((nights * DECISIONS_PER_NIGHT * model.seconds_per_1000) / 1000)}`;
}

function renderModels() {
  const locked = busy();
  $("#models").replaceChildren(...models().map((m) => {
    const fixed = m.id === "day-ahead";
    const chips = [];
    if (fixed) chips.push(el("span", { class: "chip fixed" }, "Always included"));
    else if (m.builtin) chips.push(el("span", { class: "chip ready" }, "Built in, instant"));
    else if (m.ready) chips.push(el("span", { class: "chip ready" }, "Downloaded"));
    else chips.push(el("span", { class: "chip missing" }, `Needs a download: ${size(m.size_mb)}`));
    if (!m.builtin) chips.push(el("span", { class: "chip" }, speed(m)));
    if (m.uses_day_ahead) chips.push(el("span", { class: "chip" }, "Also sees day-ahead prices"));
    if (!m.in_report && !m.builtin) chips.push(el("span", { class: "chip" }, "New, not in the report"));
    if (m.licence) chips.push(el("span", { class: "chip", title: m.licence }, "Licence: see note"));
    const input = el("input", {
      type: "checkbox", id: `model-${m.id}`, checked: fixed || state.picked.has(m.id), disabled: fixed || locked,
      onchange: (event) => pick(m.id, event.target.checked),
    });
    return el("label", { class: "pick", for: `model-${m.id}`, title: m.licence || null }, input,
      el("span", { class: "card" },
        el("span", { class: "card-name" }, m.name, " ", el("span", { class: "card-maker" }, m.maker)),
        el("span", { class: "card-text" }, m.summary),
        el("span", { class: "chips" }, chips)));
  }));

  const needing = pickedModels().filter((m) => !m.builtin && !m.ready);
  const libraries = pickedModels().some((m) => !m.builtin && !m.installed);
  const total = needing.reduce((sum, m) => sum + m.size_mb, 0);
  $("#download-hint").textContent = needing.length
    ? `The models you picked need ${size(total)}${libraries ? " plus about 1 GB of libraries" : ""}. ` +
      "Run test downloads them by itself, or download them now."
    : pickedModels().some((m) => !m.builtin) ? "Everything you picked is downloaded." : "Built-in models need no download.";
  $("#download").disabled = locked || !needing.length;

  const warning = $("#env-warning");
  warning.hidden = !(libraries && !state.info.environment.can_install);
  warning.textContent = "SPARC is not running in its own Python environment, so it cannot download AI models. " +
    "Close this window and start SPARC by double-clicking start.command (or run ./start.command).";
  for (const id of ["#pick-all", "#pick-fast", "#pick-none"]) $(id).disabled = locked;
}

function pick(id, on) {
  if (on) state.picked.add(id);
  else state.picked.delete(id);
  remember("sparc.picked", [...state.picked]);
  render();
}

function pickSet(ids) {
  state.picked = new Set(ids);
  remember("sparc.picked", ids);
  render();
}

// ---------- step 3: nights ----------

const PERIOD_NOTES = {
  study: "Recommended. The same nights as the report, so results compare directly.",
  year: "The most recent year of prices.",
  all: "Every night since 5-minute prices began. Slow: hours for the big models.",
};

function renderPeriods() {
  const options = state.info.periods;
  const box = $("#periods");
  if (!Object.keys(options).length) {
    box.replaceChildren(el("p", { class: "hint" }, "Not enough prices yet. Press Update prices first."));
    return;
  }
  if (!options[state.period]) state.period = options.study ? "study" : Object.keys(options)[0];
  box.replaceChildren(...Object.entries(options).map(([key, option]) => el("label", { class: "pick", for: `period-${key}` },
    el("input", {
      type: "radio", name: "period", id: `period-${key}`, value: key, checked: key === state.period, disabled: busy(),
      onchange: () => {
        state.period = key;
        remember("sparc.period", key);
        render();
      },
    }),
    el("span", { class: "card" },
      el("span", { class: "card-name" }, option.label),
      el("span", { class: "card-text" }, `${day(option.first)} to ${day(option.last)} · ${count(option.nights)} nights`),
      el("span", { class: "card-text" }, PERIOD_NOTES[key] || "")))));
}

// ---------- run ----------

function estimate() {
  const chosen = period();
  const picked = pickedModels();
  if (!chosen || !picked.length) return "";
  let seconds = 15 + chosen.nights * 0.04; // working out the decisions and simulating the nights
  for (const m of picked) seconds += (chosen.nights * DECISIONS_PER_NIGHT * m.seconds_per_1000) / 1000;
  const download = picked.filter((m) => !m.builtin && !m.ready).reduce((sum, m) => sum + m.size_mb, 0);
  return `About ${duration(seconds)} on this computer${download ? `, after downloading ${size(download)}` : ""}. ` +
    "A model already tested on these nights reuses its saved forecasts, so running it again takes seconds.";
}

function renderRun() {
  const picked = pickedModels();
  const run = $("#run");
  run.disabled = busy() || !picked.length || !period();
  $("#estimate").textContent = busy() ? "A job is running. Wait for it to finish, or cancel it."
    : !picked.length ? "Pick at least one model in step 2." : estimate();
  $("#update").disabled = busy();
}

async function startJob(path, body) {
  showError("");
  try {
    state.job = await api("POST", path, body);
  } catch (error) {
    showError(error.message);
    return;
  }
  render();
  poll();
}

let timer = null;
function poll() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    try {
      state.job = await api("GET", `/api/jobs/${state.job.id}`);
    } catch (error) {
      showError(error.message);
      return;
    }
    renderJob();
    if (state.job.state === "running") poll();
    else await finished(state.job);
  }, 1000);
}

async function finished(job) {
  if (job.state === "failed") showError(job.error || "That did not work.");
  else if (job.state === "cancelled") showError("Cancelled. Forecasts made so far are kept, so the next run picks up from there.");
  if (job.state === "done" && job.kind === "test" && job.result && job.result.test) await openTest(job.result.test, true);
  await refresh();
}

function renderJob() {
  const job = state.job;
  $("#job").hidden = !busy();
  if (!busy()) return;
  $("#job-title").textContent = job.title;
  $("#job-message").textContent = job.cancelling ? "Stopping…" : job.message;
  const known = job.total > 0;
  $("#meter").classList.toggle("busy", !known);
  $("#meter-fill").style.width = known ? `${Math.min(100, (100 * job.done) / job.total)}%` : "";
  $("#cancel").disabled = job.cancelling;
}

// ---------- results ----------

async function openTest(id, scroll) {
  try {
    state.result = await api("GET", `/api/ai/tests/${encodeURIComponent(id)}`);
  } catch (error) {
    showError(error.message);
    return;
  }
  renderResult();
  if (scroll) {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    $("#result").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }
}

function compared(gap) {
  if (!gap || Math.abs(gap.per_year) <= 2 * gap.error_per_year || Math.abs(gap.per_year) < 0.5) return "same";
  return gap.per_year > 0 ? "better" : "worse";
}

function gapChip(gap) {
  const kind = compared(gap);
  const text = kind === "same" ? "about the same as the plan" : `${signedMoney(gap.per_year)} a year vs the plan`;
  return el("span", { class: `gap ${kind}`, title: gap ? `± ${money(gap.error_per_year)} (one standard error)` : null }, text);
}

function accuracyChip(change) {
  return el("span", { class: `gap ${change < 0 ? "better" : "worse"}` },
    `${share(change)} ${change < 0 ? "more" : "less"} accurate`);
}

function barRow(name, fraction, value, kind, chip) {
  const width = `${Math.max(2, Math.min(100, fraction * 100)) * 0.55}%`; // room for the value; the chip wraps if it must
  return el("div", { class: "bar-row" },
    el("span", { class: "bar-name" }, name),
    el("span", { class: "bar-track" }, el("span", { class: `bar-fill ${kind}`, style: `width: ${width}` }),
      el("span", { class: "bar-value" }, value), chip));
}

function numbersTable(result) {
  const drivers = result.drivers.map((d) => d.name);
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, "Model"), el("th", { class: "num" }, "RMS error (¢/kWh)"),
      el("th", { class: "num" }, "vs day-ahead"), drivers.map((d) => el("th", { class: "num" }, `${d}: saved a year`)))),
    el("tbody", {}, result.rows.map((row) => el("tr", {},
      el("td", {}, row.id === "day-ahead" ? `${row.name} (SPARC's plan)` : row.name),
      el("td", { class: "num" }, row.rmse.toFixed(3)),
      el("td", { class: "num" }, row.id === "day-ahead" ? "—" : `${row.rmse_change < 0 ? minus : "+"}${(Math.abs(row.rmse_change) * 100).toFixed(1)}%`),
      drivers.map((d) => {
        const gap = row.vs_plan[d];
        const extra = row.id !== "day-ahead" && gap ? ` (${signedMoney(gap.per_year)} ± ${money(gap.error_per_year)})` : "";
        return el("td", { class: "num" }, row.saved[d] == null ? "—" : money(row.saved[d]) + extra);
      }))),
      el("tr", {}, el("td", {}, "Perfect hindsight (the most possible)"), el("td", { class: "num" }, "—"),
        el("td", { class: "num" }, "—"),
        drivers.map((d) => el("td", { class: "num" }, result.hindsight[d] == null ? "—" : money(result.hindsight[d])))))));
}

function renderResult() {
  const result = state.result;
  const box = $("#result");
  box.hidden = !result;
  if (!result) return;
  const driver = DRIVERS.includes(state.driver) ? state.driver : "Average";
  const rows = result.rows;
  const kwh = (name) => (result.drivers.find((d) => d.name === name) || {}).kwh;

  const worstError = Math.max(...rows.map((r) => r.rmse));
  const accuracy = [...rows].sort((a, b) => a.rmse - b.rmse).map((r) => barRow(
    r.id === "day-ahead" ? "Day-ahead price (what SPARC uses)" : r.name, r.rmse / worstError, cents(r.rmse),
    r.id === "day-ahead" ? "plan" : "", r.id === "day-ahead" ? null : accuracyChip(r.rmse_change)));

  const hindsight = result.hindsight[driver];
  const most = Math.max(hindsight || 0, ...rows.map((r) => r.saved[driver] || 0)) || 1;
  const savings = [...rows].sort((a, b) => (b.saved[driver] ?? -1e9) - (a.saved[driver] ?? -1e9)).map((r) => barRow(
    r.id === "day-ahead" ? "SPARC's plan (day-ahead price)" : r.name, (r.saved[driver] || 0) / most,
    money(r.saved[driver] || 0), r.id === "day-ahead" ? "plan" : "", r.id === "day-ahead" ? null : gapChip(r.vs_plan[driver])));
  if (hindsight != null) {
    savings.push(barRow("Perfect hindsight (impossible in real life)", hindsight / most, money(hindsight), "ref", null));
  }

  box.replaceChildren(
    el("h2", { id: "result-title" }, "Result"),
    el("p", { class: "verdict" }, result.verdict),
    el("p", { class: "meta" }, `${day(result.first)} to ${day(result.last)} · ${count(result.nights[driver] || 0)} nights · ` +
      `${count(result.decisions)} forecasts · ran ${when(result.created)} in ${duration(result.elapsed_s)}`),
    el("div", { class: "seg", role: "group", "aria-label": "Driver" }, el("span", { class: "label" }, "Driver"),
      DRIVERS.map((name) => el("button", {
        type: "button", "aria-pressed": String(name === driver),
        onclick: () => {
          state.driver = name;
          remember("sparc.driver", name);
          renderResult();
        },
      }, `${name} · ${kwh(name)} kWh a night`))),
    el("div", { class: "charts" },
      el("div", { class: "chart" }, el("h3", {}, "How far off the forecasts were"),
        el("p", {}, "The typical miss in cents per kWh, with big misses counting extra (RMS error). Shorter is better."),
        el("div", { class: "bars" }, accuracy)),
      el("div", { class: "chart" }, el("h3", {}, `Money saved in a year: ${driver} driver`),
        el("p", {}, "Against charging as soon as the car is plugged in, on the standard rate. Longer is better. " +
          "\"About the same\" means the gap is too small to tell from chance."),
        el("div", { class: "bars" }, savings))),
    el("details", {}, el("summary", {}, "All the numbers"), numbersTable(result)),
    el("p", { class: "meta" }, "Saved as ", el("code", {}, `${state.info ? state.info.tests_folder : "results/model-tests"}/${result.id}.md`),
      " (and .json). To share it with your team, commit and push those files."));
}

function renderTests() {
  const tests = state.info.tests;
  $("#past-folder").textContent = tests.length
    ? `Saved in ${state.info.tests_folder}. Commit them to share with your team.`
    : "Tests you run appear here. Each one is saved as a file you can commit.";
  $("#tests").replaceChildren(...tests.map((test) => el("li", {}, el("button", { type: "button", onclick: () => openTest(test.id, true) },
    el("span", { class: "when" }, `${when(test.created)} · nights ${day(test.first)} to ${day(test.last)}`),
    el("span", { class: "what" }, test.models.join(", ")),
    el("span", { class: "what" }, test.verdict || "")))));
}

// ---------- page ----------

function render() {
  if (!state.info) return;
  renderData();
  renderPeriods();
  renderModels();
  renderRun();
  renderJob();
  renderTests();
}

async function refresh() {
  try {
    state.info = await api("GET", "/api/ai");
  } catch (error) {
    showError(`${error.message} Is SPARC still running? Start it with start.command.`);
    return;
  }
  const known = new Set(state.info.models.map((m) => m.id));
  state.picked = new Set([...state.picked].filter((id) => known.has(id) && id !== "day-ahead"));
  const running = state.info.job && state.info.job.state === "running";
  if (running && !busy()) {
    state.job = state.info.job;
    poll();
  }
  render();
}

function start() {
  $("#update").addEventListener("click", () => startJob("/api/data/update", {}));
  $("#download").addEventListener("click", () => startJob("/api/ai/install", { models: pickedModels().map((m) => m.id) }));
  $("#run").addEventListener("click", () => startJob("/api/ai/test", { models: pickedModels().map((m) => m.id), period: state.period }));
  $("#cancel").addEventListener("click", async () => {
    try {
      state.job = await api("POST", `/api/jobs/${state.job.id}/cancel`);
      renderJob();
    } catch (error) {
      showError(error.message);
    }
  });
  $("#pick-all").addEventListener("click", () => pickSet(models().filter((m) => m.id !== "day-ahead").map((m) => m.id)));
  $("#pick-fast").addEventListener("click", () => pickSet(models()
    .filter((m) => m.id !== "day-ahead" && (m.builtin || m.seconds_per_1000 <= FAST)).map((m) => m.id)));
  $("#pick-none").addEventListener("click", () => pickSet([]));
  refresh().then(() => {
    if (state.info && state.info.tests.length) openTest(state.info.tests[0].id, false);
  });
}

start();
