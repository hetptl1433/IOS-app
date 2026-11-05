"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Page = require("./dist/norway-local.js");
const Engine = require("./dist/norway-local-engine.js");
const start = Date.parse("2019-07-01T00:00:00Z") / 1000;
function fixtureData() {
  return {
    dataSource: "Norway Dataset1", timezone: "Europe/Oslo", currency: "NOK",
    coverage: { firstDate: "2018-02-06", lastDate: "2019-07-01" },
    areas: [{ id: "NO1", label: "NO1 · Eastern Norway" }],
    locations: [{ id: "OSL_T", label: "Oslo · OSL_T" }],
    provenance: { priceCoverage: "Actual hourly Norwegian prices from 2019-07-01.", costScope: "Spot energy only; no VAT, grid fees, taxes or subsidies.", areaMapping: { OSL_T: "NO1", ASK: "NO1", BER: "NO5" }, publishedAssumption: { localTime: "13:00 Europe/Oslo on the previous local date", description: "Assumed; not observed publication times." }, attribution: "Price data authors. CC BY 4.0. Normalized for SPARC.", chargingAttribution: "Charging report authors. Cleaned for SPARC.", sources: [{ label: "Price source", url: "https://example.com/prices" }, { label: "Invalid", url: "javascript:alert(1)" }] },
    prices: { NO1: [1, .1, .2, 1.1].map((price, i) => [start + i * 3600, price, start - 86400]) },
    rows: [
      { id: "old", userId: "1", site: "OSL_T", area: "NO1", start: start - 86400, end: start - 72000, startLocal: "2019-06-30 02:00:00", gridKwh: 6, powerKw: 3 },
      { id: "usable", userId: "1", site: "OSL_T", area: "NO1", start, end: start + 4 * 3600, startLocal: "2019-07-01 02:00:00", gridKwh: 6, powerKw: 3 },
    ],
  };
}
function documentFixture() {
  const html = fs.readFileSync(path.join(__dirname, "dist/norway-local.html"), "utf8");
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size, "No duplicate HTML IDs");
  class Node {
    constructor(tag = "div") { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.dataset = {}; this.value = ""; this.textContent = ""; this.hidden = false; this.disabled = false; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(key, callback) { this.listeners[key] = callback; }
  }
  const nodes = new Map(ids.map(id => [id, new Node()]));
  for (const match of html.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)) {
    if (/\bhidden\b/.test(match[0])) nodes.get(match[1]).hidden = true;
    if (/\bdisabled\b/.test(match[0])) nodes.get(match[1]).disabled = true;
  }
  return { nodes, body: new Node("body"), createElement: tag => new Node(tag), createElementNS: (_ns, tag) => new Node(tag), getElementById: id => { assert.ok(nodes.has(id), `Element ${id} exists in the real HTML`); return nodes.get(id); } };
}

test("filters preserve Norway source dates and use Oslo dates at UTC boundaries", () => {
  const row = { start: Date.parse("2019-06-30T22:30:00Z") / 1000, site: "OSL_T", area: "NO1" };
  assert.equal(Page.dateKey(row), "2019-07-01");
  assert.equal(Page.filterRows([row], { from: "2019-07-01", to: "2019-07-01", area: "NO1" }).length, 1);
  assert.equal(Page.filterRows([row], { from: "2025-07-01", to: "2025-07-01" }).length, 0);
  assert.equal(Page.filterRows([row], { from: "2019-07-01", to: "2019-07-01", area: "NO4" }).length, 0);
  assert.match(Page.validateFilters({ from: "2019-07-02", to: "2019-07-01" }), /before/);
});

test("comparison yields between chunks and retains totals without all 5-minute slots", async () => {
  const data = fixtureData(), market = Engine.createMarket(data);
  let yields = 0;
  const compared = await Page.compareHistory(Engine, market, data.rows, { chunkSize: 1, yieldFrame: async () => { yields++; } });
  assert.equal(yields, 2);
  assert.equal(compared.summary.total, 2);
  assert.equal(compared.summary.comparable, 1);
  assert.equal(compared.summary.skipped, 1);
  assert.equal(compared.summary.results.length, 31);
  assert.ok(compared.runs.every(run => run.tracks.every(track => !track.result || track.result.slots.length === 0)));
  assert.equal(compared.summary.results.find(row => row.id === "live").costNok, null);
  const full = Engine.compareSession(market, data.rows[1]);
  assert.ok(full.tracks.find(track => track.id === "day-ahead").result.slots.length > 0, "Selected replay can be recomputed with full slots");
  assert.ok(Page.bestResult(compared.summary).id !== "hindsight");
  assert.match(Engine.exportCsv(compared.runs), /NOK/);
});

test("a filter change cancels a batch without returning a partial cost comparison", async () => {
  const data = fixtureData(), market = Engine.createMarket(data); let cancelled = false;
  const result = await Page.compareHistory(Engine, market, data.rows, { chunkSize: 1, cancelled: () => cancelled, yieldFrame: async () => { cancelled = true; } });
  assert.equal(result, null);
});

test("chart keeps partial intervals inside the original session window and handles negative prices", () => {
  const row = { start: start + 60, end: start + 3600, powerKw: 3 };
  const result = { energyKwh: .2, costNok: -.02, slots: [{ time: start, availableStart: row.start, availableEnd: start + 300, energyKwh: .2, priceNokKwh: -.1, cumulativeKwh: .2, cumulativeCost: -.02 }] };
  const model = Page.chartModel(row, result, null, "cost");
  assert.ok(model.min < 0);
  assert.ok(model.lines[0].every(([time]) => time >= row.start * 1000 && time <= row.end * 1000));
  assert.equal(model.lines[0].at(-1)[1], -.02);
  assert.match(Page.timeLabel(Date.parse("2019-10-27T00:30:00Z") / 1000, true), /GMT\+2/);
  assert.match(Page.timeLabel(Date.parse("2019-10-27T01:30:00Z") / 1000, true), /GMT\+1/);
});

test("the page runs history in NOK, counts missing prices, and never renders live data as zero", async () => {
  const doc = documentFixture(), data = fixtureData();
  const page = Page.mount(doc, data, Engine, { yieldFrame: async () => {}, chunkSize: 1 });
  assert.equal(doc.nodes.get("nl-date-from").value, "2018-02-06");
  assert.equal(doc.nodes.get("nl-date-to").value, "2019-07-01");
  assert.match(doc.nodes.get("nl-source-line").textContent, /2 reported sessions/);
  assert.match(doc.nodes.get("nl-provenance-summary").textContent, /2019-07-01/);
  assert.match(doc.nodes.get("nl-area-note").textContent, /NO1: ASK, OSL_T; NO5: BER/);
  assert.match(doc.nodes.get("nl-publication-note").textContent, /13:00 Europe\/Oslo/);
  assert.match(doc.nodes.get("nl-attribution").textContent, /Normalized for SPARC/);
  assert.match(doc.nodes.get("nl-charging-attribution").textContent, /Cleaned for SPARC/);
  assert.equal(doc.nodes.get("nl-source-links").children.length, 1, "Unsafe source links are not used");
  assert.equal(doc.nodes.get("nl-site").children[0].textContent, "Oslo · OSL_T");
  await page.runHistory();
  assert.equal(doc.nodes.get("nl-results").hidden, false);
  assert.equal(doc.nodes.get("nl-strategy-rows").children.length, 31);
  assert.equal(doc.nodes.get("nl-comparable").textContent, "1");
  assert.match(doc.nodes.get("nl-baseline").textContent, /^NOK /);
  const live = doc.nodes.get("nl-strategy-rows").children.find(row => row.dataset.strategy === "live");
  assert.equal(live.children[1].textContent, "Unavailable");
  assert.equal(live.children[1].colSpan, 3);
  assert.match(doc.nodes.get("nl-exclusion-summary").textContent, /1 with missing/);
  assert.equal(doc.nodes.get("nl-replay").hidden, false);
  assert.equal(doc.nodes.get("nl-session-number").value, "2");
  assert.ok(doc.nodes.get("nl-energy-chart").children.length > 0);
  assert.match(doc.nodes.get("nl-session-note").textContent, /usable/);
  doc.nodes.get("nl-area").value = "NO4"; page.updateFilters();
  assert.equal(doc.nodes.get("nl-results").hidden, true, "Filter edits hide stale savings");
  assert.equal(doc.nodes.get("nl-download").disabled, true);
  assert.equal(doc.nodes.get("nl-run").disabled, true);
});

test("saved full-history totals display on load and replay available original history", () => {
  const doc = documentFixture(), data = fixtureData(), market = Engine.createMarket(data);
  data.historySummary = Engine.summarize(data.rows.map(row => Page.compactRun(Engine.compareSession(market, row))));
  data.generatedAt = "2026-11-04T12:00:00Z";
  Page.mount(doc, data, Engine);
  assert.equal(doc.nodes.get("nl-results").hidden, false);
  assert.equal(doc.nodes.get("nl-replay").hidden, false);
  assert.match(doc.nodes.get("nl-run-status").textContent, /saved full-history/);
  assert.equal(doc.nodes.get("nl-download").disabled, true, "Per-session CSV requires actual selected runs");
});

test("missing files produce an explicit loading error and no invented savings", () => {
  const doc = documentFixture(); Page.mount(doc, null, null);
  assert.equal(doc.nodes.get("nl-error").hidden, false);
  assert.match(doc.nodes.get("nl-error").textContent, /could not be loaded/);
  assert.equal(doc.nodes.get("nl-results").hidden, true);
  assert.equal(doc.nodes.get("nl-run").disabled, true);
});
