"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const Page = require("./dist/comed-costs.js");
const Engine = require("./dist/comed-cost-engine.js");
const context = {window: {}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "dist/comed-cost-data.js"), "utf8"), context);
const data = context.window.SPARC_COMED_COST_DATA;
const report = Engine.compare(data, {});

function documentFixture() {
  const html = fs.readFileSync(path.join(__dirname, "dist/comed-costs.html"), "utf8");
  class Node {
    constructor() {
      this.children = []; this.attributes = {}; this.listeners = {}; this.style = {};
      this.textContent = ""; this.value = ""; this.hidden = false; this.disabled = false; this.parentElement = null;
      this.classes = new Set();
      this.classList = {toggle: (name, force) => { if (force) this.classes.add(name); else this.classes.delete(name); }};
    }
    append(...nodes) { this.children.push(...nodes); nodes.forEach(node => {node.parentElement = this;}); }
    replaceChildren(...nodes) {this.children = []; this.append(...nodes);}
    setAttribute(key, value) {this.attributes[key] = value;}
    addEventListener(name, callback) {this.listeners[name] = callback;}
    checkValidity() {return true;}
  }
  const nodes = new Map();
  for (const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
    const id = match[1], tag = match[0], node = new Node();
    assert.ok(!nodes.has(id), `Duplicate HTML ID ${id}`);
    node.hidden = /\bhidden(?:\s|>)/.test(tag);
    node.value = /\bvalue="([^"]*)"/.exec(tag)?.[1] || "";
    node.parentElement = new Node();
    nodes.set(id, node);
  }
  return {nodes, body: new Node(), createElement: () => new Node(), getElementById: id => {
    assert.ok(nodes.has(id), `Missing actual HTML element ${id}`); return nodes.get(id);
  }};
}

test("the ComEd page renders the real engine report with all itemized costs and hashes", async () => {
  const doc = documentFixture(), view = Page.mount(doc, data, Engine);
  await view.ready;
  assert.equal(doc.nodes.get("cc-error").hidden, true);
  assert.equal(doc.nodes.get("cc-results").hidden, false);
  assert.equal(doc.nodes.get("cc-breakdown").children.length, 11);
  assert.equal(doc.nodes.get("cc-monthly").children.length, report.months.length);
  assert.equal(doc.nodes.get("cc-source-hashes").children.length, 4);
  assert.ok(doc.nodes.get("cc-source-hashes").children.some(row => row.textContent.includes(data.source.sha256[data.source.trace])));
  assert.equal(doc.nodes.get("cc-sparc-cost").textContent, new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(report.strategies[2].total));
  assert.match(doc.nodes.get("cc-timing-note").textContent, /volume charges only/);
  assert.match(doc.nodes.get("cc-account-summary").textContent, /Before allocation/);
  assert.equal(doc.nodes.get("cc-download").disabled, false);
});

test("timing savings use volume costs, with negative results labeled as additional cost", async () => {
  const changed = structuredClone(report);
  changed.strategies[0].total = 300;
  changed.strategies[1].total = 100;
  changed.strategies[2].total = 200;
  changed.strategies[1].variableTotal = 150;
  changed.strategies[2].variableTotal = 140;
  const doc = documentFixture(), view = Page.mount(doc, data, {compare: () => changed});
  await view.ready;
  assert.equal(doc.nodes.get("cc-timing-saving").textContent, "$10.00");
  assert.match(doc.nodes.get("cc-timing-note").textContent, /6\.7% lower/);
  changed.strategies[2].variableTotal = 170;
  await view.run();
  assert.equal(doc.nodes.get("cc-timing-saving").textContent, "$20.00");
  assert.match(doc.nodes.get("cc-timing-note").textContent, /13\.3% higher.*Additional cost/);
  assert.equal(doc.nodes.get("cc-timing-saving").parentElement.classes.has("is-negative"), true);
});

test("changing assumptions marks results stale and rerunning passes updated values", async () => {
  const doc = documentFixture(), view = Page.mount(doc, data, Engine);
  await view.ready;
  doc.nodes.get("cc-power").value = "7.2";
  doc.nodes.get("cc-home").value = "1000";
  doc.nodes.get("cc-capacity").value = "3.5";
  doc.nodes.get("cc-fixed-local-tax").value = "0.5";
  doc.nodes.get("cc-hourly-local-tax").value = "0.25";
  doc.nodes.get("cc-form").listeners.input();
  assert.equal(doc.nodes.get("cc-download").disabled, true);
  assert.match(doc.nodes.get("cc-status").textContent, /Assumptions changed/);
  await view.run();
  assert.equal(view.getReport().options.powerKw, 7.2);
  assert.equal(view.getReport().options.householdKwh, 1000);
  assert.equal(view.getReport().options.capacityKw, 3.5);
  assert.equal(view.getReport().options.fixedMunicipalCents, 0.5);
  assert.equal(view.getReport().options.hourlyMunicipalCents, 0.25);
  assert.ok(Math.abs(view.getReport().strategies[0].municipalTax - view.getReport().energyKwh * .5 / 100) < 1e-5);
  assert.ok(Math.abs(view.getReport().strategies[2].municipalTax - view.getReport().energyKwh * .25 / 100) < 1e-5);
  assert.ok(view.getReport().strategies[2].municipalTax > 0);
  assert.equal(doc.nodes.get("cc-download").disabled, false);
});

test("zero-comparison selections hide results and explain the missing cohort", async () => {
  const doc = documentFixture(), view = Page.mount(doc, data, Engine);
  await view.ready;
  doc.nodes.get("cc-power").value = "0.1";
  const day = data.rows.find(row => row[1] > 5)[0];
  doc.nodes.get("cc-from").value = day;
  doc.nodes.get("cc-to").value = day;
  await view.run();
  assert.equal(doc.nodes.get("cc-results").hidden, true);
  assert.equal(doc.nodes.get("cc-download").disabled, true);
  assert.equal(doc.nodes.get("cc-error").hidden, false);
  assert.match(doc.nodes.get("cc-error").textContent, /No days can deliver/);
  assert.ok(view.getReport().excludedDays > 0);
});

test("missing data and invalid ranges fail without showing new totals", async () => {
  const missing = documentFixture();
  Page.mount(missing, null, Engine);
  assert.equal(missing.nodes.get("cc-error").hidden, false);
  assert.equal(missing.nodes.get("cc-run").disabled, true);
  assert.equal(missing.nodes.get("cc-results").hidden, true);
  const doc = documentFixture(), view = Page.mount(doc, data, Engine);
  await view.ready;
  doc.nodes.get("cc-from").value = "2026-10-31";
  doc.nodes.get("cc-to").value = "2022-06-01";
  await view.run();
  assert.equal(doc.nodes.get("cc-error").hidden, false);
  assert.match(doc.nodes.get("cc-error").textContent, /From on or before Through/);
  assert.equal(doc.nodes.get("cc-results").hidden, true);
  assert.equal(doc.nodes.get("cc-download").disabled, true);
});

test("energy-mismatched results are rejected", () => {
  const changed = structuredClone(report);
  changed.strategies[2].energyKwh += 1;
  assert.throws(() => Page.validate(changed), /equal energy/);
});

const textOf = node => [node.textContent, ...node.children.map(textOf)].join(" ");
function descendants(node) {return [node, ...node.children.flatMap(descendants)];}

test("tax treatment shows the same sourced state rules and separate computed taxes for each plan", async () => {
  const doc = documentFixture(), view = Page.mount(doc, data, Engine);
  await view.ready;
  const rows = doc.nodes.get("cc-tax-treatment").children;
  assert.equal(rows.length, 4);
  const stateRules = rows.find(row => row.attributes["data-tax-row"] === "state-rule");
  for (const cell of stateRules.children.slice(1)) {
    const text = textOf(cell);
    assert.match(text, /Verified state rule/);
    assert.match(text, /0\.330¢\/kWh.*first 2,000/);
    assert.match(text, /0\.319¢\/kWh.*next 48,000/);
    assert.match(text, /monthly household kWh/);
    assert.ok(descendants(cell).some(node => node.attributes.href === report.taxTreatment.fixed.state.url));
  }
  assert.deepEqual(report.taxTreatment.fixed.state.tiers, report.taxTreatment.hourly.state.tiers);
  const stateAmounts = rows.find(row => row.attributes["data-tax-row"] === "state-amount");
  assert.match(textOf(stateAmounts.children[2]), /SPARC: \$.*Immediate: \$/);
  const breakdownText = doc.nodes.get("cc-breakdown").children.map(textOf).join(" ");
  assert.match(breakdownText, /Distribution facilities/);
  assert.match(breakdownText, /Illinois electricity distribution charge/);
  assert.match(breakdownText, /not the household electricity excise tax/);
});

test("blank local overrides use sourced Glenview tiers, with historical assumptions visible", async () => {
  const doc = documentFixture(), view = Page.mount(doc, data, Engine);
  await view.ready;
  assert.equal(view.getReport().options.fixedMunicipalCents, null);
  assert.equal(view.getReport().options.hourlyMunicipalCents, null);
  const rows = doc.nodes.get("cc-tax-treatment").children;
  const rules = rows.find(row => row.attributes["data-tax-row"] === "municipal-rule");
  for (const cell of rules.children.slice(1)) {
    const text = textOf(cell);
    assert.match(text, /0\.556¢\/kWh.*first 2,000/);
    assert.match(text, /0\.366¢\/kWh.*next 48,000/);
    assert.match(text, /historical carryforward/i);
    assert.ok(descendants(cell).some(node => node.attributes.href === report.taxTreatment.fixed.municipal.url));
  }
  assert.ok(view.getReport().strategies.every(strategy => strategy.municipalTax > 0));
});

test("a fixed-plan zero override is an explicit user scenario and leaves hourly Glenview tax in place", async () => {
  const doc = documentFixture(), view = Page.mount(doc, data, Engine);
  await view.ready;
  const defaultHourly = view.getReport().strategies[2].municipalTax;
  doc.nodes.get("cc-fixed-local-tax").value = "0";
  await view.run();
  const changed = view.getReport();
  assert.equal(changed.options.fixedMunicipalCents, 0);
  assert.equal(changed.options.hourlyMunicipalCents, null);
  assert.equal(changed.strategies[0].municipalTax, 0);
  assert.equal(changed.strategies[2].municipalTax, defaultHourly);
  const rules = doc.nodes.get("cc-tax-treatment").children.find(row => row.attributes["data-tax-row"] === "municipal-rule");
  assert.match(textOf(rules.children[1]), /User scenario override.*0\.000¢\/kWh/);
  assert.match(textOf(rules.children[2]), /0\.556¢\/kWh/);
  const csv = Engine.csv(changed);
  assert.match(csv, /municipal.*status/i);
  assert.match(csv, /user-assumption/);
});
