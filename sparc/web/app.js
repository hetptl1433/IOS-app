"use strict";

const { el } = Charts;
const TABS = ["data", "run", "results", "models"];
const GROUP_ORDER = ["Baseline", "Timer", "Price cap", "Plan", "Forecast", "Saved model", "Reference"];
const ALWAYS = new Set(["charge-now", "hindsight"]);
const BASELINE = "Charge on plug-in";
const HINDSIGHT = "Perfect hindsight";
const DEFAULT_ACCENT = "var(--series-1)";

const state = {
  server: null, // GET /api/state
  tab: null,
  job: null,
  jobSeen: null, // id of a finished job whose message was dismissed
  runs: new Map(), // id -> full run, loaded on demand
  runId: null,
  tariff: null,
  compare: null, // {profile, a, b}
  form: null, // the Run tab
  formError: null,
  train: null, // the Models tab form
  trainError: null,
  fetch: null, // the Data tab form
  fetchError: null,
};

// ---------- formatting ----------

const minus = "−";
function sign(v, text) {
  return v < 0 ? minus + text : text;
}
function dollars(v, digits = 2) {
  return sign(v, `$${Math.abs(v).toFixed(digits)}`);
}
function perYear(v) {
  return sign(v, `$${Math.round(Math.abs(v)).toLocaleString("en-US")}`);
}
function cents(v, digits = 1) {
  return sign(v, `${Math.abs(v).toFixed(digits)}¢`);
}
function percent(v) {
  return `${Math.round(100 * v)}%`;
}
function share(part, total) {
  const value = (100 * part) / total;
  return value === 100 ? "100%" : `${Math.min(99.9, value).toFixed(1)}%`;
}
function whole(v) {
  return Math.round(v).toLocaleString("en-US");
}
function day(iso) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
function month(key, short = false) {
  const date = new Date(`${key}-15T12:00:00`);
  return date.toLocaleDateString("en-US", short ? { month: "short" } : { month: "short", year: "numeric" });
}
// Month names on an axis, with the year on the first one shown in each year.
function monthTick(key, i, previous) {
  return !previous || previous.slice(0, 4) !== key.slice(0, 4)
    ? `${month(key, true)} ’${key.slice(2, 4)}`
    : month(key, true);
}
function addDays(iso, days) {
  const date = new Date(`${iso}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
function daysBetween(a, b) {
  return Math.round((Date.parse(`${b}T12:00:00Z`) - Date.parse(`${a}T12:00:00Z`)) / 864e5);
}
function stamp(iso) {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// ---------- server ----------

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-Sparc"] = "1";
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

async function refresh() {
  state.server = await api("GET", "/api/state");
  if (!state.form) state.form = initialForm();
  else syncForm();
  if (!state.fetch) state.fetch = initialFetch();
  if (!state.train) state.train = initialTrain();
}

function saved(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(`sparc.${key}`));
    return value ?? fallback;
  } catch {
    return fallback;
  }
}
function remember(key, value) {
  try {
    localStorage.setItem(`sparc.${key}`, JSON.stringify(value));
  } catch {
    /* private window */
  }
}

// ---------- forms ----------

function initialForm() {
  const defaults = state.server.defaults;
  const kept = saved("form", {});
  const form = {
    start: defaults.start,
    end: defaults.end,
    calibrate_days: Number.isInteger(kept.calibrate_days) ? kept.calibrate_days : defaults.calibrate_days,
    tariffs: Array.isArray(kept.tariffs) && kept.tariffs.length ? kept.tariffs : defaults.tariffs,
    profiles: Array.isArray(kept.profiles) && kept.profiles.length ? kept.profiles : defaults.profiles,
    custom: Array.isArray(kept.strategies), // false: every eligible strategy
    strategies: Array.isArray(kept.strategies) ? kept.strategies : defaults.strategies,
  };
  if (!form.custom) form.strategies = eligibleStrategies(form);
  return form;
}

// After new prices or models arrive: keep the user's choices, refresh what follows from the data.
function syncForm() {
  const form = state.form;
  const known = new Set(state.server.choices.map(c => c.id));
  form.strategies = form.strategies.filter(id => known.has(id));
  if (!form.start || !form.end) {
    form.start = state.server.defaults.start;
    form.end = state.server.defaults.end;
  }
  if (!form.custom) form.strategies = eligibleStrategies(form);
}

function keepForm() {
  const form = state.form;
  remember("form", {
    calibrate_days: form.calibrate_days,
    tariffs: form.tariffs,
    profiles: form.profiles,
    strategies: form.custom ? form.strategies : undefined,
  });
}

function blocked(choice, form) {
  if (ALWAYS.has(choice.id)) return "Always included.";
  const learns = choice.id === "live" || choice.group === "Forecast";
  if (learns && form.calibrate_days < state.server.min_calibration) {
    return `Needs at least ${state.server.min_calibration} calibration days.`;
  }
  if (choice.usable_from && form.start && form.start < choice.usable_from) {
    return `Can only be tested on nights from ${day(choice.usable_from)}.`;
  }
  return null;
}

function eligibleStrategies(form) {
  return state.server.choices.filter(c => !ALWAYS.has(c.id) && !blocked(c, form)).map(c => c.id);
}

function initialFetch() {
  const data = state.server.data,
    today = state.server.today;
  return { start: data.empty ? `${Number(today.slice(0, 4)) - 1}-01-01` : addDays(data.last, -1), end: today };
}

function initialTrain() {
  const data = state.server.data;
  if (data.empty) return { kind: "ridge", train_start: "", train_end: "", test_start: "", test_end: "", name: "" };
  const first = addDays(data.first, 1),
    last = addDays(data.last, -1);
  const span = daysBetween(first, last);
  const trainEnd = addDays(first, Math.max(30, Math.round(span * 0.6)));
  return {
    kind: "ridge",
    train_start: first,
    train_end: trainEnd,
    test_start: addDays(trainEnd, 2),
    test_end: last,
    name: "",
  };
}

// ---------- jobs ----------

let polling = null;

async function startJob(path, body, errorKey) {
  state[errorKey] = null;
  try {
    state.job = await api("POST", path, body);
    state.jobSeen = null;
    renderJob();
    poll();
  } catch (error) {
    state[errorKey] = error.message;
  }
  render();
}

function poll() {
  clearTimeout(polling);
  polling = setTimeout(async () => {
    try {
      state.job = await api("GET", `/api/jobs/${state.job.id}`);
    } catch (error) {
      state.job = { ...state.job, state: "failed", error: `Lost contact with the server: ${error.message}` };
    }
    renderJob();
    if (state.job.state === "running") poll();
    else await finished(state.job);
  }, 600);
}

async function finished(job) {
  await refresh();
  if (job.state === "done" && job.kind === "backtest") {
    state.runId = job.result.run;
    state.compare = null;
    go("results");
  } else if (job.state === "done" && job.kind === "train") {
    go("models");
  } else {
    render();
  }
}

function renderJob() {
  const bar = document.getElementById("job");
  const job = state.job;
  if (!job || state.jobSeen === job.id) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  bar.className = `job ${job.state}`;
  const running = job.state === "running";
  const meter = el("div", { class: `meter ${running && !job.total ? "indeterminate" : ""}` }, el("div"));
  meter.firstChild.style.width = running ? (job.total ? `${(100 * job.done) / job.total}%` : "") : "100%";
  let status;
  if (running)
    status = job.cancelling ? "Stopping…" : job.total ? `${job.message}: ${job.done} of ${job.total}` : job.message;
  else if (job.state === "cancelled")
    status = job.kind === "fetch" ? "Prices fetched before stopping are kept." : "Nothing was saved.";
  else if (job.state === "failed") status = job.error;
  else status = doneMessage(job);
  const buttons = running
    ? el("button", { class: "secondary", type: "button", disabled: job.cancelling, onclick: cancelJob }, "Cancel")
    : el(
        "button",
        {
          class: "link",
          type: "button",
          onclick: () => {
            state.jobSeen = job.id;
            renderJob();
          },
        },
        "Dismiss",
      );
  bar.replaceChildren(
    el(
      "div",
      { class: "job-line" },
      el("span", { class: "job-title" }, running ? job.title : `${job.title}: ${job.state}`),
      el("span", { class: "job-message" }, status),
      buttons,
    ),
  );
  if (running) bar.append(meter);
}

function doneMessage(job) {
  const seconds = Math.round(job.finished - job.started);
  if (job.kind === "fetch") {
    const failed = job.result.failed || [];
    return failed.length
      ? `Finished in ${seconds} s. ${failed.length} hourly tables could not be read: ${failed.join(", ")}. ` +
          "Try those days again later."
      : `Finished in ${seconds} s.`;
  }
  return `Finished in ${seconds} s.`;
}

async function cancelJob() {
  try {
    state.job = await api("POST", `/api/jobs/${state.job.id}/cancel`);
  } catch (error) {
    state.job = { ...state.job, error: error.message };
  }
  renderJob();
}

const busy = () => state.job && state.job.state === "running";

// ---------- layout helpers ----------

function card(title, ...children) {
  return el(
    "section",
    { class: "card" },
    title ? el("div", { class: "card-head" }, el("h2", {}, title)) : null,
    ...children,
  );
}
function message(kind, text) {
  return text ? el("div", { class: `message ${kind}`, role: "alert" }, text) : null;
}
function tile(label, value, sub) {
  return el(
    "div",
    { class: "tile" },
    el("span", { class: "label" }, label),
    el("span", { class: "value" }, value),
    sub ? el("span", { class: "sub" }, sub) : null,
  );
}
function field(label, control) {
  return el("label", {}, label, control);
}
function input(attrs, onchange) {
  const node = el("input", attrs);
  node.addEventListener("change", () => onchange(node));
  return node;
}
function segmented(options, current, onpick) {
  return el(
    "div",
    { class: "segmented", role: "group" },
    options.map(([value, label]) =>
      el("button", { type: "button", "aria-pressed": String(value === current), onclick: () => onpick(value) }, label),
    ),
  );
}

// ---------- Data ----------

function renderData(panel) {
  const data = state.server.data;
  if (data.empty) {
    panel.append(
      card("Stored prices", el("p", {}, "No prices yet. Fetch some from ComEd below; two years is a good start.")),
    );
  } else {
    const priced = data.hours - data.unpriced;
    panel.append(
      card(
        "Stored prices",
        el(
          "div",
          { class: "tiles" },
          tile("Days", `${day(data.first)} – ${day(data.last)}`, `${whole(data.hours)} hours`),
          tile(
            "Hours with a billing price",
            share(priced, data.hours),
            `${whole(data.published)} settled, ${whole(priced - data.published)} from 5-minute prices`,
          ),
          tile(
            "Hours with a day-ahead price",
            share(data.day_ahead, data.hours),
            `${whole(data.hours - data.day_ahead)} missing`,
          ),
          tile("Posted prices later revised", whole(data.revised), `of ${whole(data.compared)} hours compared`),
        ),
        coverageChart(data),
      ),
    );
  }
  const form = state.fetch;
  const days = form.start && form.end ? daysBetween(form.start, form.end) + 1 : 0;
  const requests = days > 0 ? Math.ceil(days / 7) + days + 1 : 0;
  const run = () => startJob("/api/fetch", { start: form.start, end: form.end }, "fetchError");
  panel.append(
    card(
      "Fetch from ComEd",
      el(
        "p",
        { class: "note" },
        "5-minute prices come from ComEd's documented API, day-ahead and settled hourly prices ",
        "from its hourly pricing table. Days already stored are skipped, ",
        "so an interrupted fetch picks up where it stopped.",
      ),
      el(
        "div",
        { class: "fields" },
        field(
          "From",
          input({ type: "date", value: form.start, max: state.server.today, "data-key": "fetch-start" }, node => {
            form.start = node.value;
            later();
          }),
        ),
        field(
          "To",
          input({ type: "date", value: form.end, max: state.server.today, "data-key": "fetch-end" }, node => {
            form.end = node.value;
            later();
          }),
        ),
        el(
          "div",
          { class: "actions" },
          el("button", { class: "primary", type: "button", disabled: busy() || days <= 0, onclick: run }, "Fetch"),
          data.empty
            ? null
            : el(
                "button",
                {
                  class: "secondary",
                  type: "button",
                  disabled: busy(),
                  onclick: () => {
                    form.start = addDays(data.last, -1);
                    form.end = state.server.today;
                    run();
                  },
                },
                "Update to today",
              ),
        ),
      ),
      days > 0
        ? el(
            "p",
            { class: "note" },
            `Up to ${whole(requests)} requests at about one a second`,
            requests > 90 ? `: roughly ${Math.ceil((requests * 1.3) / 60)} minutes.` : ".",
          )
        : null,
      message("error", state.fetchError),
      el(
        "p",
        { class: "note small" },
        "Stored in ",
        el("code", {}, `${state.server.folder}/sparc.db`),
        " on this computer. ComEd's terms do not allow redistributing its prices, so keep the folder out of git.",
      ),
    ),
  );
}

function coverageChart(data) {
  const months = data.months;
  const missing = months.map(m => m.hours - m.priced),
    noAhead = months.map(m => m.hours - m.day_ahead);
  const holder = el("div");
  if (!missing.some(Boolean) && !noAhead.some(Boolean)) {
    holder.append(el("p", { class: "note" }, "Every stored hour has a billing price and a day-ahead price."));
    return holder;
  }
  requestAnimationFrame(() =>
    Charts.columns(holder, {
      title: "Hours without a price, by month",
      subtitle:
        "Backtests leave out any night missing one of these prices. " +
        "ComEd's table for the day the clocks fall back cannot be read.",
      legend: [
        { label: "No billing price", color: "var(--series-1)" },
        { label: "No day-ahead price", color: "var(--series-2)" },
      ],
      categories: months.map(m => m.month),
      series: [
        { name: "No billing price", color: "var(--series-1)", values: missing },
        { name: "No day-ahead price", color: "var(--series-2)", values: noAhead },
      ],
      format: v => whole(v),
      label: monthTick,
      tipTitle: key => month(key),
      tipRows: i => [
        { value: whole(missing[i]), name: "hours without a billing price", color: "var(--series-1)" },
        { value: whole(noAhead[i]), name: "hours without a day-ahead price", color: "var(--series-2)" },
        { value: whole(months[i].hours), name: "hours in the month" },
      ],
      table: {
        columns: [
          { label: "Month" },
          { label: "Hours", num: true },
          { label: "No billing price", num: true },
          { label: "No day-ahead price", num: true },
        ],
        rows: months.map((m, i) => [month(m.month), whole(m.hours), whole(missing[i]), whole(noAhead[i])]),
      },
    }),
  );
  return holder;
}

// ---------- Run ----------

function renderRun(panel) {
  const server = state.server,
    form = state.form;
  if (server.data.empty) {
    panel.append(
      card(
        "Backtest",
        el("p", {}, "There are no prices to replay yet. "),
        el("button", { class: "primary", type: "button", onclick: () => go("data") }, "Fetch prices"),
      ),
    );
    return;
  }
  const first = addDays(server.data.first, 1),
    last = addDays(server.data.last, -1);
  const nights = form.start && form.end ? daysBetween(form.start, form.end) + 1 : 0;
  const changed = () => {
    keepForm();
    later();
  };

  panel.append(
    card(
      "Period",
      el(
        "p",
        { class: "note" },
        `Prices are stored for ${day(server.data.first)} to ${day(server.data.last)}. `,
        "A night runs from plug-in on the date shown until leaving the next morning.",
      ),
      el(
        "div",
        { class: "fields" },
        field(
          "First night",
          input({ type: "date", value: form.start, min: first, max: last, "data-key": "start" }, node => {
            form.start = node.value;
            resync();
            changed();
          }),
        ),
        field(
          "Last night",
          input({ type: "date", value: form.end, min: first, max: last, "data-key": "end" }, node => {
            form.end = node.value;
            changed();
          }),
        ),
        field(
          "Calibration days",
          input(
            { type: "number", min: 0, max: 730, step: 1, value: form.calibrate_days, "data-key": "calibrate" },
            node => {
              form.calibrate_days = Math.max(0, Math.round(Number(node.value) || 0));
              resync();
              changed();
            },
          ),
        ),
      ),
      el(
        "p",
        { class: "note small" },
        "Calibration days come before the first night. The live strategy and the forecasts ",
        "learn from them; no strategy is ever scored on them.",
        nights > 0 ? ` ${whole(nights)} nights selected.` : "",
      ),
    ),
  );

  const rates = Object.entries(server.tariffs).map(([key, tariff]) =>
    el(
      "label",
      { class: "check" },
      el("input", {
        type: "checkbox",
        checked: form.tariffs.includes(key),
        "data-key": `tariff-${key}`,
        onchange: event => {
          form.tariffs = event.target.checked ? [...form.tariffs, key] : form.tariffs.filter(t => t !== key);
          changed();
        },
      }),
      el("span", {}, tariff.name, el("span", { class: "note" }, rateText(tariff.bands))),
    ),
  );
  panel.append(
    card(
      "Delivery rate",
      el("div", { class: "groups" }, rates),
      el(
        "p",
        { class: "note small" },
        "Supply is billed at each hour's real-time price on both. ",
        "Flat per-kWh charges are left out: they don't depend on when you charge.",
      ),
    ),
  );

  panel.append(profilesCard(form, changed));
  panel.append(strategiesCard(form, changed));

  const strategies = 2 + form.strategies.length;
  panel.append(
    card(
      null,
      el(
        "div",
        { class: "actions" },
        el(
          "button",
          {
            class: "primary",
            type: "button",
            disabled: busy(),
            onclick: () =>
              startJob(
                "/api/backtest",
                {
                  start: form.start,
                  end: form.end,
                  calibrate_days: form.calibrate_days,
                  tariffs: form.tariffs,
                  strategies: form.strategies,
                  profiles: form.profiles,
                },
                "formError",
              ),
          },
          "Run backtest",
        ),
        el(
          "span",
          { class: "note" },
          `${whole(nights)} nights × ${form.profiles.length} drivers × ${form.tariffs.length} rates × `,
          `${strategies} strategies (with the baseline and perfect hindsight)`,
        ),
      ),
      message("error", state.formError),
    ),
  );
}

function resync() {
  if (!state.form.custom) state.form.strategies = eligibleStrategies(state.form);
}

function rateText(bands) {
  if (bands.length === 1) return `${bands[0][1]}¢/kWh at all hours`;
  const hour = h => (h === 0 ? "midnight" : h === 12 ? "noon" : h < 12 ? `${h} a.m.` : `${h - 12} p.m.`);
  return bands
    .map(([first, rate], i) => {
      const end = i + 1 < bands.length ? bands[i + 1][0] : 24;
      return `${rate}¢ ${hour(first)}–${hour(end % 24)}`;
    })
    .join(", ");
}

function profilesCard(form, changed) {
  const columns = [
    ["name", "Name", "text"],
    ["arrive", "Plug in", "time"],
    ["depart", "Leave", "time"],
    ["battery_kwh", "Energy added (kWh)", "number"],
    ["power_kw", "Charger (kW)", "number"],
    ["efficiency", "Efficiency (%)", "number"],
    ["reserve_min", "Finish early (min)", "number"],
  ];
  const rows = form.profiles.map((profile, index) =>
    el(
      "tr",
      {},
      columns.map(([key, label, type]) => {
        const value = key === "efficiency" ? Math.round(profile[key] * 100) : profile[key];
        const attrs = {
          type,
          value,
          "aria-label": `${label}, driver ${index + 1}`,
          "data-key": `profile-${index}-${key}`,
        };
        if (type === "time") attrs.step = 300;
        if (key === "battery_kwh") Object.assign(attrs, { min: 0.5, max: 150, step: 0.5 });
        if (key === "power_kw") Object.assign(attrs, { min: 1, max: 20, step: 0.1 });
        if (key === "efficiency") Object.assign(attrs, { min: 50, max: 100, step: 1 });
        if (key === "reserve_min") Object.assign(attrs, { min: 0, max: 180, step: 5 });
        if (key === "name") attrs.maxlength = 40;
        return el(
          "td",
          {},
          input(attrs, node => {
            profile[key] =
              type === "number" ? (key === "efficiency" ? Number(node.value) / 100 : Number(node.value)) : node.value;
            changed();
          }),
        );
      }),
      el(
        "td",
        {},
        el(
          "button",
          {
            class: "link danger",
            type: "button",
            disabled: form.profiles.length === 1,
            "aria-label": `Remove ${profile.name}`,
            onclick: () => {
              form.profiles.splice(index, 1);
              changed();
            },
          },
          "Remove",
        ),
      ),
    ),
  );
  return card(
    "Drivers",
    el(
      "p",
      { class: "note" },
      "Each driver plugs in at the same time every day. Leaving at or before the plug-in time means the next morning. ",
      "Energy added is what reaches the battery; the grid supplies that divided by the efficiency.",
    ),
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        { class: "profiles" },
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            columns.map(([, label]) => el("th", {}, label)),
            el("th"),
          ),
        ),
        el("tbody", {}, rows),
      ),
    ),
    el(
      "div",
      { class: "actions" },
      el(
        "button",
        {
          class: "secondary",
          type: "button",
          disabled: form.profiles.length >= state.server.profiles_max,
          onclick: () => {
            form.profiles.push({
              ...form.profiles[form.profiles.length - 1],
              name: `Driver ${form.profiles.length + 1}`,
            });
            changed();
          },
        },
        "Add driver",
      ),
      el(
        "button",
        {
          class: "link",
          type: "button",
          onclick: () => {
            form.profiles = structuredClone(state.server.defaults.profiles);
            changed();
          },
        },
        "Reset to the four examples",
      ),
    ),
  );
}

function strategiesCard(form, changed) {
  const choices = state.server.choices;
  const groups = GROUP_ORDER.map(group => [group, choices.filter(c => c.group === group)]).filter(
    ([, list]) => list.length,
  );
  const toggle = (id, on) => {
    form.custom = true;
    form.strategies = on ? [...new Set([...form.strategies, id])] : form.strategies.filter(s => s !== id);
    changed();
  };
  return card(
    "Strategies",
    el(
      "div",
      { class: "actions" },
      el(
        "button",
        {
          class: "link",
          type: "button",
          onclick: () => {
            form.custom = false;
            resync();
            changed();
          },
        },
        "Select every strategy that can run",
      ),
      el(
        "button",
        {
          class: "link",
          type: "button",
          onclick: () => {
            form.custom = true;
            form.strategies = [];
            changed();
          },
        },
        "Clear",
      ),
    ),
    el(
      "div",
      { class: "groups" },
      groups.map(([group, list]) =>
        el(
          "div",
          { class: "group" },
          el("h4", {}, group),
          state.server.groups[group] ? el("p", { class: "note small" }, state.server.groups[group]) : null,
          list.map(choice => {
            const reason = blocked(choice, form);
            const always = ALWAYS.has(choice.id);
            const checked = always || form.strategies.includes(choice.id);
            return el(
              "label",
              { class: "check" },
              el("input", {
                type: "checkbox",
                checked,
                disabled: always || (reason && !checked),
                "data-key": `strategy-${choice.id}`,
                onchange: event => toggle(choice.id, event.target.checked),
              }),
              el(
                "span",
                {},
                choice.label,
                choice.description ? el("span", { class: "note" }, choice.description) : null,
                reason && !always
                  ? el(
                      "span",
                      { class: "note" },
                      reason,
                      choice.usable_from
                        ? [
                            " ",
                            el(
                              "button",
                              {
                                class: "link",
                                type: "button",
                                onclick: event => {
                                  event.preventDefault();
                                  form.start = choice.usable_from;
                                  if (form.end < form.start) form.end = form.start;
                                  toggle(choice.id, true);
                                },
                              },
                              `Start on ${day(choice.usable_from)}`,
                            ),
                          ]
                        : null,
                    )
                  : null,
              ),
            );
          }),
        ),
      ),
    ),
  );
}

// ---------- Results ----------

async function loadRun(id) {
  if (!state.runs.has(id)) state.runs.set(id, await api("GET", `/api/runs/${id}`));
  return state.runs.get(id);
}

function renderResults(panel) {
  const runs = state.server.runs;
  if (!runs.length) {
    panel.append(
      card(
        "Results",
        el("p", {}, "No backtests yet."),
        el("button", { class: "primary", type: "button", onclick: () => go("run") }, "Set one up"),
      ),
    );
    return;
  }
  if (!runs.some(r => r.id === state.runId)) state.runId = runs[0].id;
  const run = state.runs.get(state.runId);
  if (!run) {
    panel.append(card("Results", el("p", { class: "note" }, "Loading…")));
    loadRun(state.runId).then(render, error => {
      panel.replaceChildren(card("Results", message("error", error.message)));
    });
    return;
  }
  if (!run.tariffs.includes(state.tariff)) state.tariff = run.tariffs[0];
  const tariff = state.tariff;
  const summaries = run.summaries.filter(s => s.delivery === tariff);
  const find = (profile, strategy) => summaries.find(s => s.profile === profile && s.strategy === strategy);
  const winners = run.winners.filter(w => w.tariff === tariff);

  const picker = el(
    "select",
    {
      "aria-label": "Backtest run",
      onchange: event => {
        state.runId = event.target.value;
        state.compare = null;
        render();
      },
    },
    runs.map(r =>
      el(
        "option",
        { value: r.id, selected: r.id === run.id },
        `${stamp(r.created)} · ${day(r.start)} to ${day(r.end)} · ${r.strategies} strategies`,
      ),
    ),
  );
  const config = run.config;
  panel.append(
    card(
      null,
      el(
        "div",
        { class: "card-head" },
        el("h2", {}, "Results"),
        el(
          "div",
          { class: "actions" },
          picker,
          el("a", { href: `/api/runs/${run.id}/nights.csv`, download: "" }, "Every night as CSV"),
          el(
            "button",
            {
              class: "link danger",
              type: "button",
              disabled: busy(),
              onclick: async () => {
                if (!confirm("Delete this backtest run?")) return;
                await api("DELETE", `/api/runs/${run.id}`);
                state.runs.delete(run.id);
                await refresh();
                render();
              },
            },
            "Delete",
          ),
        ),
      ),
      el(
        "p",
        { class: "note" },
        `Nights ${day(config.start)} to ${day(config.end)}`,
        config.calibrate_days ? `, after ${config.calibrate_days} calibration days` : "",
        `. ${run.profiles.length} drivers, ${run.strategies.length} strategies, `,
        `finished in ${Math.round(run.elapsed_s)} s.`,
        Object.keys(run.skipped).length
          ? ` Skipped for missing prices: ${Object.entries(run.skipped)
              .map(([p, n]) => `${p} ${n}`)
              .join(", ")}.`
          : "",
      ),
      run.tariffs.length > 1
        ? el(
            "div",
            { class: "actions" },
            el("span", { class: "note" }, "Delivery rate"),
            segmented(
              run.tariffs.map(t => [t, t]),
              tariff,
              value => {
                state.tariff = value;
                state.compare = null;
                render();
              },
            ),
          )
        : null,
    ),
  );

  panel.append(
    card(
      "Cheapest strategy",
      el(
        "p",
        { class: "note" },
        'Savings are against charging on plug-in, scaled to a year. "Tied" strategies cost within two ',
        "standard errors of the cheapest, comparing the same nights: the difference could be luck.",
      ),
      el(
        "div",
        { class: "winners" },
        winners.map(w => {
          const best = find(w.profile, w.best),
            ideal = find(w.profile, HINDSIGHT);
          return el(
            "div",
            { class: "winner" },
            el("span", { class: "who" }, `${w.profile} · ${run.nights[w.profile]} nights`),
            el("span", { class: "best" }, w.best),
            el(
              "span",
              { class: "figures" },
              `${dollars(w.cost)} a night, saves ${perYear(best.saved_per_year)} a year`,
              best.share !== null
                ? `, ${percent(best.share)} of the most possible (${perYear(ideal.saved_per_year)})`
                : "",
            ),
            el(
              "span",
              { class: "note" },
              w.tied.length ? `Tied: ${w.tied.join(", ")}` : "No strategy is tied with it.",
            ),
          );
        }),
      ),
    ),
  );

  const grid = el("div", { class: "grid-2" });
  panel.append(card("Savings per year by strategy", grid));
  requestAnimationFrame(() => {
    for (const profile of run.profiles) {
      const winner = winners.find(w => w.profile === profile.name);
      const rows = run.strategies
        .filter(s => s !== BASELINE && s !== HINDSIGHT)
        .map(s => find(profile.name, s))
        .filter(Boolean)
        .sort((a, b) => b.saved_per_year - a.saved_per_year);
      const ideal = find(profile.name, HINDSIGHT);
      if (!winner || !ideal) continue;
      const holder = el("div");
      grid.append(holder);
      Charts.barList(holder, {
        title: profile.name,
        subtitle: profile.description,
        legend: [
          { kind: "rect", color: DEFAULT_ACCENT, label: "Cheapest" },
          { kind: "rect", color: "var(--deemph)", label: "Other strategies" },
          { kind: "ref", label: `Perfect hindsight ${perYear(ideal.saved_per_year)}` },
        ],
        rows: rows.map(s => ({
          label: s.strategy,
          value: s.saved_per_year,
          accent: s.strategy === winner.best,
          tag: s.strategy === winner.best ? "cheapest" : winner.tied.includes(s.strategy) ? "tied" : null,
          tip: summaryTip(s),
        })),
        reference: { value: ideal.saved_per_year },
        format: perYear,
        signed: true,
        table: {
          columns: [
            { label: "Strategy" },
            { label: "Saved a year", num: true },
            { label: "Per night", num: true },
            { label: "Of the most possible", num: true },
          ],
          rows: [...rows, ideal].map(s => [
            s.strategy,
            perYear(s.saved_per_year),
            dollars(s.cost),
            s.share === null ? "—" : percent(s.share),
          ]),
        },
      });
    }
  });

  panel.append(compareCard(run, tariff, winners));
  const forecasts = forecastCard(run);
  if (forecasts) panel.append(forecasts);
  panel.append(detailsCard(run, summaries));
}

function summaryTip(s) {
  return [
    { value: perYear(s.saved_per_year), name: "saved a year" },
    { value: dollars(s.cost), name: "a night" },
    { value: s.share === null ? "—" : percent(s.share), name: "of what perfect hindsight saves" },
    { value: dollars(s.worst), name: "most expensive night" },
    { value: `${s.starts.toFixed(1)} / ${s.pauses.toFixed(1)}`, name: "starts / pauses a night" },
    { value: whole(s.risk_kwh_per_year), name: "kWh a year in capacity-risk hours" },
    { value: percent(s.ready), name: "of nights finished on time" },
  ];
}

function compareCard(run, tariff, winners) {
  const strategies = run.strategies.filter(s => s !== HINDSIGHT);
  if (!state.compare || !run.profiles.some(p => p.name === state.compare.profile)) {
    const profile = run.profiles[0].name;
    state.compare = { profile, ...defaultPair(run, tariff, profile, winners) };
  }
  const pick = state.compare;
  const select = (value, options, onpick, label) =>
    el(
      "select",
      { "aria-label": label, onchange: event => onpick(event.target.value) },
      options.map(o => el("option", { value: o, selected: o === value }, o)),
    );
  const series = name => {
    const rows = run.monthly.filter(m => m.profile === pick.profile && m.tariff === tariff && m.strategy === name);
    return new Map(rows.map(m => [m.month, m]));
  };
  const a = series(pick.a),
    b = series(pick.b);
  const months = [...a.keys()].filter(m => b.has(m)).sort();
  const gaps = months.map(m => 100 * (b.get(m).cost - a.get(m).cost));
  const holder = el("div");
  const won = gaps.filter(g => g > 0).length,
    lost = gaps.filter(g => g < 0).length;
  const winner = winners.find(w => w.profile === pick.profile);
  const cost = name =>
    run.summaries.find(s => s.delivery === tariff && s.profile === pick.profile && s.strategy === name).cost;
  const gain = 100 * (cost(pick.b) - cost(pick.a));
  const paired =
    winner && (winner.best === pick.a ? winner.behind[pick.b] : winner.best === pick.b ? winner.behind[pick.a] : null);
  const overall =
    ` Over all ${whole(run.nights[pick.profile])} nights it ${gain >= 0 ? "saved" : "cost"} ` +
    `${cents(Math.abs(gain))} a night` +
    (paired ? ` (± ${cents(paired.standard_error)}, one standard error).` : ".");
  const node = card(
    "Month by month",
    el(
      "div",
      { class: "fields" },
      field(
        "Driver",
        select(
          pick.profile,
          run.profiles.map(p => p.name),
          value => {
            state.compare = { profile: value, ...defaultPair(run, tariff, value, winners) };
            render();
          },
          "Driver",
        ),
      ),
      field(
        "Strategy",
        select(
          pick.a,
          strategies,
          value => {
            pick.a = value;
            render();
          },
          "Strategy",
        ),
      ),
      field(
        "Compared with",
        select(
          pick.b,
          strategies,
          value => {
            pick.b = value;
            render();
          },
          "Compared with",
        ),
      ),
    ),
    pick.a === pick.b
      ? el("p", { class: "note" }, "Choose two different strategies.")
      : el(
          "p",
          { class: "note" },
          `${pick.a} was cheaper in ${won} of ${months.length} months and more expensive in ${lost}.`,
          overall,
        ),
    holder,
  );
  if (pick.a !== pick.b && months.length) {
    requestAnimationFrame(() =>
      Charts.columns(holder, {
        title: `How much ${pick.a} saved over ${pick.b}`,
        subtitle: "Cents per night, by month. Below zero, it cost more.",
        categories: months,
        series: [{ name: "saved", values: gaps }],
        diverging: true,
        positive: "var(--series-1)",
        negative: "var(--negative)",
        legend: [
          { label: `${pick.a} cheaper`, color: "var(--series-1)" },
          { label: `${pick.b} cheaper`, color: "var(--negative)" },
        ],
        format: v => cents(v, Math.abs(v) < 10 && v % 1 ? 1 : 0),
        label: monthTick,
        tipTitle: key => month(key),
        tipRows: i => [
          { value: cents(gaps[i]), name: "saved a night" },
          { value: dollars(a.get(months[i]).cost), name: pick.a },
          { value: dollars(b.get(months[i]).cost), name: pick.b },
          { value: whole(a.get(months[i]).nights), name: "nights" },
        ],
        table: {
          columns: [
            { label: "Month" },
            { label: pick.a, num: true },
            { label: pick.b, num: true },
            { label: "Saved a night", num: true },
            { label: "Nights", num: true },
          ],
          rows: months.map((m, i) => [
            month(m),
            dollars(a.get(m).cost),
            dollars(b.get(m).cost),
            cents(gaps[i]),
            whole(a.get(m).nights),
          ]),
        },
      }),
    );
  }
  return node;
}

function defaultPair(run, tariff, profile, winners) {
  const winner = winners.find(w => w.profile === profile);
  const costs = run.summaries
    .filter(s => s.delivery === tariff && s.profile === profile && s.strategy !== HINDSIGHT)
    .sort((x, y) => x.cost - y.cost);
  const a = winner ? winner.best : costs[0].strategy;
  const b = (costs.find(s => s.strategy !== a) || costs[0]).strategy;
  return { a, b };
}

function forecastCard(run) {
  const entries = Object.entries(run.forecasts || {}).filter(([, f]) => f.test && f.test.samples);
  if (!entries.length) return null;
  const rows = entries.map(([label, f]) => [
    label,
    cents(f.test.mae, 2),
    cents(f.test.day_ahead_mae, 2),
    cents(f.test.rmse, 2),
    cents(f.test.day_ahead_rmse, 2),
    whole(f.test.samples),
  ]);
  const charts = el("div", { class: "grid-2" });
  const node = card(
    "Forecast accuracy",
    el(
      "p",
      { class: "note" },
      "Error in forecasting each hour's settled price, on nights the model never trained on. ",
      "Day-ahead is the error of simply using the day-ahead price. ",
      "A forecast can win on these numbers and still not save money.",
    ),
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        {},
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            ["Strategy", "Mean error", "Day-ahead", "RMS error", "Day-ahead", "Hours forecast"].map((h, i) =>
              el("th", { class: i ? "num" : null }, h),
            ),
          ),
        ),
        el(
          "tbody",
          {},
          rows.map(r =>
            el(
              "tr",
              {},
              r.map((c, i) => el("td", { class: i ? "num" : null }, c)),
            ),
          ),
        ),
      ),
    ),
    charts,
  );
  const drawn = entries
    .filter(([, f]) => f.months && f.months.filter(m => m.samples).length > 1)
    .map(([label, f]) => [label, f, charts.appendChild(el("div"))]);
  requestAnimationFrame(() => {
    for (const [label, f, holder] of drawn) {
      const months = f.months.filter(m => m.samples);
      Charts.lines(holder, {
        title: label,
        subtitle: "Mean absolute error by month, cents/kWh. Each month's model learned from the months before it.",
        legend: [
          { kind: "line", color: "var(--series-1)", label: "Forecast" },
          { kind: "line", color: "var(--series-2)", label: "Day-ahead price" },
        ],
        categories: months.map(m => m.month),
        series: [
          { name: "Forecast", color: "var(--series-1)", values: months.map(m => m.mae) },
          { name: "Day-ahead price", color: "var(--series-2)", values: months.map(m => m.day_ahead_mae) },
        ],
        format: v => cents(v, 1),
        label: monthTick,
        tipTitle: key => month(key),
        table: {
          columns: [
            { label: "Month" },
            { label: "Forecast mean error", num: true },
            { label: "Day-ahead", num: true },
            { label: "Forecast RMS error", num: true },
            { label: "Day-ahead", num: true },
            { label: "Hours", num: true },
          ],
          rows: months.map(m => [
            month(m.month),
            cents(m.mae, 2),
            cents(m.day_ahead_mae, 2),
            cents(m.rmse, 2),
            cents(m.day_ahead_rmse, 2),
            whole(m.samples),
          ]),
        },
      });
    }
  });
  return node;
}

function detailsCard(run, summaries) {
  const header = [
    "Strategy",
    "Per night",
    "Saved a year",
    "Of max",
    "Worst night",
    "Starts",
    "Pauses",
    "Risk kWh/yr",
    "On time",
  ];
  return card(
    "All numbers",
    el(
      "p",
      { class: "note" },
      '"Of max" is the share of perfect hindsight\'s savings. Risk kWh/yr is energy charged on summer weekday ',
      "afternoons, when next year's capacity charge is set; a few hours of it can cost more than a year of savings.",
    ),
    run.profiles.map((profile, i) => {
      const rows = summaries.filter(s => s.profile === profile.name).sort((a, b) => a.cost - b.cost);
      return el(
        "details",
        { open: i === 0 },
        el("summary", {}, `${profile.name}: ${profile.description}`),
        el(
          "div",
          { class: "table-wrap" },
          el(
            "table",
            {},
            el(
              "thead",
              {},
              el(
                "tr",
                {},
                header.map((h, j) => el("th", { class: j ? "num" : null }, h)),
              ),
            ),
            el(
              "tbody",
              {},
              rows.map((s, j) =>
                el(
                  "tr",
                  { class: j === 0 && s.strategy !== HINDSIGHT ? "best" : null },
                  [
                    s.strategy,
                    dollars(s.cost),
                    perYear(s.saved_per_year),
                    s.share === null ? "—" : percent(s.share),
                    dollars(s.worst),
                    s.starts.toFixed(2),
                    s.pauses.toFixed(2),
                    whole(s.risk_kwh_per_year),
                    percent(s.ready),
                  ].map((c, k) => el("td", { class: k ? "num" : null }, c)),
                ),
              ),
            ),
          ),
        ),
      );
    }),
  );
}

// ---------- Models ----------

function renderModels(panel) {
  const server = state.server,
    form = state.train;
  const set = key => node => {
    form[key] = node.value;
  };
  panel.append(
    card(
      "Train a forecast",
      el(
        "p",
        { class: "note" },
        "A forecast predicts how far each hour's settled real-time price lands from its day-ahead price, from ",
        "what is known when the plan is made: day-ahead prices already out, the last day of 5-minute prices, ",
        "the hour, the weekday and the season. ",
        'Backtests can also refit one every month on their own ("refit monthly" strategies); ',
        "a saved model is one fixed fit you can inspect.",
      ),
      server.data.empty
        ? el("p", {}, "Fetch prices first.")
        : el(
            "div",
            { class: "fields" },
            field(
              "Model",
              el(
                "select",
                {
                  onchange: event => {
                    form.kind = event.target.value;
                  },
                },
                el("option", { value: "ridge", selected: form.kind === "ridge" }, "Ridge regression"),
                el(
                  "option",
                  { value: "lightgbm", selected: form.kind === "lightgbm", disabled: !server.lightgbm },
                  server.lightgbm ? "LightGBM" : "LightGBM (pip install lightgbm)",
                ),
              ),
            ),
            field("Train from", input({ type: "date", value: form.train_start }, set("train_start"))),
            field("Train to", input({ type: "date", value: form.train_end }, set("train_end"))),
            field("Test from", input({ type: "date", value: form.test_start }, set("test_start"))),
            field("Test to", input({ type: "date", value: form.test_end }, set("test_end"))),
            field("Name (optional)", input({ type: "text", value: form.name, maxlength: 60 }, set("name"))),
            el(
              "div",
              { class: "actions" },
              el(
                "button",
                {
                  class: "primary",
                  type: "button",
                  disabled: busy(),
                  onclick: () => startJob("/api/train", { ...form }, "trainError"),
                },
                "Train",
              ),
            ),
          ),
      el(
        "p",
        { class: "note small" },
        "The test period must start at least two days after training ends, and backtests only use a model ",
        "on nights after that, so it is never scored on what it learned from.",
      ),
      message("error", state.trainError),
    ),
  );

  if (!server.models.length) {
    panel.append(card("Saved models", el("p", { class: "note" }, "None yet.")));
    return;
  }
  server.models.forEach((model, i) => panel.append(modelCard(model, i === 0)));
}

function modelCard(model, newest) {
  const metrics = ["train", "test"].filter(p => model.metrics[p] && model.metrics[p].samples);
  const holder = el("div");
  const effects = el("details", { open: newest }, el("summary", {}, "What moves the forecast"), holder);
  const node = card(
    model.name,
    el(
      "p",
      { class: "note" },
      `${model.kind === "ridge" ? "Ridge regression" : "LightGBM"}, trained on nights ${day(model.train[0])} to `,
      `${day(model.train[1])}. Usable in backtests from ${day(model.usable_from)}.`,
    ),
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        {},
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            ["Nights", "Mean error", "Day-ahead", "Change", "RMS error", "Day-ahead", "Change", "Hours"].map((h, i) =>
              el("th", { class: i ? "num" : null }, h),
            ),
          ),
        ),
        el(
          "tbody",
          {},
          metrics.map(period => {
            const m = model.metrics[period];
            const change = (x, base) => `${x < base ? minus : "+"}${Math.abs(Math.round((100 * (x - base)) / base))}%`;
            return el(
              "tr",
              {},
              [
                period === "train" ? "Training" : `Test, ${day(model.test[0])} to ${day(model.test[1])}`,
                cents(m.mae, 2),
                cents(m.day_ahead_mae, 2),
                change(m.mae, m.day_ahead_mae),
                cents(m.rmse, 2),
                cents(m.day_ahead_rmse, 2),
                change(m.rmse, m.day_ahead_rmse),
                whole(m.samples),
              ].map((c, i) => el("td", { class: i ? "num" : null }, c)),
            );
          }),
        ),
      ),
    ),
    effects,
    el(
      "div",
      { class: "actions" },
      el(
        "button",
        {
          class: "secondary",
          type: "button",
          onclick: () => {
            const form = state.form;
            if (!form.start || form.start < model.usable_from) form.start = model.usable_from;
            if (form.end < form.start) form.end = form.start;
            form.custom = true;
            form.strategies = [...new Set([...form.strategies, `model:${model.id}`])];
            keepForm();
            go("run");
          },
        },
        "Backtest it",
      ),
      el(
        "button",
        {
          class: "link danger",
          type: "button",
          disabled: busy(),
          onclick: async () => {
            if (!confirm(`Delete ${model.name}?`)) return;
            await api("DELETE", `/api/models/${model.id}`);
            await refresh();
            render();
          },
        },
        "Delete",
      ),
    ),
  );
  const inputs = Object.entries(model.effects);
  requestAnimationFrame(() =>
    Charts.barList(
      holder,
      model.kind === "ridge"
        ? {
            title: "Effect of each input",
            subtitle:
              "Change in the forecast gap, in cents, for a typical (one standard deviation) change in each input.",
            rows: inputs
              .sort((x, y) => Math.abs(y[1]) - Math.abs(x[1]))
              .map(([name, v]) => ({
                label: featureName(name),
                value: v,
                accent: true,
                tip: [{ value: cents(v, 2), name: "per standard deviation" }],
              })),
            format: v => cents(v, 2),
            signed: true,
            table: {
              columns: [{ label: "Input" }, { label: "Effect", num: true }],
              rows: inputs.map(([n, v]) => [featureName(n), cents(v, 2)]),
            },
          }
        : {
            title: "What the trees split on",
            subtitle: "Share of the total gain from each input.",
            rows: inputs
              .sort((x, y) => y[1] - x[1])
              .map(([name, v]) => ({
                label: featureName(name),
                value: v,
                accent: true,
                tip: [{ value: percent(v), name: "of the gain" }],
              })),
            format: percent,
            table: {
              columns: [{ label: "Input" }, { label: "Share of gain", num: true }],
              rows: inputs.map(([n, v]) => [featureName(n), percent(v)]),
            },
          },
    ),
  );
  return node;
}

const FEATURE_NAMES = {
  day_ahead: "Day-ahead price",
  day_ahead_known: "Day-ahead price is out",
  hours_ahead: "Hours ahead",
  hour_sin: "Hour of day (sine)",
  hour_cos: "Hour of day (cosine)",
  weekend: "Weekend",
  season_sin: "Season (sine)",
  season_cos: "Season (cosine)",
  gap_latest: "Latest 5-minute gap",
  gap_1h: "Gap over the last hour",
  gap_3h: "Gap over 3 hours",
  gap_24h: "Gap over 24 hours",
};
function featureName(name) {
  return FEATURE_NAMES[name] || name;
}

// ---------- shell ----------

// Tabs live in the URL hash, so reloading keeps the tab and Back returns to the previous one.
function go(tab) {
  state.tab = tab;
  if (location.hash === `#${tab}`) render();
  else location.hash = tab; // renders on hashchange
}

let rendering = null;
function later() {
  cancelAnimationFrame(rendering);
  rendering = requestAnimationFrame(render);
}

function render() {
  cancelAnimationFrame(rendering);
  Charts.hideTip();
  const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
  for (const tab of TABS) {
    const button = document.querySelector(`[data-tab="${tab}"]`);
    button.setAttribute("aria-selected", String(tab === state.tab));
    document.getElementById(`tab-${tab}`).hidden = tab !== state.tab;
  }
  const panel = document.getElementById(`tab-${state.tab}`);
  panel.replaceChildren();
  ({ data: renderData, run: renderRun, results: renderResults, models: renderModels })[state.tab](panel);
  if (focused) {
    const again = panel.querySelector(`[data-key="${CSS.escape(focused)}"]`);
    if (again) again.focus({ preventScroll: true });
  }
}

async function start() {
  for (const tab of TABS) document.querySelector(`[data-tab="${tab}"]`).addEventListener("click", () => go(tab));
  try {
    await refresh();
  } catch (error) {
    document
      .getElementById("tab-data")
      .replaceChildren(message("error", `Could not reach the SPARC server: ${error.message}`));
    return;
  }
  const wanted = location.hash.slice(1);
  state.tab = TABS.includes(wanted)
    ? wanted
    : state.server.data.empty
      ? "data"
      : state.server.runs.length
        ? "results"
        : "run";
  if (state.server.job && state.server.job.state === "running") {
    state.job = state.server.job;
    poll();
  }
  if (location.hash !== `#${state.tab}`) history.replaceState(null, "", `#${state.tab}`);
  addEventListener("hashchange", () => {
    const tab = location.hash.slice(1);
    if (TABS.includes(tab)) {
      state.tab = tab;
      render();
    }
  });
  renderJob();
  render();
  let resizing = null;
  addEventListener("resize", () => {
    clearTimeout(resizing);
    resizing = setTimeout(() => {
      if (state.tab !== "run") render();
    }, 200);
  });
}

start();
