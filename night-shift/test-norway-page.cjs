"use strict";
const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const Page=require("./dist/norway-comparison.js");
const report=JSON.parse(fs.readFileSync(path.join(__dirname,"../results/charging-history/norway-strategies-2025.json"),"utf8"));

test("the website bundle matches the verified Norway report exactly",()=>{
  const context={window:{}};vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,"dist/norway-comparison-data.js"),"utf8"),context);
  assert.deepEqual(JSON.parse(JSON.stringify(context.window.SPARC_NORWAY_COMPARISON)),report);
});

test("the Norway page presents all 31 time-of-day strategies on identical completed sessions",()=>{
    const view=Page.view(report);
    assert.equal(view.main.length,8);
    assert.equal(view.timers.length,24);
    assert.equal(new Set([...view.main,...view.timers].map(row=>row.strategy)).size,31);
    assert.equal(view.winner.strategy,"live");
    assert.equal(view.bestTimer.strategy,"timer-0100");
    assert.ok(view.main.every(row=>row.commonSessions===33784));
    assert.ok([...view.main,...view.timers].every(row=>row.tariff==="time-of-day"));
    assert.ok(view.main.every(row=>Math.abs(row.deliveredKwh-report.commonEnergyKwh)<=report.commonSessions*1e-6));
    assert.equal(view.byId.get("hindsight").label,"Perfect hindsight (reference)");
});

test("the time-of-day comparison explains the rate change and keeps the standard baseline",()=>{
  const tod=Page.view(report);
  assert.match(tod.explanation,/delivery-rate change/);
  assert.equal(tod.winner.savingPct.toFixed(2),"33.97");
  assert.equal(tod.winner.baselineCostUsd,report.baselineCostUsd);
  assert.equal(Page.timerLabel("timer-0000"),"12 AM timer");
  assert.equal(Page.timerLabel("timer-1200"),"12 PM timer");
  assert.equal(Page.timerLabel("timer-2300"),"11 PM timer");
});

test("other datasets, years, incomplete results and mismatched energy are rejected",()=>{
  assert.throws(()=>Page.view({...report,dataSource:"TeslaMate"}),/Norway Dataset 1/);
  assert.throws(()=>Page.view({...report,priceYear:2026}),/Norway Dataset 1/);
  assert.throws(()=>Page.view({...report,results:report.results.slice(2)}),/incomplete/);
  const changed=structuredClone(report);changed.results[1].deliveredKwh=1;
  assert.throws(()=>Page.view(changed),/matching/);
});

test("downloads include only the 31 time-of-day configurations with precise costs",()=>{
  const csv=Page.csv(report),lines=csv.split("\r\n");
  assert.equal(lines.length,32);
  assert.ok(lines[0].includes('"completionPct"'));
  assert.ok(!lines.some(row=>row.includes('"standard"')));
  assert.ok(lines.some(row=>row.includes('"time-of-day"')));
  assert.ok(lines.some(row=>row.includes(String(report.results[1].totalCostUsd))));
});

function documentFixture(){
  const html=fs.readFileSync(path.join(__dirname,"dist/norway.html"),"utf8");
  const ids=Array.from(html.matchAll(/\bid="([^"]+)"/g),match=>match[1]);
  assert.equal(new Set(ids).size,ids.length,"Duplicate HTML IDs");
  class Node {
    constructor(){this.children=[];this.attributes={};this.listeners={};this.classList=new Set();this.dataset={};this.style={};this.textContent="";this.hidden=false;}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=nodes;}
    setAttribute(key,value){this.attributes[key]=value;}
    addEventListener(event,callback){this.listeners[event]=callback;}
  }
  const nodes=new Map(ids.map(id=>[id,new Node()]));
  nodes.get("norway-error").hidden=true;
  return {nodes,body:new Node(),createElement:()=>new Node(),getElementById:id=>{
    assert.ok(nodes.has(id),`Missing actual page element: ${id}`);return nodes.get(id);
  }};
}

test("the page renders time-of-day totals without comparison switches",()=>{
  const doc=documentFixture();Page.mount(doc,report);
  assert.equal(doc.nodes.get("norway-error").hidden,true);
  assert.equal(doc.nodes.get("norway-strategy-rows").children.length,8);
  assert.equal(doc.nodes.get("norway-timer-rows").children.length,24);
  assert.equal(doc.nodes.get("norway-best-percent").textContent,"33.97%");
  assert.equal(doc.nodes.get("norway-best-dollars").textContent,"$15,065.83");
  assert.equal(doc.nodes.has("norway-timing"),false);
  assert.equal(doc.nodes.has("norway-tod"),false);
  const live=doc.nodes.get("norway-strategy-rows").children.find(node=>node.dataset.strategy==="live");
  assert.equal(live.children[1].textContent,"$29,288.87");
  assert.equal(live.children[3].textContent,"33.97%");
});

test("missing Norway data hides results and provides a useful error",()=>{
  const doc=documentFixture();Page.mount(doc,null);
  assert.equal(doc.nodes.get("norway-error").hidden,false);
  assert.match(doc.nodes.get("norway-error").textContent,/Norway/);
  assert.equal(doc.nodes.get("norway-results").hidden,true);
  assert.equal(doc.nodes.get("norway-download").disabled,true);
});
